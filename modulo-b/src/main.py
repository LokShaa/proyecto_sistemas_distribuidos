from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import logging
import os
import fcntl
import threading
from time import monotonic, sleep
from typing import Annotated, Generator, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, FastAPI, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
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
DB_BACKUP_HOST = os.getenv("DB_BACKUP_HOST", "bd-relacional-backup")
DB_COORD_DIR = os.getenv("DB_COORD_DIR", "/coord")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "negocio_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres123")
REPLICATION_WAIT_SECONDS = float(os.getenv("REPLICATION_WAIT_SECONDS", "2"))
if not 0 <= REPLICATION_WAIT_SECONDS <= 10:
    raise ValueError("REPLICATION_WAIT_SECONDS debe estar entre 0 y 10.")

_failover_lock = threading.Lock()
_fence_file = None


def connect(host: str) -> psycopg2.extensions.connection:
    conn = psycopg2.connect(
        host=host, port=DB_PORT, dbname=DB_NAME, user=DB_USER,
        password=DB_PASSWORD, connect_timeout=3,
        options="-c synchronous_commit=local -c statement_timeout=5000 -c lock_timeout=3000",
        keepalives_idle=3, keepalives_interval=1, keepalives_count=3,
        tcp_user_timeout=5000,
    )
    return conn


def replication_confirmed(conn: psycopg2.extensions.connection) -> bool:
    """Espera acotada tras COMMIT local, sin bloquear por una réplica apagada.

    Una respuesta pendiente conserva la compra en el servidor activo. PostgreSQL
    enviará el WAL pendiente cuando la réplica vuelva a conectarse.
    """
    deadline = monotonic() + REPLICATION_WAIT_SECONDS
    try:
        conn.autocommit = True  # Cada consulta observa el progreso actual del WAL.
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_current_wal_flush_lsn()::text")
            target_lsn = cursor.fetchone()[0]
            while True:
                cursor.execute(
                    """SELECT replay_lsn >= %s::pg_lsn
                       FROM pg_stat_replication
                       WHERE application_name IN ('standby_b', 'original_b')
                         AND state = 'streaming'""",
                    (target_lsn,),
                )
                replicas = cursor.fetchall()
                if any(row[0] is True for row in replicas):
                    return True
                if not replicas or monotonic() >= deadline:
                    return False
                sleep(min(0.05, max(0, deadline - monotonic())))
    except psycopg2.Error:
        # El COMMIT ya terminó: un fallo de verificación no rechaza la compra
        # ni debe provocar que el cliente la repita y genere otra transacción.
        logger.warning("Compra guardada; no se pudo confirmar su replicación.", exc_info=True)
        return False


def active_connection() -> psycopg2.extensions.connection:
    """Promueve la réplica solo si el proceso primario liberó el bloqueo local."""
    global _fence_file
    with _failover_lock:
        marker = os.path.join(DB_COORD_DIR, "promoted")
        if os.path.exists(marker):
            conn = connect(DB_BACKUP_HOST)
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_is_in_recovery()")
                if cursor.fetchone()[0]:
                    cursor.execute("SELECT pg_promote(wait => true, wait_seconds => 30)")
                    if not cursor.fetchone()[0]:
                        conn.close()
                        raise psycopg2.OperationalError("No se pudo completar la promoción de la réplica.")
            conn.autocommit = False
            return conn
        try:
            return connect(DB_HOST)
        except psycopg2.OperationalError:
            logger.warning("No se pudo conectar al primario; comprobando su bloqueo.")

        fence = open(os.path.join(DB_COORD_DIR, "primary.lock"), "a+")
        try:
            fcntl.flock(fence, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fence.close()
            raise psycopg2.OperationalError("El primario aún conserva el bloqueo; no se promueve la réplica.")

        try:
            conn = connect(DB_BACKUP_HOST)
            conn.autocommit = True
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_is_in_recovery()")
                if cursor.fetchone()[0]:
                    # Marcar antes de promover impide que el primario vuelva a arrancar.
                    with open(marker, "w", encoding="utf-8") as file:
                        file.write("standby promoted\n")
                        file.flush()
                        os.fsync(file.fileno())
                    cursor.execute("SELECT pg_promote(wait => true, wait_seconds => 30)")
                    if not cursor.fetchone()[0]:
                        raise psycopg2.OperationalError("No se pudo promover la réplica.")
            conn.autocommit = False
            _fence_file = fence
            logger.warning("Réplica promovida a primario; el primario anterior queda bloqueado.")
            return conn
        except Exception:
            fence.close()
            raise


@contextmanager
def get_db_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    conn = None
    try:
        conn = active_connection()
        yield conn
    except psycopg2.Error as err:
        if conn and not conn.closed:
            try:
                conn.rollback()
            except psycopg2.Error:
                logger.warning("No fue posible revertir la conexión interrumpida.")
        logger.error("Error en operación con PostgreSQL: %s", err)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Error en PostgreSQL: {err.pgerror or str(err)}",
        ) from err
    finally:
        if conn:
            conn.close()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    if _fence_file:
        _fence_file.close()


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


