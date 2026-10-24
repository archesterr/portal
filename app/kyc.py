import re

_TR = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def digits(s: str) -> str:
    return (s or "").translate(_TR).strip()


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


def valid_national_code(c: str) -> bool:
    if not re.fullmatch(r"\d{10}", c or "") or len(set(c)) == 1:
        return False
    check = int(c[9])
    s = sum(int(c[i]) * (10 - i) for i in range(9)) % 11
    return check == s if s < 2 else check == 11 - s


def sniff_image(b: bytes):
    if b[:3] == b"\xff\xd8\xff":
        return "jpg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "webp"
    return None


def shahkar_check(phone: str, national_code: str):
    """Phone <-> national-code ownership check (Shahkar).

    Plug your provider (Finnotech / Jibit / Zibal ...) here and return True/False.
    Returning None means "not configured" -> admin reviews manually.
    """
    return None
