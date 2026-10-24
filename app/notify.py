import logging
import os

import httpx

log = logging.getLogger("notify")


def admin(text: str):
    tok, chat = os.getenv("TG_BOT_TOKEN"), os.getenv("TG_ADMIN_CHAT_ID")
    if not (tok and chat):
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                   data={"chat_id": chat, "text": text}, timeout=5)
    except Exception:
        log.warning("telegram notify failed", exc_info=True)
