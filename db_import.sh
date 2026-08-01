#!/usr/bin/env bash
set -e

DB=treeline
USER=kvik
DUMP_USER=amos     # role the dump was taken under; it owns everything until reassigned
SQL=treeline.sql

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Fetching $SQL from amos..."
scp amos:~/treeline/$SQL ./$SQL

echo "Restarting PostgreSQL..."
sudo systemctl restart postgresql

echo "Recreating database '$DB'..."
sudo -u postgres dropdb --if-exists "$DB"
sudo -u postgres createdb -O "$USER" "$DB"

echo "Enabling postgis extension in database '$DB'..."
sudo -u postgres psql -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS postgis;"

echo "Importing $SQL..."
# Restored as postgres, so the dump's own OWNER TO statements apply and everything lands
# on $DUMP_USER — reassigned to $USER below.
sudo -u postgres pg_restore -d "$DB" ./$SQL

echo "Transferring ownership from '$DUMP_USER' to '$USER'..."
# Database-scoped, so it only touches this DB. PostGIS's own tables are owned by postgres
# and are left alone.
sudo -u postgres psql -d "$DB" -v ON_ERROR_STOP=1 \
    -c "REASSIGN OWNED BY $DUMP_USER TO $USER"

echo "Done"
