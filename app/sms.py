import logging
import os

import httpx

log = logging.getLogger("sms")


def send_otp(phone: str, code: str):
    provider = os.getenv("SMS_PROVIDER", "console")
    if provider == "kavenegar":
        key = os.environ["KAVENEGAR_API_KEY"]
        r = httpx.get(
            f"https://api.kavenegar.com/v1/{key}/verify/lookup.json",
            params={"receptor": phone, "token": code, "template": os.environ["KAVENEGAR_TEMPLATE"]},
            timeout=10,
        )
        r.raise_for_status()
    else:
        # DEV ONLY: code goes to container logs
        log.warning("OTP %s -> %s", phone, code)
