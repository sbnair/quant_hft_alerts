"""Binance provider (crypto). Uses public REST endpoints — real L2 depth
gives the true order-book imbalance from the paper. API keys are optional
and only attached if configured."""
import time

import requests

import config

_session = requests.Session()
if config.BINANCE_API_KEY:
    _session.headers["X-MBX-APIKEY"] = config.BINANCE_API_KEY

_tick_size_cache: dict[str, float] = {}
_TICK_CACHE_TTL = 24 * 3600
_tick_cache_time = 0.0


def _base() -> str:
    return config.BINANCE_BASE_URL.rstrip("/")


def _get(path: str, params: dict | None = None):
    r = _session.get(f"{_base()}{path}", params=params or {}, timeout=10)
    r.raise_for_status()
    return r.json()


def get_tick_size(symbol: str) -> float:
    global _tick_cache_time
    now = time.time()
    if symbol in _tick_size_cache and now - _tick_cache_time < _TICK_CACHE_TTL:
        return _tick_size_cache[symbol]
    try:
        info = _get("/api/v3/exchangeInfo", {"symbol": symbol})
        for f in info["symbols"][0]["filters"]:
            if f["filterType"] == "PRICE_FILTER":
                _tick_size_cache[symbol] = float(f["tickSize"])
                _tick_cache_time = now
                return _tick_size_cache[symbol]
    except Exception:
        pass
    return 0.01


def get_snapshot(symbol: str) -> dict:
    symbol = symbol.upper().replace("/", "").replace("-", "")

    # Aggregated top-5 depth -> qb, qa (true order-book imbalance)
    depth = _get("/api/v3/depth", {"symbol": symbol, "limit": 5})
    bid_qty = sum(float(q) for _, q in depth.get("bids", []))
    ask_qty = sum(float(q) for _, q in depth.get("asks", []))
    bid = float(depth["bids"][0][0]) if depth.get("bids") else None
    ask = float(depth["asks"][0][0]) if depth.get("asks") else None

    # 1-minute klines for S (EMA), eta estimation and bar-volume fallback
    klines = _get("/api/v3/klines", {"symbol": symbol, "interval": "1m", "limit": 120})
    closes = [float(k[4]) for k in klines]
    volumes = [float(k[5]) for k in klines]

    ticker = _get("/api/v3/ticker/24hr", {"symbol": symbol})
    price = float(ticker["lastPrice"])
    prev_close = float(ticker["prevClosePrice"]) if ticker.get("prevClosePrice") else None

    return {
        "price": price,
        "bid": bid,
        "ask": ask,
        "bid_qty": bid_qty,
        "ask_qty": ask_qty,
        "closes": closes,
        "volumes": volumes,
        "tick_size": get_tick_size(symbol),
        "prev_close": prev_close,
        "source": "binance",
    }
