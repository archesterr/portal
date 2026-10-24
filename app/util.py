import hashlib
import hmac
import os
import re

_TR = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_FA = str.maketrans("0123456789,", "۰۱۲۳۴۵۶۷۸۹٬")


def digits(s: str) -> str:
    return (s or "").translate(_TR).strip()


def fa(n) -> str:
    """1234567 -> ۱٬۲۳۴٬۵۶۷"""
    return f"{n:,}".translate(_FA) if isinstance(n, int) else str(n).translate(_FA)


def norm_phone(s: str):
    s = re.sub(r"[\s\-()]", "", digits(s))
    if s.startswith("+98"):
        s = "0" + s[3:]
    elif s.startswith("0098"):
        s = "0" + s[4:]
    elif s.startswith("98") and len(s) == 12:
        s = "0" + s[2:]
    elif s.startswith("9") and len(s) == 10:
        s = "0" + s
    return s if re.fullmatch(r"09\d{9}", s) else None


def valid_email(s: str) -> bool:
    return bool(re.fullmatch(r"[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}", s or "")) and len(s) <= 120


def sniff_file(b: bytes):
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    if b[:5] == b"%PDF-":
        return "pdf"
    return None


def hash_pw(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"{salt.hex()}${h.hex()}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$")
        calc = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1).hex()
        return hmac.compare_digest(calc, h)
    except Exception:
        return False
