from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import logging
import os
from typing import Annotated, Generator
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from psycopg2 import pool
import psycopg2

# Configuración de Logging estructurado
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [MODULO-B] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("modulo-b")

# Variables de entorno para PostgreSQL
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "negocio_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres123")
DB_MIN_CONN = int(os.getenv("DB_MIN_CONN", "1"))
DB_MAX_CONN = int(os.getenv("DB_MAX_CONN", "10"))

# Pool de conexiones multihilo
db_pool: pool.ThreadedConnectionPool | None = None


def init_db_pool() -> None:
    global db_pool
    logger.info("Inicializando pool de conexiones a PostgreSQL (%s:%d/%s)...", DB_HOST, DB_PORT, DB_NAME)
    try:
        db_pool = pool.ThreadedConnectionPool(
            minconn=DB_MIN_CONN,
            maxconn=DB_MAX_CONN,
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
        logger.info("Pool de conexiones a PostgreSQL establecido exitosamente.")
    except Exception as err:
        logger.error("No se pudo inicializar el pool de conexiones: %s", err)
        db_pool = None


def close_db_pool() -> None:
    global db_pool
    if db_pool:
        logger.info("Cerrando pool de conexiones a PostgreSQL...")
        db_pool.closeall()
        db_pool = None
        logger.info("Pool de conexiones cerrado.")


@contextmanager
def get_db_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    global db_pool
    if db_pool is None:
        init_db_pool()
        if db_pool is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="PostgreSQL no disponible (pool de conexiones inactivo).",
            )

    conn = None
    try:
        conn = db_pool.getconn()
        yield conn
    except psycopg2.Error as err:
        if conn:
            conn.rollback()
        logger.error("Error en operación con PostgreSQL: %s", err)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Error en PostgreSQL: {err.pgerror or str(err)}",
        ) from err
    finally:
        if conn and db_pool:
            db_pool.putconn(conn)


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db_pool()
    yield
    close_db_pool()


# Esquemas de datos con Pydantic
class TransaccionCreate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "cuenta_origen": "CLI-MARTIN-01",
                "cuenta_destino": "COMERCIO-SNEAKERSTORE",
                "monto": 89990.00,
                "moneda": "CLP",
                "descripcion": "Compra zapatillas: Nike Air Max 90 (Talla EU 41)",
            }
        },
    )

    cuenta_origen: Annotated[str, Field(min_length=1, max_length=50, description="Identificador de cuenta del comprador")]
    cuenta_destino: Annotated[str, Field(min_length=1, max_length=50, description="Identificador de cuenta del comercio")]
    monto: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2, description="Monto monetario de la operación")]
    moneda: Annotated[str, Field(min_length=1, max_length=10, description="Código de moneda (ej. CLP, USD)")]
    descripcion: Annotated[str | None, Field(default=None, max_length=255, description="Detalle del producto o servicio comprado")]


class TransaccionResponse(TransaccionCreate):
    id: UUID = Field(description="Identificador único UUID de la transacción")
    estado: str = Field(description="Estado de la transacción en el sistema", example="RECIBIDO")
    fecha: datetime = Field(description="Fecha y hora UTC de recepción")


# Instancia de FastAPI
app = FastAPI(
    title="Módulo B - Servicio de Dominio",
    version="1.1.0",
    description="Servicio transaccional de dominio (Python/FastAPI) encargado de procesar y persistir transacciones comerciales en PostgreSQL.",
    lifespan=lifespan,
)

router = APIRouter(tags=["Transacciones"])


@router.get(
    "/health",
    summary="Healthcheck del servicio",
    description="Verifica que el servicio y la conexión a la base de datos PostgreSQL estén activos y operativos.",
    tags=["Salud"],
)
def health_check() -> dict[str, str]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()
    return {"status": "ok", "servicio": "modulo-b", "db": "PostgreSQL conectado"}


@router.post(
    "/transacciones",
    response_model=TransaccionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar nueva transacción",
    description="Recibe una transacción desde el API Gateway (Módulo A) y la persiste en la base de datos relacional PostgreSQL con estado RECIBIDO.",
)
@router.post(
    "/api/transacciones",
    response_model=TransaccionResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_transaction(transaction: TransaccionCreate) -> TransaccionResponse:
    tx_id = uuid4()
    created_at = datetime.now(timezone.utc)

    logger.info(
        "Recibida orden: %s -> %s | Monto: %s %s | Ref: '%s'",
        transaction.cuenta_origen,
        transaction.cuenta_destino,
        transaction.monto,
        transaction.moneda,
        transaction.descripcion or "Sin detalle",
    )

    query = """
        INSERT INTO transacciones (
            id, cuenta_origen, cuenta_destino, monto, moneda,
            descripcion, estado, fecha
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, cuenta_origen, cuenta_destino, monto, moneda,
                  descripcion, estado, fecha;
    """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (
                    str(tx_id),
                    transaction.cuenta_origen,
                    transaction.cuenta_destino,
                    transaction.monto,
                    transaction.moneda,
                    transaction.descripcion,
                    "RECIBIDO",
                    created_at,
                ),
            )
            row = cursor.fetchone()
            conn.commit()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PostgreSQL no devolvió la transacción creada.",
        )

    logger.info("Transacción persistida con éxito en PostgreSQL. ID: %s (Estado: %s)", row[0], row[6])

    return TransaccionResponse(
        id=row[0],
        cuenta_origen=row[1],
        cuenta_destino=row[2],
        monto=row[3],
        moneda=row[4],
        descripcion=row[5],
        estado=row[6],
        fecha=row[7],
    )


@router.get(
    "/transacciones",
    response_model=list[TransaccionResponse],
    summary="Listar transacciones registradas",
    description="Devuelve las últimas transacciones persistidas en PostgreSQL ordenadas por fecha descendente.",
)
def list_transactions(
    limit: Annotated[int, Query(ge=1, le=100, description="Cantidad máxima de transacciones a retornar")] = 20,
) -> list[TransaccionResponse]:
    query = """
        SELECT id, cuenta_origen, cuenta_destino, monto, moneda,
               descripcion, estado, fecha
        FROM transacciones
        ORDER BY fecha DESC
        LIMIT %s;
    """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (limit,))
            rows = cursor.fetchall()

    return [
        TransaccionResponse(
            id=row[0],
            cuenta_origen=row[1],
            cuenta_destino=row[2],
            monto=row[3],
            moneda=row[4],
            descripcion=row[5],
            estado=row[6],
            fecha=row[7],
        )
        for row in rows
    ]


@router.get(
    "/transacciones/{transaccion_id}",
    response_model=TransaccionResponse,
    summary="Consultar transacción por ID",
    description="Permite verificar el estado y los datos de una transacción específica a partir de su identificador UUID.",
)
def get_transaction(
    transaccion_id: Annotated[UUID, Path(description="UUID de la transacción a consultar")],
) -> TransaccionResponse:
    query = """
        SELECT id, cuenta_origen, cuenta_destino, monto, moneda,
               descripcion, estado, fecha
        FROM transacciones
        WHERE id = %s;
    """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (str(transaccion_id),))
            row = cursor.fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Transacción '{transaccion_id}' no encontrada.",
        )

    return TransaccionResponse(
        id=row[0],
        cuenta_origen=row[1],
        cuenta_destino=row[2],
        monto=row[3],
        moneda=row[4],
        descripcion=row[5],
        estado=row[6],
        fecha=row[7],
    )


app.include_router(router)
