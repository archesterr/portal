import hmac
import io
import json
import logging
import os
import re
import secrets
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path

import qrcode
import qrcode.image.svg
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import db, notify
from .panel import Panel, PanelError
from .util import check_pw, digits, fa, hash_pw, norm_phone, sniff_file, valid_email

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("shop")

# ---------- config ----------
SECRET = os.environ["SECRET_KEY"]
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]
BRAND = os.getenv("BRAND_NAME", "فروشگاه")
TELEGRAM = os.getenv("SUPPORT_TELEGRAM", "").lstrip("@")
PRICE = int(os.getenv("PRICE_PER_GB", "800000"))          # toman per KG
MIN_GB, MAX_GB = int(os.getenv("MIN_GB", "1")), int(os.getenv("MAX_GB", "200"))
PRESETS = [int(x) for x in os.getenv("GB_PRESETS", "1,2,5,10,20,50").split(",") if x.strip()]
INBOUNDS = [int(x) for x in os.getenv("INBOUND_IDS", "1").split(",") if x.strip()]
SERVICE_DAYS = int(os.getenv("SERVICE_DAYS", "0"))        # 0 = volume only
IP_LIMIT = int(os.getenv("IP_LIMIT", "0"))
SUB_BASE = os.getenv("SUB_BASE_URL", "").rstrip("/")
EMAIL_PREFIX = os.getenv("CLIENT_EMAIL_PREFIX", "u")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
DATA = Path(os.getenv("DATA_DIR", "/data"))
RECEIPTS = DATA / "receipts"
MAX_UPLOAD = 8 * 1024 * 1024

panel = Panel()
BASE = Path(__file__).parent
tpl = Jinja2Templates(directory=BASE / "templates")
tpl.env.filters["fa"] = fa


@asynccontextmanager
async def lifespan(_):
    RECEIPTS.mkdir(parents=True, exist_ok=True)
    db.init()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET, session_cookie="sid",
                   max_age=30 * 86400, same_site="lax", https_only=COOKIE_SECURE)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.middleware("http")
async def security_headers(req: Request, call_next):
    r = await call_next(req)
    r.headers.update({
        "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self'; "
                                   "script-src 'self'; frame-ancestors 'none'; form-action 'self'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex, nofollow",
    })
    return r


# ---------- helpers ----------
class Redirect(Exception):
    def __init__(self, url):
        self.url = url


@app.exception_handler(Redirect)
async def _redirect(_req, exc: Redirect):
    return RedirectResponse(exc.url, 303)


def flash(req, msg, kind="ok"):
    req.session["flash"] = {"m": msg, "k": kind}


def fail(req, msg, to):
    flash(req, msg, "err")
    raise Redirect(to)


def current_user(req):
    uid = req.session.get("uid")
    if not uid:
        return None
    with db.conn() as c:
        return c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()


def need_user(req):
    u = current_user(req)
    if not u:
        req.session.pop("uid", None)
        raise Redirect("/login")
    return u


def need_admin(req):
    if not req.session.get("admin"):
        raise Redirect("/admin/login")


def audit(actor, action, target):
    with db.conn() as c:
        c.execute("INSERT INTO audit(actor,action,target,at) VALUES(?,?,?,?)",
                  (actor, action, str(target), int(time.time())))


def render(req, name, **ctx):
    ctx.setdefault("user", current_user(req))
    ctx.update(brand=BRAND, telegram=TELEGRAM, flash=req.session.pop("flash", None),
               is_admin=bool(req.session.get("admin")))
    return tpl.TemplateResponse(req, name, ctx)


_hits: dict = defaultdict(deque)


def rate_ok(req, key, limit, window):
    ip = req.client.host if req.client else "?"
    q, now = _hits[(key, ip)], time.time()
    while q and q[0] < now - window:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def qr_svg(text):
    img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    s = buf.getvalue().decode()
    return s[s.find("<svg"):]


def gb_str(b):
    v = b / 1024 ** 3
    return fa(f"{v:.2f}".rstrip("0").rstrip("."))


def login_as(req, uid):
    admin = req.session.get("admin")
    req.session.clear()
    req.session["uid"] = uid
    if admin:
        req.session["admin"] = True


