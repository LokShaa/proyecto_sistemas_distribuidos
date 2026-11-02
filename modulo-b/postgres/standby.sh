#!/bin/sh
set -eu
export PGPASSWORD="$POSTGRES_PASSWORD"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
  echo "Creando réplica inicial desde bd-relacional..."
  until pg_basebackup -h bd-relacional -U postgres -D "$PGDATA" -R -X stream \
    -P -w; do
    echo "No se pudo crear la réplica; se reintentará." >&2
    sleep 5
  done
  echo "primary_conninfo = 'host=bd-relacional port=5432 user=postgres password=$POSTGRES_PASSWORD application_name=standby_b'" >> "$PGDATA/postgresql.auto.conf"
  touch "$PGDATA/.replica_initialized"
elif [ ! -f "$PGDATA/.replica_initialized" ]; then
  echo "La inicialización de la réplica quedó incompleta; se requiere revisión manual." >&2
  exit 1
fi
exec docker-entrypoint.sh postgres
