"""Default scanner watchlists: top-50 high-volume names + famous indexes.

- crypto: DYNAMIC — top 50 USDT pairs by 24h quote volume (stablecoin and
  leveraged-token pairs excluded), refreshed hourly from Binance.
- india:  NIFTY 50 constituents (the 50 largest/most-liquid NSE names)
          + NIFTY 50 and Bank NIFTY indexes.
- us:     50 highest-volume mega/large caps + S&P 500, Dow, NASDAQ indexes
          and SPY/QQQ.
"""
import threading
import time

import requests

# ── India: NIFTY 50 constituents (Dhan NSE_EQ trading symbols) ──
NIFTY50 = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "ITC", "LT",
    "AXISBANK", "SBIN", "BHARTIARTL", "KOTAKBANK", "HINDUNILVR",
    "BAJFINANCE", "ASIANPAINT", "MARUTI", "HCLTECH", "SUNPHARMA", "TITAN",
    "ULTRACEMCO", "NTPC", "TATAMOTORS", "POWERGRID", "TATASTEEL", "ONGC",
    "M&M", "WIPRO", "ADANIENT", "ADANIPORTS", "COALINDIA", "BAJAJFINSV",
    "NESTLEIND", "GRASIM", "JSWSTEEL", "HINDALCO", "TECHM", "DRREDDY",
    "CIPLA", "EICHERMOT", "INDUSINDBK", "APOLLOHOSP", "DIVISLAB",
    "BRITANNIA", "TATACONSUM", "HEROMOTOCO", "BAJAJ-AUTO", "BPCL",
    "SHRIRAMFIN", "LTIM", "SBILIFE", "HDFCLIFE",
]
INDIA_INDEXES = [
    ("^NSEI", "NIFTY 50"),
    ("^NSEBANK", "Bank NIFTY"),
]

# ── US: 50 most-traded large caps + headline indexes/ETFs ──
US_TOP50 = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO",
    "BRK-B", "JPM", "V", "MA", "UNH", "XOM", "LLY", "JNJ", "WMT", "PG",
    "HD", "COST", "ORCL", "MRK", "ABBV", "CVX", "KO", "PEP", "BAC", "AMD",
    "CRM", "NFLX", "ADBE", "QCOM", "CSCO", "INTC", "TXN", "IBM", "GE",
    "CAT", "BA", "DIS", "NKE", "MCD", "T", "VZ", "PYPL", "UBER", "PLTR",
    "COIN", "F", "GM",
]
US_INDEXES = [
    ("^GSPC", "S&P 500"),
    ("^DJI", "Dow Jones"),
    ("^IXIC", "NASDAQ Composite"),
    ("SPY", "SPDR S&P 500 ETF"),
    ("QQQ", "Invesco QQQ ETF"),
]

# ── Crypto: dynamic top-50 by 24h quote volume ──
_STABLE_BASES = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "EUR", "EURI",
                 "BUSD", "AEUR", "XUSD", "USD1", "USDE"}
_crypto_lock = threading.Lock()
_crypto_cache: tuple[float, list] | None = None
_CRYPTO_TTL = 3600

CRYPTO_FALLBACK = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "AVAXUSDT", "LINKUSDT", "TRXUSDT", "DOTUSDT", "LTCUSDT",
]


def crypto_top_by_volume(n: int = 50) -> list[tuple[str, str]]:
    """[(symbol, display name)] — top-n USDT pairs by 24h quote volume."""
    global _crypto_cache
    with _crypto_lock:
        if _crypto_cache and time.time() - _crypto_cache[0] < _CRYPTO_TTL:
            return _crypto_cache[1][:n]
    try:
        import config
        r = requests.get(
            f"{config.BINANCE_BASE_URL.rstrip('/')}/api/v3/ticker/24hr",
            timeout=20,
        )
        r.raise_for_status()
        rows = []
        for t in r.json():
            sym = t.get("symbol", "")
            if not sym.endswith("USDT"):
                continue
            base = sym[:-4]
            if base in _STABLE_BASES:
                continue
            if base.endswith(("UP", "DOWN", "BULL", "BEAR")):
                continue
            rows.append((sym, f"{base}/USDT", float(t.get("quoteVolume") or 0)))
        rows.sort(key=lambda x: -x[2])
        result = [(s, name) for s, name, _ in rows]
        with _crypto_lock:
            _crypto_cache = (time.time(), result)
        return result[:n]
    except Exception:
        return [(s, f"{s[:-4]}/USDT") for s in CRYPTO_FALLBACK][:n]


def get_watchlist(market: str, top_n: int = 50) -> dict:
    """{"stocks": [(symbol, name)], "indexes": [(symbol, name)]}"""
    if market == "crypto":
        return {"stocks": crypto_top_by_volume(top_n), "indexes": []}
    if market == "india":
        return {"stocks": [(s, s) for s in NIFTY50[:top_n]],
                "indexes": INDIA_INDEXES}
    if market == "us":
        return {"stocks": [(s, s) for s in US_TOP50[:top_n]],
                "indexes": US_INDEXES}
    raise ValueError(f"Unknown market: {market}")