def deliver(order):
    """Create the customer's service, or top up the existing one."""
    with db.conn() as c:
        svc = c.execute("SELECT * FROM services WHERE user_id=?", (order["user_id"],)).fetchone()
    now = int(time.time())
    if svc and svc["status"] == "active":
        panel.topup(json.loads(svc["clients"]), order["gb"])
        with db.conn() as c:
            c.execute("UPDATE services SET total_gb=total_gb+? WHERE user_id=?", (order["gb"], order["user_id"]))
        return
    sub_id, cid = secrets.token_hex(8), str(uuid.uuid4())
    clients = panel.add_clients(INBOUNDS, sub_id, cid, f"{EMAIL_PREFIX}{order['user_id']}-{order['id']}",
                                order["gb"], SERVICE_DAYS, IP_LIMIT, True)
    with db.conn() as c:
        c.execute("INSERT INTO services(user_id,sub_id,client_uuid,clients,total_gb,status,created_at) "
                  "VALUES(?,?,?,?,?,'active',?) ON CONFLICT(user_id) DO UPDATE SET sub_id=excluded.sub_id, "
                  "client_uuid=excluded.client_uuid, clients=excluded.clients, total_gb=excluded.total_gb, "
                  "status='active', created_at=excluded.created_at",
                  (order["user_id"], sub_id, cid, json.dumps(clients), order["gb"], now))


# ---------- public ----------
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index(req: Request):
    return RedirectResponse("/panel" if req.session.get("uid") else "/register", 303)


@app.get("/register")
def register_page(req: Request):
    if req.session.get("uid"):
        raise Redirect("/panel")
    return render(req, "register.html")


@app.post("/register")
def register(req: Request, name: str = Form(...), phone: str = Form(...),
             email: str = Form(""), password: str = Form(...)):
    name, p, email = " ".join(name.split()), norm_phone(phone), email.strip().lower()
    if not 2 <= len(name) <= 60:
        fail(req, "نام را وارد کنید.", "/register")
    if not p:
        fail(req, "شماره موبایل معتبر نیست (مثل 09121234567).", "/register")
    if email and not valid_email(email):
        fail(req, "ایمیل معتبر نیست.", "/register")
    if not 6 <= len(password) <= 128:
        fail(req, "رمز عبور حداقل ۶ کاراکتر باشد.", "/register")
    if not rate_ok(req, "register", 5, 3600):
        fail(req, "تعداد تلاش زیاد است. کمی بعد دوباره امتحان کنید.", "/register")
    with db.conn() as c:
        if c.execute("SELECT 1 FROM users WHERE phone=?", (p,)).fetchone():
            fail(req, "این شماره قبلاً ثبت شده است. وارد شوید.", "/login")
        uid = c.execute("INSERT INTO users(name,phone,email,pw_hash,created_at) VALUES(?,?,?,?,?)",
                        (name, p, email or None, hash_pw(password), int(time.time()))).lastrowid
    login_as(req, uid)
    flash(req, f"خوش آمدید {name} 👋")
    raise Redirect("/panel")


@app.get("/login")
def login_page(req: Request):
    if req.session.get("uid"):
        raise Redirect("/panel")
    return render(req, "login.html")


@app.post("/login")
def login(req: Request, phone: str = Form(...), password: str = Form(...)):
    if not rate_ok(req, "login", 10, 600):
        fail(req, "تعداد تلاش زیاد است. چند دقیقه بعد امتحان کنید.", "/login")
    p = norm_phone(phone)
    with db.conn() as c:
        u = c.execute("SELECT * FROM users WHERE phone=?", (p,)).fetchone() if p else None
    if not u or not check_pw(password, u["pw_hash"]):
        fail(req, "شماره یا رمز عبور اشتباه است.", "/login")
    login_as(req, u["id"])
    raise Redirect("/panel")


@app.post("/logout")
def logout(req: Request):
    req.session.clear()
    raise Redirect("/login")


