#!/bin/sh
set -eu
# El bloqueo permanece en poder de flock mientras PostgreSQL está ejecutándose.
exec flock -n /coord/primary.lock /bin/sh -c '
  if [ -f /coord/promoted ]; then
    echo "El respaldo ya fue promovido; no se inicia el antiguo primario." >&2
    exit 1
  fi
  exec docker-entrypoint.sh postgres \
    -c hba_file=/replication-pg_hba.conf \
    -c wal_level=replica \
    -c max_wal_senders=5 \
    -c wal_keep_size=256MB \
    -c synchronous_commit=local \
    -c synchronous_standby_names=standby_b
'
