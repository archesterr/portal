#!/usr/bin/env bash
# Build /etc/nginx/arvan-ips.conf (allow + set_real_ip_from per Arvan edge range).
# Cron weekly: 0 4 * * 1 /opt/portal/deploy/arvan-ips.sh
set -euo pipefail
OUT=/etc/nginx/arvan-ips.conf
TMP=$(mktemp)
curl -fsS https://www.arvancloud.ir/en/ips.txt \
  | tr -d '\r' | grep -E '^[0-9a-fA-F:.]+/[0-9]+$' \
  | awk '{print "allow " $1 ";\nset_real_ip_from " $1 ";"}' > "$TMP"
[ -s "$TMP" ] || { echo "empty IP list, keeping old $OUT" >&2; rm -f "$TMP"; exit 1; }
install -m 644 "$TMP" "$OUT" && rm -f "$TMP"
nginx -t -q && systemctl reload nginx
echo "$(grep -c '^allow' "$OUT") Arvan ranges loaded"