# ---------- customer ----------
@app.get("/panel")
def customer_panel(req: Request):
    u = need_user(req)
    with db.conn() as c:
        svc = c.execute("SELECT * FROM services WHERE user_id=?", (u["id"],)).fetchone()
        orders = c.execute("SELECT * FROM orders WHERE user_id=? ORDER BY id DESC LIMIT 20", (u["id"],)).fetchall()
    service = None
    if svc and svc["status"] == "active":
        link = f"{SUB_BASE}/{svc['sub_id']}"
        service = {"link": link, "qr": qr_svg(link), "total": fa(svc["total_gb"]), "used": None, "pct": 0}
        try:
            us = panel.usage(json.loads(svc["clients"]))
            total = us["total"] or svc["total_gb"] * 1024 ** 3
            service.update(used=gb_str(us["used"]), total=gb_str(total),
                           left=gb_str(max(0, total - us["used"])),
                           pct=min(100, int(100 * us["used"] / total)) if total else 0)
        except Exception as e:
            log.warning("usage fetch failed: %s", e)
    pending = any(o["status"] == "pending" for o in orders)
    return render(req, "panel.html", user=u, service=service, orders=orders, pending=pending,
                  price=PRICE, presets=PRESETS, min_gb=MIN_GB, max_gb=MAX_GB)


@app.post("/order")
def new_order(req: Request, gb: str = Form(...), receipt: UploadFile = File(...)):
    u = need_user(req)
    try:
        n = int(digits(gb))
    except ValueError:
        n = 0
    if not MIN_GB <= n <= MAX_GB:
        fail(req, f"مقدار باید بین {fa(MIN_GB)} تا {fa(MAX_GB)} KG باشد.", "/panel")
    with db.conn() as c:
        if c.execute("SELECT 1 FROM orders WHERE user_id=? AND status='pending'", (u["id"],)).fetchone():
            fail(req, "یک سفارش در انتظار تأیید دارید.", "/panel")
    b = receipt.file.read(MAX_UPLOAD + 1)
    ext = sniff_file(b)
    if len(b) > MAX_UPLOAD or not ext:
        fail(req, "رسید باید عکس (JPG/PNG) یا PDF و کمتر از ۸ مگابایت باشد.", "/panel")
    fn = f"{secrets.token_hex(16)}.{ext}"
    (RECEIPTS / fn).write_bytes(b)
    amount = n * PRICE
    with db.conn() as c:
        oid = c.execute("INSERT INTO orders(user_id,gb,amount,receipt,created_at) VALUES(?,?,?,?,?)",
                        (u["id"], n, amount, fn, int(time.time()))).lastrowid
    notify.admin(f"🧾 سفارش #{oid}: {n} KG — {amount:,} تومان\n{u['name']} — {u['phone']}")
    flash(req, "رسید ثبت شد ✓ پس از تأیید، حجم به پنل شما اضافه می‌شود.")
    raise Redirect("/panel")


# ---------- admin ----------
@app.get("/admin/login")
def admin_login_page(req: Request):
    return render(req, "admin_login.html", user=None)


@app.post("/admin/login")
def admin_login(req: Request, password: str = Form(...)):
    if not rate_ok(req, "admin", 5, 900) or not hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        time.sleep(1)
        fail(req, "رمز اشتباه است.", "/admin/login")
    req.session["admin"] = True
    raise Redirect("/admin")


@app.post("/admin/logout")
def admin_logout(req: Request):
    req.session.pop("admin", None)
    raise Redirect("/admin/login")


ORDER_TABS = ("pending", "approved", "rejected", "all")
PAGE = 50


def back_to(tab):
    return f"/admin?status={tab}" if tab in ORDER_TABS else "/admin"


