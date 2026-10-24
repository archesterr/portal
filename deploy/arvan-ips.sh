#!/usr/bin/env bash
# Build Arvan edge range files for nginx:
#   /etc/nginx/arvan-geo.conf     "<cidr> 1;"          (origin allowlist, checked on the real peer IP)
#   /etc/nginx/arvan-realip.conf  "set_real_ip_from"   (trust ar-real-ip only from Arvan)
# Cron weekly: 0 4 * * 1 /opt/portal/deploy/arvan-ips.sh
set -euo pipefail
GEO=/etc/nginx/arvan-geo.conf
REALIP=/etc/nginx/arvan-realip.conf
command -v nginx >/dev/null || { echo "nginx not installed" >&2; exit 1; }
LIST=$(curl -fsS https://www.arvancloud.ir/en/ips.txt | tr -d '\r' | grep -E '^[0-9a-fA-F:.]+/[0-9]+$' || true)
[ -n "$LIST" ] || { echo "empty IP list, keeping existing files" >&2; exit 1; }
awk '{print $1 " 1;"}' <<<"$LIST" > "$GEO.tmp"
awk '{print "set_real_ip_from " $1 ";"}' <<<"$LIST" > "$REALIP.tmp"
mv "$GEO.tmp" "$GEO"
mv "$REALIP.tmp" "$REALIP"
rm -f /etc/nginx/arvan-ips.conf
nginx -t -q
systemctl reload nginx
echo "$(wc -l < "$GEO") Arvan ranges loaded"
