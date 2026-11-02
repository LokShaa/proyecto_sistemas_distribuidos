#!/bin/sh
set -eu

export PGPASSWORD="$POSTGRES_PASSWORD"
# La marca de promoción nunca se borra: la instancia original solo puede volver
# como réplica. Se conserva su directorio anterior dentro del mismo volumen.
work="$PGDATA/.rejoin"
mkdir -p "$work"
chmod 700 "$work"

if [ ! -f "$PGDATA/.rejoined" ]; then
  if [ ! -f "$work/ready" ]; then
    echo "Esperando al nuevo principal bd-relacional-backup..."
    until [ "$(psql -h bd-relacional-backup -U postgres -d postgres -tAc 'SELECT pg_is_in_recovery()' 2>/dev/null || true)" = "f" ]; do
      sleep 3
    done
    # Un intento interrumpido se conserva para revisión, nunca se mezcla con
    # una copia nueva ni se elimina la única copia de los datos anteriores.
    if [ -d "$work/new" ]; then
      mv "$work/new" "$work/incomplete-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    fi
    echo "Sincronizando la instancia original desde el nuevo principal..."
    PGOPTIONS='-c synchronous_commit=local' pg_basebackup \
      -h bd-relacional-backup -U postgres -D "$work/new" \
      -R -X stream --checkpoint=fast -w
    pg_verifybackup "$work/new"
    printf "\nprimary_conninfo = 'host=bd-relacional-backup port=5432 user=postgres password=%s application_name=original_b'\n" "$POSTGRES_PASSWORD" >> "$work/new/postgresql.auto.conf"
    touch "$work/ready"
  fi

  if [ ! -f "$work/archived" ]; then
    mkdir -p "$work/previous-primary"
    for item in "$PGDATA"/* "$PGDATA"/.[!.]* "$PGDATA"/..?*; do
      [ -e "$item" ] || [ -L "$item" ] || continue
      [ "$item" = "$work" ] && continue
      mv "$item" "$work/previous-primary/"
    done
    touch "$work/archived"
  fi
  # Si el contenedor se interrumpe durante estos movimientos, el siguiente
  # arranque instala solo los archivos pendientes de la copia ya verificada.
  for item in "$work/new"/* "$work/new"/.[!.]* "$work/new"/..?*; do
    [ -e "$item" ] || [ -L "$item" ] || continue
    mv "$item" "$PGDATA/"
  done
  touch "$PGDATA/.rejoined"
fi

touch "$PGDATA/standby.signal"
echo "La instancia original se inicia como réplica de bd-relacional-backup."
exec docker-entrypoint.sh postgres \
  -c hba_file=/replication-pg_hba.conf \
  -c wal_level=replica -c max_wal_senders=5 -c wal_keep_size=256MB \
  -c synchronous_standby_names='' -c wal_receiver_status_interval=1s