@app.get("/admin")
def admin_home(req: Request, status: str = "pending", page: int = 1):
    need_admin(req)
    status = status if status in ORDER_TABS else "pending"
    page = max(1, page)
    where, args = ("", ()) if status == "all" else ("WHERE o.status=?", (status,))
    order_by = "o.id ASC" if status == "pending" else "o.id DESC"
    with db.conn() as c:
        counts = {r["status"]: r["n"] for r in c.execute("SELECT status, COUNT(*) n FROM orders GROUP BY status")}
        counts["all"] = sum(counts.values())
        orders = c.execute(
            "SELECT o.*, u.name, u.phone, u.email, s.status AS svc FROM orders o "
            "JOIN users u ON u.id=o.user_id LEFT JOIN services s ON s.user_id=o.user_id "
            f"{where} ORDER BY {order_by} LIMIT ? OFFSET ?", (*args, PAGE + 1, (page - 1) * PAGE)).fetchall()
        stats = c.execute("SELECT (SELECT COUNT(*) FROM users) users, "
                          "(SELECT COUNT(*) FROM services WHERE status='active') active, "
                          "(SELECT COALESCE(SUM(amount),0) FROM orders WHERE status='approved') revenue").fetchone()
    return render(req, "admin.html", user=None, orders=orders[:PAGE], has_next=len(orders) > PAGE,
                  page=page, status=status, counts=counts, stats=stats, tab="orders")


@app.get("/admin/customers")
def admin_customers(req: Request):
    need_admin(req)
    with db.conn() as c:
        customers = c.execute("SELECT u.*, s.total_gb, s.status AS svc, s.sub_id, "
                              "(SELECT COUNT(*) FROM orders o WHERE o.user_id=u.id AND o.status='approved') paid "
                              "FROM users u LEFT JOIN services s ON s.user_id=u.id ORDER BY u.id DESC").fetchall()
    return render(req, "admin_customers.html", user=None, customers=customers, sub_base=SUB_BASE, tab="customers")


@app.get("/admin/receipt/{name}")
def admin_receipt(req: Request, name: str):
    need_admin(req)
    if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp|pdf)", name) or not (RECEIPTS / name).exists():
        raise HTTPException(404)
    return FileResponse(RECEIPTS / name, headers={"Cache-Control": "no-store"})


@app.post("/admin/order/{oid}")
def admin_order(req: Request, oid: int, action: str = Form(...), note: str = Form(""), back: str = Form("pending")):
    need_admin(req)
    dest = back_to(back)
    with db.conn() as c:
        o = c.execute("SELECT * FROM orders WHERE id=?", (oid,)).fetchone()
    allowed = {"approve": ("pending", "rejected"), "reject": ("pending",)}
    if not o or o["status"] not in allowed.get(action, ()):
        fail(req, "این سفارش قابل تغییر نیست (قبلاً بررسی شده).", dest)
    if action == "approve":
        try:
            deliver(o)
        except PanelError as e:
            fail(req, f"ارسال به پنل ناموفق: {e}", dest)
        status = "approved"
        flash(req, f"سفارش #{oid} تأیید شد و {o['gb']} KG اضافه شد.")
    elif action == "reject":
        status = "rejected"
        flash(req, f"سفارش #{oid} رد شد.")
    else:
        raise HTTPException(400)
    with db.conn() as c:
        c.execute("UPDATE orders SET status=?, note=?, decided_at=? WHERE id=?",
                  (status, note.strip()[:200] or None, int(time.time()), oid))
    audit("admin", f"order_{status}", oid)
    raise Redirect(dest)


@app.post("/admin/user/{uid}/revoke")
def admin_revoke(req: Request, uid: int):
    need_admin(req)
    with db.conn() as c:
        s = c.execute("SELECT * FROM services WHERE user_id=? AND status='active'", (uid,)).fetchone()
    if s:
        try:
            panel.remove(json.loads(s["clients"]))
        except PanelError as e:
            fail(req, f"غیرفعال‌سازی ناموفق: {e}", "/admin/customers")
        with db.conn() as c:
            c.execute("UPDATE services SET status='revoked' WHERE user_id=?", (uid,))
        audit("admin", "revoke", uid)
        flash(req, "سرویس غیرفعال شد.")
    raise Redirect("/admin/customers")


@app.post("/admin/user/{uid}/reset")
def admin_reset(req: Request, uid: int):
    need_admin(req)
    pw = secrets.token_urlsafe(6)
    with db.conn() as c:
        n = c.execute("UPDATE users SET pw_hash=? WHERE id=?", (hash_pw(pw), uid)).rowcount
    if n:
        audit("admin", "reset_pw", uid)
        flash(req, f"رمز جدید کاربر #{uid}: {pw}")
    raise Redirect("/admin/customers")
