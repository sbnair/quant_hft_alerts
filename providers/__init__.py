"""Market data providers. Each exposes get_snapshot(symbol) -> dict with:
price, bid, ask, bid_qty, ask_qty, closes[], volumes[], tick_size,
prev_close, ts, source
"""
from datetime import datetime, time as dtime, timezone, timedelta

from . import binance_provider, dhan_provider, us_provider

_PROVIDERS = {
    "crypto": binance_provider,
    "india": dhan_provider,
    "us": us_provider,
}


def get_snapshot(market: str, symbol: str) -> dict:
    provider = _PROVIDERS.get(market)
    if provider is None:
        raise ValueError(f"Unknown market: {market}")
    snap = provider.get_snapshot(symbol)
    snap["market"] = market
    snap["symbol"] = symbol
    snap.setdefault("ts", datetime.now(timezone.utc).isoformat())
    return snap


def is_market_open(market: str) -> bool:
    """Coarse session check. Crypto is 24/7; US 9:30-16:00 ET Mon-Fri;
    India 9:15-15:30 IST Mon-Fri. DST for the US is approximated via
    fixed UTC offsets checked against both EST and EDT windows."""
    if market == "crypto":
        return True
    now_utc = datetime.now(timezone.utc)
    if market == "india":
        now_ist = now_utc + timedelta(hours=5, minutes=30)
        if now_ist.weekday() >= 5:
            return False
        return dtime(9, 15) <= now_ist.time() <= dtime(15, 30)
    if market == "us":
        for offset in (-4, -5):  # EDT / EST
            local = now_utc + timedelta(hours=offset)
            if local.weekday() < 5 and dtime(9, 30) <= local.time() <= dtime(16, 0):
                return True
        return False
    return True
