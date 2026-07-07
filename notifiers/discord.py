import requests

import config


def send(title: str, message: str) -> dict:
    if not config.DISCORD_WEBHOOK_URL:
        return {"ok": False, "error": "discord not configured"}
    payload = {
        "embeds": [
            {
                "title": f"⚡ {title}",
                "description": message,
                "color": 0xF59E0B,
            }
        ]
    }
    try:
        r = requests.post(config.DISCORD_WEBHOOK_URL, json=payload, timeout=10)
        ok = r.status_code in (200, 204)
        return {"ok": ok, "error": None if ok else f"HTTP {r.status_code}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