class TransaccionRegistradaResponse(TransaccionResponse):
    replicacion: Literal["confirmada", "pendiente"] = Field(
        description="Confirmada en la réplica o pendiente de sincronización; la compra ya está guardada en el servidor activo."
    )


# Instancia de FastAPI
app = FastAPI(
    title="Módulo B - Servicio de Dominio",
    version="1.1.0",
    description="Servicio transaccional de dominio (Python/FastAPI) encargado de procesar y persistir transacciones comerciales en PostgreSQL.",
    lifespan=lifespan,
)

router = APIRouter(tags=["Transacciones"])

def obtener_estado_bd(host: str) -> dict[str, object]:
    """Consulta directamente un contenedor PostgreSQL (primario o backup) para ver qué transacciones tiene."""
    try:
        with connect(host) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT pg_is_in_recovery();")
                en_recuperacion = bool(cursor.fetchone()[0])
                cursor.execute(
                    """
                    SELECT id, cuenta_origen, cuenta_destino, monto, moneda,
                           descripcion, estado, fecha
                    FROM transacciones
                    ORDER BY fecha DESC
                    LIMIT 20;
                    """
                )
                rows = cursor.fetchall()
        return {
            "host": host,
            "online": True,
            "rol": "RESPALDO (Standby)" if en_recuperacion else "PRIMARIA (Activa)",
            "cantidad": len(rows),
            "transacciones": [
                {
                    "id": str(r[0]),
                    "cuenta_origen": r[1],
                    "cuenta_destino": r[2],
                    "monto": float(r[3]),
                    "moneda": r[4],
                    "descripcion": r[5],
                    "estado": r[6],
                    "fecha": r[7].isoformat() if r[7] else None,
                }
                for r in rows
            ],
        }
    except Exception as err:
        return {
            "host": host,
            "online": False,
            "rol": "CAÍDA / SIN CONEXIÓN",
            "error": str(err),
            "cantidad": 0,
            "transacciones": [],
        }


@router.get(
    "/health",
    summary="Healthcheck del servicio",
    description="Verifica que el servicio y la conexión a la base de datos PostgreSQL estén activos y operativos.",
    tags=["Salud"],
)
def health_check() -> dict[str, str]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT EXISTS (SELECT 1 FROM pg_stat_replication WHERE state = 'streaming');")
            replica_connected = cursor.fetchone()[0]
        host = conn.info.host
    return {
        "status": "ok", "servicio": "modulo-b", "db": "PostgreSQL conectado",
        "servidor_activo": host,
        "modo": "normal" if replica_connected else "degradado",
    }


@router.get(
    "/transacciones-bds",
    summary="Comparar contenido de BD 1 (Normal) y BD Backup",
    description="Devuelve las transacciones almacenadas en bd-relacional y en bd-relacional-backup para verificar su sincronización.",
)
def get_databases_comparison() -> dict[str, object]:
    bd_1 = obtener_estado_bd(DB_HOST)
    bd_backup = obtener_estado_bd(DB_BACKUP_HOST)
    ids_1 = [tx["id"] for tx in bd_1["transacciones"]]
    ids_backup = [tx["id"] for tx in bd_backup["transacciones"]]
    set_backup = set(ids_backup)
    ids_en_ambas = [tx_id for tx_id in ids_1 if tx_id in set_backup]
    sincronizadas = bool(bd_1["online"] and bd_backup["online"] and set(ids_1) == set_backup)
    return {
        "sincronizadas": sincronizadas,
        "ids_en_ambas": ids_en_ambas,
        "bd_1": bd_1,
        "bd_backup": bd_backup,
    }


@router.post(
    "/transacciones",
    response_model=TransaccionRegistradaResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Registrar nueva transacción",
    description="Recibe una transacción desde el API Gateway (Módulo A) y la persiste en la base de datos relacional PostgreSQL con estado RECIBIDO.",
)
@router.post(
    "/api/transacciones",
    response_model=TransaccionRegistradaResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_transaction(transaction: TransaccionCreate) -> TransaccionRegistradaResponse:
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
        replicated = replication_confirmed(conn)

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PostgreSQL no devolvió la transacción creada.",
        )

    logger.info("Transacción persistida con éxito en PostgreSQL. ID: %s (Estado: %s)", row[0], row[6])

    if not replicated:
        logger.warning("Transacción %s guardada en modo degradado; replicación pendiente.", tx_id)

    return TransaccionRegistradaResponse(
        id=row[0],
        cuenta_origen=row[1],
        cuenta_destino=row[2],
        monto=row[3],
        moneda=row[4],
        descripcion=row[5],
        estado=row[6],
        fecha=row[7],
        replicacion="confirmada" if replicated else "pendiente",
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
