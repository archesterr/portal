# portal

Minimal self-service shop in front of a 3x-ui panel.

Customer: register (name, mobile, optional email, password) → choose amount in **KG** → upload payment receipt.
Admin: open receipt → **Approve** → first purchase creates the client on the panel, later purchases top up the same client. The customer sees their link, QR and remaining KG in their panel.

Persian/RTL UI. No protocol/product keywords. The only contact shown is your Telegram ID.

## Deploy

```bash
cp .env.example .env && vi .env      # SECRET_KEY, ADMIN_PASSWORD, SUPPORT_TELEGRAM, PRICE_PER_GB, INBOUND_IDS, XUI_*, SUB_BASE_URL
mkdir -p data && chown 10001:10001 data
docker compose up -d --build
```

- Customers: `https://<domain>/` → register / login → panel
- Admin: `https://<domain>/admin` → `ADMIN_PASSWORD`

Origin nginx behind Arvan CDN (Arvan → origin HTTPS :8880, Arvan-only allowlist): `deploy/nginx.conf` + `deploy/arvan-ips.sh`.
Backups: `deploy/backup.sh` (DB) — also back up `data/receipts/`.

## Admin

| Action | Effect |
|---|---|
| Approve order | No active service → `addClient` on every `INBOUND_IDS` with one shared `subId`. Active service → `updateClient` adds the KG to `totalGB` and re-enables the client. |
| Reject order | Order marked rejected with your note (customer sees it). |
| Revoke | `delClientByEmail` on the panel; next approved order creates a fresh service. |
| New password | Generates a temporary password for a customer who forgot theirs (shown once). |

Optional: `TG_BOT_TOKEN` + `TG_ADMIN_CHAT_ID` → Telegram alert on every new receipt.

## Notes

- 3x-ui counts traffic per inbound client; with several `INBOUND_IDS` a customer effectively gets N × KG. Use one inbound.
- `SERVICE_DAYS=0` → volume only, no expiry.
- Phone numbers are not verified; one account per number.
- Receipts are kept in `data/receipts/` (admin-only, random names, `no-store`).
