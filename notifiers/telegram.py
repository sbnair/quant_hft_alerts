import requests

import config


def send(title: str, message: str) -> dict:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return {"ok": False, "error": "telegram not configured"}
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    text = f"⚡ <b>{title}</b>\n\n{message}"
    try:
        r = requests.post(
            url,
            json={
                "chat_id": config.TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        body = r.json()
        return {"ok": bool(body.get("ok")), "error": body.get("description")}
    except Exception as e:
        return {"ok": False, "error": str(e)}
