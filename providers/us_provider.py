"""US stocks provider via yfinance (no API key needed).

Yahoo gives no L2 depth, so the order-book imbalance is approximated the
same way the original MT5 EA does on retail feeds: qb proxy = volume of
up bars, qa proxy = volume of down bars over the recent 1-minute window
(handled downstream by signal_engine.imbalance_from_bars)."""
import threading
import time

import yfinance as yf

_lock = threading.Lock()
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 15  # yahoo rate-limits aggressively; short cache smooths polling

US_TICK_SIZE = 0.01


def get_snapshot(symbol: str) -> dict:
    symbol = symbol.upper()
    with _lock:
        hit = _cache.get(symbol)
        if hit and time.time() - hit[0] < _CACHE_TTL:
            return dict(hit[1])

    t = yf.Ticker(symbol)
    hist = t.history(period="2d", interval="1m", auto_adjust=False)
    if hist is None or hist.empty:
        raise RuntimeError(f"No Yahoo Finance data for {symbol}")

    closes = [float(x) for x in hist["Close"].dropna().tolist()][-120:]
    volumes = [float(x) for x in hist["Volume"].fillna(0).tolist()][-120:]
    price = closes[-1]

    bid = ask = bid_qty = ask_qty = None
    prev_close = None
    try:
        fi = t.fast_info
        prev_close = float(fi.get("previousClose") or 0) or None
        last = float(fi.get("lastPrice") or 0)
        if last > 0:
            price = last
    except Exception:
        pass
    try:
        info = t.info
        bid = float(info.get("bid") or 0) or None
        ask = float(info.get("ask") or 0) or None
        bid_qty = float(info.get("bidSize") or 0) or None
        ask_qty = float(info.get("askSize") or 0) or None
        if prev_close is None:
            prev_close = float(info.get("previousClose") or 0) or None
    except Exception:
        pass

    snap = {
        "price": price,
        "bid": bid,
        "ask": ask,
        "bid_qty": bid_qty,
        "ask_qty": ask_qty,
        "closes": closes,
        "volumes": volumes,
        "tick_size": US_TICK_SIZE,
        "prev_close": prev_close,
        "source": "yfinance",
    }
    with _lock:
        _cache[symbol] = (time.time(), dict(snap))
    return snap
