# Modulo B: Servicio de Dominio

Servicio FastAPI que recibe transacciones y las persiste en PostgreSQL.

## Endpoints

- `GET /health`: verifica la conexión con PostgreSQL.
- `POST /transacciones`: crea una transacción.
- `POST /api/transacciones`: alias compatible con el gateway.

Ejemplo de solicitud:

```json
{
  "cuenta_origen": "ACC-10023",
  "cuenta_destino": "ACC-90412",
  "monto": 15000.50,
  "moneda": "CLP",
  "descripcion": "Pago de servicios"
}
```

La respuesta incluye un UUID, la fecha de recepción y el estado `RECIBIDO`.

## Variables de conexión

El contenedor usa estas variables, con los valores por defecto de `docker-compose.yml`:

| Variable | Valor por defecto |
| --- | --- |
| `DB_HOST` | `localhost` |
| `DB_PORT` | `5432` |
| `DB_NAME` | `negocio_db` |
| `DB_USER` | `postgres` |
| `DB_PASSWORD` | `postgres123` |

Para ejecutar el módulo junto con PostgreSQL desde la raíz:

```bash
docker compose up --build bd-relacional modulo-b
```

El módulo B queda disponible dentro de la red interna en `http://modulo-b:5000`.
