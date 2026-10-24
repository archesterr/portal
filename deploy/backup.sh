#!/usr/bin/env bash
# Hourly: 0 * * * * /opt/portal/deploy/backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
sqlite3 data/portal.db ".backup backups/portal-$(date +%F-%H).db"
find backups -name 'portal-*.db' -mtime +7 -delete
