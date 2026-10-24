import hashlib
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

from . import db, kyc, notify, sms
from .panel import Panel, PanelError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("portal")

# ---------- config ----------
SECRET = os.environ["SECRET_KEY"]
BRAND = os.getenv("BRAND_NAME", "پرتال")
SUPPORT = os.getenv("SUPPORT_CONTACT", "")
ADMIN_PHONES = {kyc.norm_phone(p) for p in os.getenv("ADMIN_PHONES", "").split(",") if kyc.norm_phone(p)}
SUB_BASE = os.getenv("SUB_BASE_URL", "").rstrip("/")
EMAIL_PREFIX = os.getenv("CLIENT_EMAIL_PREFIX", "p")
AUTO_APPROVE = os.getenv("AUTO_APPROVE_REQUESTS", "false").lower() == "true"
START_ON_FIRST_USE = os.getenv("START_ON_FIRST_USE", "true").lower() == "true"
KYC_PURGE = os.getenv("KYC_PURGE_AFTER_REVIEW", "true").lower() == "true"
MAX_ACTIVE = int(os.getenv("MAX_ACTIVE_PER_USER", "1"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
DATA = Path(os.getenv("DATA_DIR", "/data"))
KYC_DIR = DATA / "kyc"
MAX_UPLOAD = 5 * 1024 * 1024
OTP_TTL, OTP_COOLDOWN, OTP_MAX_TRIES = 120, 60, 5
PLANS = {p["id"]: p for p in json.loads(Path(os.getenv("PLANS_FILE", "/app/plans.json")).read_text("utf-8"))}

panel = Panel()
BASE = Path(__file__).parent
tpl = Jinja2Templates(directory=BASE / "templates")


@asynccontextmanager
async def lifespan(_):
    KYC_DIR.mkdir(parents=True, exist_ok=True)
    db.init()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionMiddleware, secret_key=SECRET, session_cookie="sid",
                   max_age=7 * 86400, same_site="lax", https_only=COOKIE_SECURE)
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
        req.session.clear()
        raise Redirect("/login")
    return u


def is_admin(u):
    return bool(u) and u["phone"] in ADMIN_PHONES


def need_admin(req):
    u = need_user(req)
    if not is_admin(u):
        raise HTTPException(404)
    return u


def audit(actor, action, target):
    with db.conn() as c:
        c.execute("INSERT INTO audit(actor,action,target,at) VALUES(?,?,?,?)",
                  (actor, action, str(target), int(time.time())))


def render(req, name, **ctx):
    ctx.setdefault("user", current_user(req))
    ctx.update(brand=BRAND, support=SUPPORT, flash=req.session.pop("flash", None),
               admin=is_admin(ctx["user"]))
    return tpl.TemplateResponse(req, name, ctx)


def otp_hash(phone, code):
    return hmac.new(SECRET.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


_ip_hits: dict = defaultdict(deque)


def ip_allowed(req, limit=5, window=600):
    ip = req.client.host if req.client else "?"
    q, now = _ip_hits[ip], time.time()
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


def fmt_usage(u):
    gb = lambda b: f"{b / 1024 ** 3:.1f}"
    exp = u["expiry"]
    if exp < 0:
        e = f"{-exp // 86_400_000} روز، از اولین استفاده"
    elif exp == 0:
        e = "بدون محدودیت زمانی"
    else:
        left = (exp / 1000 - time.time()) / 86400
        e = f"{int(left)} روز باقی‌مانده" if left > 0 else "منقضی شده"
    return {"used": gb(u["used"]), "total": gb(u["total"]) if u["total"] else "∞", "expiry": e,
            "pct": min(100, int(100 * u["used"] / u["total"])) if u["total"] else 0}


def provision(rid):
    with db.conn() as c:
        r = c.execute("SELECT * FROM requests WHERE id=? AND status='pending'", (rid,)).fetchone()
    if not r:
        raise ValueError("request is not pending")
    plan = PLANS[r["plan_id"]]
    sub_id, cid = secrets.token_hex(8), str(uuid.uuid4())
    clients = panel.add_clients(plan["inbounds"], sub_id, cid, f"{EMAIL_PREFIX}{r['user_id']}-{rid}",
                                plan["gb"], plan["days"], plan.get("ip_limit", 0), START_ON_FIRST_USE)
    with db.conn() as c:
        c.execute("UPDATE requests SET status='active',sub_id=?,client_uuid=?,clients=?,decided_at=? WHERE id=?",
                  (sub_id, cid, json.dumps(clients), int(time.time()), rid))


def purge_docs(u):
    for f in (u["id_doc"], u["selfie"]):
        if f:
            (KYC_DIR / f).unlink(missing_ok=True)


# ---------- public ----------
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/")
def index(req: Request):
    return RedirectResponse("/dashboard" if req.session.get("uid") else "/login", 303)


@app.get("/login")
def login_page(req: Request):
    if req.session.get("uid"):
        raise Redirect("/dashboard")
    return render(req, "login.html")


@app.post("/login")
def login(req: Request, phone: str = Form(...)):
    p = kyc.norm_phone(phone)
    if not p:
        fail(req, "شماره موبایل معتبر نیست.", "/login")
    now = int(time.time())
    with db.conn() as c:
        row = c.execute("SELECT last_sent FROM otps WHERE phone=?", (p,)).fetchone()
    if row and now - row["last_sent"] < OTP_COOLDOWN:
        req.session["otp_phone"] = p
        fail(req, f"کد قبلاً ارسال شده. {OTP_COOLDOWN - (now - row['last_sent'])} ثانیه دیگر دوباره تلاش کنید.", "/verify")
    if not ip_allowed(req):
        fail(req, "تعداد درخواست‌ها زیاد است. چند دقیقه بعد تلاش کنید.", "/login")
    code = f"{secrets.randbelow(10 ** 6):06d}"
    with db.conn() as c:
        c.execute("INSERT INTO otps(phone,code_hash,expires_at,attempts,last_sent) VALUES(?,?,?,0,?) "
                  "ON CONFLICT(phone) DO UPDATE SET code_hash=excluded.code_hash, "
                  "expires_at=excluded.expires_at, attempts=0, last_sent=excluded.last_sent",
                  (p, otp_hash(p, code), now + OTP_TTL, now))
    try:
        sms.send_otp(p, code)
    except Exception:
        log.exception("sms send failed")
        fail(req, "ارسال پیامک ناموفق بود. کمی بعد تلاش کنید.", "/login")
    req.session["otp_phone"] = p
    raise Redirect("/verify")


@app.get("/verify")
def verify_page(req: Request):
    p = req.session.get("otp_phone")
    if not p:
        raise Redirect("/login")
    return render(req, "verify.html", phone=p)


@app.post("/verify")
def verify(req: Request, code: str = Form(...)):
    p = req.session.get("otp_phone")
    if not p:
        raise Redirect("/login")
    code, now, uid = kyc.digits(code), int(time.time()), None
    with db.conn() as c:
        row = c.execute("SELECT * FROM otps WHERE phone=?", (p,)).fetchone()
        ok = bool(row) and row["expires_at"] > now and row["attempts"] < OTP_MAX_TRIES \
            and hmac.compare_digest(row["code_hash"], otp_hash(p, code))
        if ok:
            c.execute("DELETE FROM otps WHERE phone=?", (p,))
            c.execute("INSERT OR IGNORE INTO users(phone,created_at) VALUES(?,?)", (p, now))
            uid = c.execute("SELECT id FROM users WHERE phone=?", (p,)).fetchone()["id"]
        elif row:
            c.execute("UPDATE otps SET attempts=attempts+1 WHERE phone=?", (p,))
    if not ok:
        fail(req, "کد نادرست یا منقضی شده است.", "/verify")
    req.session.clear()
    req.session["uid"] = uid
    raise Redirect("/admin" if p in ADMIN_PHONES else "/dashboard")


@app.post("/logout")
def logout(req: Request):
    req.session.clear()
    raise Redirect("/login")


# ---------- customer ----------
@app.get("/dashboard")
def dashboard(req: Request):
    u = need_user(req)
    with db.conn() as c:
        rows = c.execute("SELECT * FROM requests WHERE user_id=? ORDER BY id DESC", (u["id"],)).fetchall()
    items, open_count = [], 0
    for r in rows:
        d = dict(r)
        d["plan"] = PLANS.get(r["plan_id"], {"title": r["plan_id"]})
        if r["status"] in ("pending", "active"):
            open_count += 1
        if r["status"] == "active":
            d["link"] = f"{SUB_BASE}/{r['sub_id']}"
            d["qr"] = qr_svg(d["link"])
            try:
                d["usage"] = fmt_usage(panel.usage(json.loads(r["clients"])))
            except Exception as e:
                log.warning("usage fetch failed: %s", e)
                d["usage"] = None
        items.append(d)
    can_request = u["kyc_status"] == "approved" and open_count < MAX_ACTIVE
    return render(req, "dashboard.html", user=u, items=items, plans=list(PLANS.values()),
                  can_request=can_request)


@app.post("/kyc")
def submit_kyc(req: Request, full_name: str = Form(...), national_code: str = Form(...),
               id_doc: UploadFile = File(...), selfie: UploadFile = File(...)):
    u = need_user(req)
    if u["kyc_status"] not in ("none", "rejected"):
        raise Redirect("/dashboard")
    name, nc = " ".join(full_name.split()), kyc.digits(national_code)
    if not 3 <= len(name) <= 80:
        fail(req, "نام و نام خانوادگی را کامل وارد کنید.", "/dashboard")
    if not kyc.valid_national_code(nc):
        fail(req, "کد ملی معتبر نیست.", "/dashboard")
    with db.conn() as c:
        if c.execute("SELECT 1 FROM users WHERE national_code=? AND id!=?", (nc, u["id"])).fetchone():
            fail(req, "این کد ملی قبلاً با شماره دیگری ثبت شده است.", "/dashboard")
    if kyc.shahkar_check(u["phone"], nc) is False:
        fail(req, "شماره موبایل به نام این کد ملی ثبت نشده است.", "/dashboard")

    saved = []
    for f in (id_doc, selfie):
        b = f.file.read(MAX_UPLOAD + 1)
        ext = kyc.sniff_image(b)
        if len(b) > MAX_UPLOAD or not ext:
            for s in saved:
                (KYC_DIR / s).unlink(missing_ok=True)
            fail(req, "تصویر باید JPG/PNG/WEBP و کمتر از ۵ مگابایت باشد.", "/dashboard")
        fn = f"{secrets.token_hex(16)}.{ext}"
        (KYC_DIR / fn).write_bytes(b)
        saved.append(fn)

    purge_docs(u)  # leftovers from a rejected attempt
    with db.conn() as c:
        c.execute("UPDATE users SET full_name=?,national_code=?,id_doc=?,selfie=?,"
                  "kyc_status='submitted',kyc_note=NULL WHERE id=?", (name, nc, saved[0], saved[1], u["id"]))
    notify.admin(f"🪪 احراز هویت جدید: {name} — {u['phone']}")
    flash(req, "مدارک ثبت شد. پس از بررسی، نتیجه همین‌جا نمایش داده می‌شود.")
    raise Redirect("/dashboard")


@app.post("/request")
def new_request(req: Request, plan_id: str = Form(...)):
    u = need_user(req)
    if u["kyc_status"] != "approved":
        fail(req, "ابتدا احراز هویت را کامل کنید.", "/dashboard")
    if plan_id not in PLANS:
        fail(req, "سرویس انتخاب‌شده معتبر نیست.", "/dashboard")
    with db.conn() as c:
        n = c.execute("SELECT COUNT(*) FROM requests WHERE user_id=? AND status IN ('pending','active')",
                      (u["id"],)).fetchone()[0]
        if n < MAX_ACTIVE:
            rid = c.execute("INSERT INTO requests(user_id,plan_id,created_at) VALUES(?,?,?)",
                            (u["id"], plan_id, int(time.time()))).lastrowid
    if n >= MAX_ACTIVE:
        fail(req, "شما یک سرویس فعال یا در انتظار دارید.", "/dashboard")
    if AUTO_APPROVE:
        try:
            provision(rid)
            audit("auto", "provision", rid)
            flash(req, "سرویس شما فعال شد ✓")
            raise Redirect("/dashboard")
        except (PanelError, ValueError, KeyError) as e:
            log.error("auto provision failed: %s", e)
    notify.admin(f"📥 درخواست جدید #{rid}: {PLANS[plan_id]['title']} — {u['phone']}")
    flash(req, "درخواست ثبت شد و به‌زودی فعال می‌شود.")
    raise Redirect("/dashboard")


# ---------- admin ----------
@app.get("/admin")
def admin_home(req: Request):
    a = need_admin(req)
    q = "SELECT r.*, u.phone, u.full_name FROM requests r JOIN users u ON u.id=r.user_id WHERE r.status=? ORDER BY r.id {} LIMIT 300"
    with db.conn() as c:
        kycs = c.execute("SELECT * FROM users WHERE kyc_status='submitted' ORDER BY id").fetchall()
        pending = c.execute(q.format("ASC"), ("pending",)).fetchall()
        active = c.execute(q.format("DESC"), ("active",)).fetchall()
        stats = c.execute("SELECT (SELECT COUNT(*) FROM users) users, "
                          "(SELECT COUNT(*) FROM users WHERE kyc_status='approved') verified, "
                          "(SELECT COUNT(*) FROM requests WHERE status='active') active").fetchone()
    title = lambda pid: PLANS.get(pid, {}).get("title", pid)
    return render(req, "admin.html", user=a, kycs=kycs, pending=pending, active=active,
                  stats=stats, plan_title=title)


@app.get("/admin/file/{name}")
def admin_file(req: Request, name: str):
    need_admin(req)
    if not re.fullmatch(r"[0-9a-f]{32}\.(jpg|png|webp)", name) or not (KYC_DIR / name).exists():
        raise HTTPException(404)
    return FileResponse(KYC_DIR / name, headers={"Cache-Control": "no-store"})


@app.post("/admin/kyc/{uid}")
def admin_kyc(req: Request, uid: int, action: str = Form(...), note: str = Form("")):
    a = need_admin(req)
    status = {"approve": "approved", "reject": "rejected"}.get(action)
    if not status:
        raise HTTPException(400)
    with db.conn() as c:
        u = c.execute("SELECT * FROM users WHERE id=? AND kyc_status='submitted'", (uid,)).fetchone()
        if u:
            c.execute("UPDATE users SET kyc_status=?, kyc_note=? WHERE id=?",
                      (status, note.strip()[:200] or None, uid))
            if KYC_PURGE:
                c.execute("UPDATE users SET id_doc=NULL, selfie=NULL WHERE id=?", (uid,))
    if u:
        if KYC_PURGE:
            purge_docs(u)
        audit(a["phone"], f"kyc_{status}", uid)
        flash(req, f"احراز هویت {u['full_name']}: {'تأیید' if status == 'approved' else 'رد'} شد.")
    raise Redirect("/admin")


@app.post("/admin/request/{rid}")
def admin_request(req: Request, rid: int, action: str = Form(...), note: str = Form("")):
    a = need_admin(req)
    if action == "approve":
        try:
            provision(rid)
        except (PanelError, ValueError, KeyError) as e:
            fail(req, f"فعال‌سازی ناموفق: {e}", "/admin")
        flash(req, f"درخواست #{rid} فعال شد.")
    elif action == "reject":
        with db.conn() as c:
            c.execute("UPDATE requests SET status='rejected', note=?, decided_at=? WHERE id=? AND status='pending'",
                      (note.strip()[:200] or None, int(time.time()), rid))
        flash(req, f"درخواست #{rid} رد شد.")
    elif action == "revoke":
        with db.conn() as c:
            r = c.execute("SELECT * FROM requests WHERE id=? AND status='active'", (rid,)).fetchone()
        if r:
            try:
                panel.remove(json.loads(r["clients"]))
            except PanelError as e:
                fail(req, f"غیرفعال‌سازی ناموفق: {e}", "/admin")
            with db.conn() as c:
                c.execute("UPDATE requests SET status='revoked', decided_at=? WHERE id=?", (int(time.time()), rid))
        flash(req, f"سرویس #{rid} غیرفعال شد.")
    else:
        raise HTTPException(400)
    audit(a["phone"], f"request_{action}", rid)
    raise Redirect("/admin")
