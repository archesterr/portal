# portal

Customer self-service portal for a 3x-ui panel.
Phone OTP login → identity check → pick a plan → admin approves → client is created on the panel → customer gets subscription link + QR + live usage.
Customer UI is Persian/RTL and contains no protocol or product keywords.

## Deploy

```bash
cp .env.example .env && vi .env           # SECRET_KEY, ADMIN_PHONES, XUI_*, SUB_BASE_URL
vi plans.json                             # inbound IDs from the panel, GB, days, price
mkdir -p data && chown 10001:10001 data
docker compose up -d --build
docker compose logs -f                    # SMS_PROVIDER=console prints OTPs here
```

Origin nginx behind Arvan CDN (TLS on Arvan, origin :8880, Arvan-only allowlist): `deploy/nginx.conf` + `deploy/arvan-ips.sh`. Backups: `deploy/backup.sh` in cron.

Log in with a number listed in `ADMIN_PHONES` → lands on `/admin`.

## Flow

| Customer | Admin (`/admin`) |
|---|---|
| Enter mobile → 6-digit SMS code | — |
| Name + national code (checksum-validated, unique per account) + ID card photo + selfie | Sees photos, approves/rejects with a note. Photos are deleted right after the decision (`KYC_PURGE_AFTER_REVIEW`). |
| Picks a plan → request | Approves → portal calls 3x-ui `addClient` on every inbound in the plan with a shared `subId`. Revoke = `delClientByEmail`. |
| Dashboard: link, QR, used/total GB, days left | Telegram alert on new KYC / request (optional) |

`AUTO_APPROVE_REQUESTS=true` skips the second admin step (KYC is still manual).

## plans.json

```json
{"id": "m1-50", "title": "یک ماهه — ۵۰ گیگ", "gb": 50, "days": 30, "ip_limit": 2, "inbounds": [1], "price": "۱۵۰ هزار تومان"}
```

- `inbounds`: panel inbound IDs. Multiple IDs share one subscription link.
- 3x-ui counts traffic **per inbound client**, so a plan with 3 inbounds effectively gives 3 × `gb`. Divide `gb` yourself if that matters.
- `START_ON_FIRST_USE=true` → negative `expiryTime`, days start on first connection.

## Panel side

- Enable the subscription service in 3x-ui; `SUB_BASE_URL` = its URL + path (e.g. `https://sub.example.com:2096/sub`).
- Use a dedicated panel user. If 2FA is on, set `XUI_TOTP_SECRET`.
- Run the portal on a host that can reach the panel API; keep the panel port firewalled to that host.

## SMS

`SMS_PROVIDER=kavenegar` uses the Verify Lookup API — create a template with a `%token%` placeholder. Other providers: edit `app/sms.py` (one function).

## Shahkar (phone ↔ national-code ownership)

`app/kyc.py:shahkar_check()` is a stub returning `None` (manual review only). Return `True/False` from your provider (Finnotech / Jibit / Zibal) to auto-reject mismatches.

## Security notes

- Sessions: signed cookie, `HttpOnly`, `Secure`, `SameSite=Lax`. OTP: HMAC-hashed, 2-min TTL, 60 s resend cooldown, 5 attempts, 5 sends / 10 min per IP.
- Uploads: magic-byte checked, 5 MB cap, random names, outside the web root, served only to admins with `no-store`.
- Strict CSP (no inline JS/CSS), `noindex`, no server header, no API docs.
- **ID photos are the most sensitive data you hold.** Keep `KYC_PURGE_AFTER_REVIEW=true`, keep `data/` off shared backups, and disk-encrypt the host.

## Layout

```
app/main.py      routes, auth, KYC, requests, admin
app/panel.py     3x-ui API client
app/kyc.py       phone/national-code validation, image sniffing, Shahkar hook
app/sms.py       OTP delivery
app/notify.py    Telegram admin alerts
app/templates/   Persian RTL Jinja templates
```
