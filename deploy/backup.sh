#!/usr/bin/env bash
# Hourly: 0 * * * * /opt/portal/deploy/backup.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
sqlite3 data/shop.db ".backup backups/shop-$(date +%F-%H).db"
find backups -name 'shop-*.db' -mtime +7 -delete
