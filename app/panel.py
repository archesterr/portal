"""Thin client for the 3x-ui panel API."""
import json
import logging
import os
import time

import httpx
import pyotp

log = logging.getLogger("panel")


class PanelError(Exception):
    pass


class Panel:
    def __init__(self):
        self.base = os.getenv("XUI_URL", "").rstrip("/")          # include webBasePath
        self.user = os.getenv("XUI_USERNAME", "")
        self.pw = os.getenv("XUI_PASSWORD", "")
        self.totp = os.getenv("XUI_TOTP_SECRET") or None
        self.verify = os.getenv("XUI_VERIFY_TLS", "true").lower() == "true"

    def _session(self) -> httpx.Client:
        c = httpx.Client(base_url=self.base, verify=self.verify, timeout=15)
        data = {"username": self.user, "password": self.pw}
        if self.totp:
            data["twoFactorCode"] = pyotp.TOTP(self.totp).now()
        try:
            r = c.post("/login", data=data)
            ok = r.status_code == 200 and r.json().get("success")
        except Exception as e:
            c.close()
            raise PanelError(f"panel unreachable: {e}") from e
        if not ok:
            c.close()
            raise PanelError(f"panel login failed ({r.status_code})")
        return c

    @staticmethod
    def _ok(r: httpx.Response):
        try:
            j = r.json()
        except ValueError:
            raise PanelError(f"bad panel response ({r.status_code})")
        if not j.get("success"):
            raise PanelError(j.get("msg") or "panel error")
        return j.get("obj")

    def add_clients(self, inbounds, sub_id, cid, email_prefix, gb, days, ip_limit, start_on_first_use):
        """Add one client per inbound, all sharing sub_id -> one subscription link."""
        if not days:
            exp = 0  # volume only, no time limit
        elif start_on_first_use:
            exp = -days * 86_400_000
        else:
            exp = int((time.time() + days * 86400) * 1000)
        added = []
        with self._session() as c:
            try:
                for inb in inbounds:
                    email = f"{email_prefix}-{inb}"
                    client = {
                        "id": cid, "password": cid, "auth": cid, "flow": "",
                        "email": email, "limitIp": int(ip_limit),
                        "totalGB": int(gb * 1024 ** 3), "expiryTime": exp,
                        "enable": True, "tgId": "", "subId": sub_id,
                        "comment": "", "reset": 0,
                    }
                    self._ok(c.post("/panel/api/inbounds/addClient",
                                    data={"id": str(inb), "settings": json.dumps({"clients": [client]})}))
                    added.append([inb, email])
            except Exception:
                for inb, email in added:  # roll back partial provisioning
                    try:
                        c.post(f"/panel/api/inbounds/{inb}/delClientByEmail/{email}")
                    except Exception:
                        pass
                raise
        return added

    def topup(self, clients, add_gb):
        """Raise totalGB of existing clients by add_gb and re-enable them."""
        with self._session() as c:
            for inb, email in clients:
                ib = self._ok(c.get(f"/panel/api/inbounds/get/{inb}")) or {}
                settings = json.loads(ib.get("settings") or "{}")
                cl = next((x for x in settings.get("clients", []) if x.get("email") == email), None)
                if cl is None:
                    raise PanelError(f"client {email} not found on inbound {inb}")
                cl["totalGB"] = int(cl.get("totalGB", 0)) + int(add_gb * 1024 ** 3)
                cl["enable"] = True
                key = cl.get("id") or cl.get("password") or cl.get("auth") or email
                self._ok(c.post(f"/panel/api/inbounds/updateClient/{key}",
                                data={"id": str(inb), "settings": json.dumps({"clients": [cl]})}))

    def remove(self, clients):
        with self._session() as c:
            for inb, email in clients:
                try:
                    self._ok(c.post(f"/panel/api/inbounds/{inb}/delClientByEmail/{email}"))
                except PanelError as e:
                    log.warning("remove %s/%s: %s", inb, email, e)

    def usage(self, clients):
        used, total, exp = 0, 0, 0
        with self._session() as c:
            for _, email in clients:
                o = self._ok(c.get(f"/panel/api/inbounds/getClientTraffics/{email}")) or {}
                used += o.get("up", 0) + o.get("down", 0)
                total = max(total, o.get("total", 0))
                exp = o.get("expiryTime", exp)
        return {"used": used, "total": total, "expiry": exp}
