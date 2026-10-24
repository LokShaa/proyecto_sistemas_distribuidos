import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4
import json
import urllib.request

import psycopg2
from fastapi import APIRouter, FastAPI, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field


DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "negocio_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres123")


class TransaccionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cuenta_origen: Annotated[str, Field(min_length=1, max_length=50)]
    cuenta_destino: Annotated[str, Field(min_length=1, max_length=50)]
    monto: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    moneda: Annotated[str, Field(min_length=1, max_length=10)]
    descripcion: Annotated[str | None, Field(default=None, max_length=255)]


class TransaccionResponse(TransaccionCreate):
    id: UUID
    estado: str
    fecha: datetime


def connect_to_database():
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )


def save_transaction(transaction: TransaccionCreate) -> TransaccionResponse:
    transaction_id = uuid4()
    created_at = datetime.now(timezone.utc)
    query = """
        INSERT INTO transacciones (
            id, cuenta_origen, cuenta_destino, monto, moneda,
            descripcion, estado, fecha
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, cuenta_origen, cuenta_destino, monto, moneda,
                  descripcion, estado, fecha
    """

    try:
        with connect_to_database() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    query,
                    (
                        str(transaction_id),
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
    except psycopg2.Error as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No fue posible guardar la transaccion en PostgreSQL.",
        ) from error

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="PostgreSQL no devolvio la transaccion creada.",
        )
    auditoria_payload = {
        "TransaccionID": str(row[0]),
        "CuentaOrigen": row[1],
        "CuentaDestino": row[2],
        "Monto": float(row[3]),
        "Moneda": row[4],
        "Descripcion": row[5] if row[5] else ""
    }
    
    try:
        req = urllib.request.Request(
            "http://modulo-c:6000/auditoria",
            data=json.dumps(auditoria_payload).encode('utf-8'),
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"Error interno: No se pudo auditar en Módulo C -> {e}")
        
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


app = FastAPI(
    title="Modulo B - Servicio de Dominio",
    version="1.0.0",
    description="Persiste transacciones recibidas por el gateway en PostgreSQL.",
)
router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    try:
        with connect_to_database() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
    except psycopg2.Error as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL no esta disponible.",
        ) from error
    return {"status": "ok"}


@router.post(
    "/transacciones",
    response_model=TransaccionResponse,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/api/transacciones",
    response_model=TransaccionResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
def create_transaction(transaction: TransaccionCreate) -> TransaccionResponse:
    return save_transaction(transaction)


app.include_router(router)
