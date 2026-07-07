"""Central configuration — all secrets come from .env"""
import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

# All user-facing times are shown in IST
IST = timezone(timedelta(hours=5, minutes=30), "IST")


def ist_str(dt=None) -> str:
    """Format a datetime (naive = UTC) as e.g. '07-Jul-2026 22:05:31 IST'."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).strftime("%d-%b-%Y %H:%M:%S IST")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# --- Dhan (Indian stock market) ---
DHAN_CLIENT_ID = os.getenv("DHAN_CLIENT_ID", "").strip()
DHAN_ACCESS_TOKEN = os.getenv("DHAN_ACCESS_TOKEN", "").strip()

# --- Binance (Crypto) — public market data works without keys ---
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "").strip()
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").strip()
BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://api.binance.com").strip()

# --- Telegram alerts ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

# --- Discord alerts ---
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "").strip()

# --- App ---
FLASK_SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "dev-secret-change-me")
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))
DATABASE_URI = os.getenv(
    "DATABASE_URI", "sqlite:///" + os.path.join(BASE_DIR, "alerts.db")
)

# Skip alert evaluation when the market is closed (crypto is always open)
RESPECT_MARKET_HOURS = os.getenv("RESPECT_MARKET_HOURS", "true").lower() == "true"

# --- Built-in market scanner (top-N high-volume + indexes, no setup needed) ---
SCANNER_ENABLED = os.getenv("SCANNER_ENABLED", "true").lower() == "true"
SCANNER_TOP_N = int(os.getenv("SCANNER_TOP_N", "50"))
SCANNER_INTERVAL_SEC = max(30, int(os.getenv("SCANNER_INTERVAL_SEC", "120")))
SCANNER_IMBALANCE_THRESHOLD = float(os.getenv("SCANNER_IMBALANCE_THRESHOLD", "0.4"))
SCANNER_INDEX_MOVE_PCT = float(os.getenv("SCANNER_INDEX_MOVE_PCT", "1.0"))
SCANNER_COOLDOWN_SEC = int(os.getenv("SCANNER_COOLDOWN_SEC", "900"))
SCANNER_NOTIFY = os.getenv("SCANNER_NOTIFY", "true").lower() == "true"
SCANNER_CHANNELS = [c.strip() for c in
                    os.getenv("SCANNER_CHANNELS", "telegram,discord").split(",")
                    if c.strip()]


def _is_set(*values):
    """True only if every value is non-empty and not an obvious placeholder."""
    for v in values:
        if not v or v.lower().startswith(("your_", "change", "xxx", "123456:abc")):
            return False
        if "your-bot-token" in v or "/xxxx/" in v:
            return False
    return True


def integrations_status():
    """Which integrations are configured (used by the UI status bar)."""
    return {
        "dhan": _is_set(DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN),
        "binance": True,  # public endpoints, keys optional
        "binance_keys": _is_set(BINANCE_API_KEY),
        "telegram": _is_set(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID),
        "discord": _is_set(DISCORD_WEBHOOK_URL),
        "us_data": True,  # yfinance, no key required
    }
