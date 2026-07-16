#!/usr/bin/env bash
set -e

DB=treeline
USER=kvik
SQL=treeline.sql

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Fetching $SQL from amos..."
scp amos:~/$SQL ~/$SQL

echo "Restarting PostgreSQL..."
sudo systemctl restart postgresql

echo "Recreating database '$DB'..."
sudo -u postgres dropdb --if-exists "$DB"
sudo -u postgres createdb -O "$USER" "$DB"

echo "Enabling postgis extension in database '$DB'..."
sudo -u postgres psql -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS postgis;"

echo "Importing $SQL..."
psql -U "$USER" -d "$DB" -f ~/$SQL

echo "Done."
