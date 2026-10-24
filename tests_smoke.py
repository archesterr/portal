"""End-to-end smoke test against a fake panel.  pip install -r requirements.txt && python tests_smoke.py"""
import os
import re
import shutil
import sys

S = "/tmp/shop-smoke"
shutil.rmtree(S, ignore_errors=True)
os.makedirs(S)
os.environ.update(SECRET_KEY="x" * 32, ADMIN_PASSWORD="adminpw", SUB_BASE_URL="https://sub.t/sub",
                  DATA_DIR=S, DB_PATH=S + "/shop.db", COOKIE_SECURE="false", SUPPORT_TELEGRAM="@supp")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi.testclient import TestClient  # noqa: E402

from app import main  # noqa: E402

calls = []


class FakePanel:
    def add_clients(self, *a):
        calls.append("add")
        return [[1, "u1-1-1"]]

    def topup(self, clients, gb):
        calls.append(f"topup{gb}")

    def remove(self, c):
        calls.append("rm")

    def usage(self, c):
        return {"used": 2 * 1024 ** 3, "total": 5 * 1024 ** 3, "expiry": 0}


main.panel = FakePanel()
JPG = b"\xff\xd8\xff\xe0" + b"0" * 100

with TestClient(main.app) as u, TestClient(main.app) as a:
    r = u.post("/register", data={"name": "علی", "phone": "۰۹۱۲۱۱۱۲۲۳۳", "password": "123"})
    assert "۶ کاراکتر" in r.text
    r = u.post("/register", data={"name": "علی", "phone": "09121112233", "email": "a@gmail.com", "password": "secret1"})
    assert "خوش آمدید" in r.text and "خرید" in r.text, r.text[:400]
    r = u.post("/register", data={"name": "x", "phone": "09121112233", "password": "secret1"})
    u.post("/logout")
    r = u.post("/login", data={"phone": "09121112233", "password": "bad"}); assert "اشتباه" in r.text
    r = u.post("/login", data={"phone": "+989121112233", "password": "secret1"}); assert "سلام علی" in r.text

    r = u.post("/order", data={"gb": "0"}, files={"receipt": ("r.jpg", JPG)}); assert "بین" in r.text
    r = u.post("/order", data={"gb": "5"}, files={"receipt": ("r.txt", b"hello")}); assert "رسید باید" in r.text
    r = u.post("/order", data={"gb": "۵"}, files={"receipt": ("r.jpg", JPG)}); assert "رسید ثبت شد" in r.text
    assert "۴٬۰۰۰٬۰۰۰" in r.text  # 5 x 800,000
    r = u.post("/order", data={"gb": "1"}, files={"receipt": ("r.jpg", JPG)}); assert "در انتظار" in r.text

    assert "/admin/login" in str(a.get("/admin").url)
    r = a.post("/admin/login", data={"password": "nope"}); assert "اشتباه" in r.text
    r = a.post("/admin/login", data={"password": "adminpw"}); assert "سفارش‌های در انتظار" in r.text
    rc = re.search(r"/admin/receipt/([0-9a-f]{32}\.jpg)", r.text).group(1)
    assert a.get("/admin/receipt/" + rc).status_code == 200
    assert u.get("/admin/receipt/" + rc, follow_redirects=False).status_code == 303
    r = a.post("/admin/order/1", data={"action": "approve"}); assert "تأیید شد" in r.text
    r = u.get("/panel"); assert "https://sub.t/sub/" in r.text and "<svg" in r.text and "۳ <small>KG باقی" in r.text

    u.post("/order", data={"gb": "2"}, files={"receipt": ("r.pdf", b"%PDF-1.4 x")})
    r = a.post("/admin/order/2", data={"action": "approve"}); assert "تأیید شد" in r.text
    u.post("/order", data={"gb": "3"}, files={"receipt": ("r.png", b"\x89PNG\r\n\x1a\n000")})
    r = a.post("/admin/order/3", data={"action": "reject", "note": "مبلغ کامل نیست"}); assert "رد شد" in r.text
    r = u.get("/panel"); assert "مبلغ کامل نیست" in r.text
    r = a.post("/admin/user/1/reset"); new_pw = re.search(r"رمز جدید کاربر #1: (\S+)<", r.text).group(1)
    u.post("/logout"); r = u.post("/login", data={"phone": "09121112233", "password": new_pw}); assert "سلام" in r.text
    r = a.post("/admin/user/1/revoke"); assert "غیرفعال شد" in r.text

    low = (u.get("/panel").text + a.get("/admin").text).lower()
    for w in ["vless", "vpn", "xray", "x-ui", "proxy", "v2ray", "پروکسی", "فیلترشکن", "وی پی ان"]:
        assert w not in low, w
    assert calls == ["add", "topup2", "rm"], calls
print("ALL OK")
