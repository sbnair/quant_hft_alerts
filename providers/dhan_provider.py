"""Dhan provider (Indian stocks, NSE equity).

Uses the dhanhq v2 SDK when DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN are set:
quote_data() gives 5-level market depth -> true order-book imbalance,
intraday_minute_data() gives 1-minute closes for S and eta.

Symbols are resolved to Dhan security ids via the public scrip-master CSV
(downloaded once and cached next to this file). Without credentials the
provider transparently falls back to Yahoo Finance ("SYMBOL.NS")."""
import csv
import os
import threading
import time
from datetime import datetime, timedelta

import requests

import config

_lock = threading.Lock()
_dhan = None
_dhan_failed = False
_symbol_map: dict[str, str] | None = None

SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
SCRIP_CACHE = os.path.join(os.path.dirname(__file__), "_dhan_scrip_nse_eq.csv")
NSE_TICK_SIZE = 0.05


def _get_client():
    """Lazily build the dhanhq v2 client (DhanContext + dhanhq)."""
    global _dhan, _dhan_failed
    if _dhan is not None or _dhan_failed:
        return _dhan
    if not (config.DHAN_CLIENT_ID and config.DHAN_ACCESS_TOKEN):
        _dhan_failed = True
        return None
    try:
        from dhanhq import DhanContext, dhanhq
        ctx = DhanContext(config.DHAN_CLIENT_ID, config.DHAN_ACCESS_TOKEN)
        _dhan = dhanhq(ctx)
    except Exception:
        _dhan_failed = True
    return _dhan


def _load_symbol_map() -> dict[str, str]:
    """symbol -> security_id for NSE equities, from the Dhan scrip master."""
    global _symbol_map
    with _lock:
        if _symbol_map is not None:
            return _symbol_map
        if not os.path.exists(SCRIP_CACHE):
            r = requests.get(SCRIP_MASTER_URL, timeout=60)
            r.raise_for_status()
            # Keep only NSE equity rows to shrink the cache file
            lines = r.text.splitlines()
            header = lines[0]
            keep = [header]
            for line in lines[1:]:
                if line.startswith("NSE,") and (",EQ," in line or ",EQUITY," in line):
                    keep.append(line)
            with open(SCRIP_CACHE, "w") as f:
                f.write("\n".join(keep))
        mapping = {}
        with open(SCRIP_CACHE) as f:
            reader = csv.DictReader(f)
            cols = reader.fieldnames or []
            sym_col = next((c for c in cols if "TRADING_SYMBOL" in c.upper()), None)
            id_col = next((c for c in cols if "SECURITY_ID" in c.upper()), None)
            series_col = next((c for c in cols if "SERIES" in c.upper()), None)
            for row in reader:
                if series_col and row.get(series_col, "").strip() not in ("EQ", ""):
                    continue
                if sym_col and id_col and row.get(sym_col):
                    mapping[row[sym_col].strip().upper()] = row[id_col].strip()
        _symbol_map = mapping
        return mapping


def resolve_security_id(symbol: str) -> str | None:
    try:
        return _load_symbol_map().get(symbol.upper())
    except Exception:
        return None


def _dhan_snapshot(symbol: str) -> dict | None:
    dhan = _get_client()
    if dhan is None:
        return None
    sec_id = resolve_security_id(symbol)
    if not sec_id:
        return None
    try:
        quote = dhan.quote_data(securities={"NSE_EQ": [int(sec_id)]})
        data = quote.get("data", {}).get("data", {}).get("NSE_EQ", {}).get(str(sec_id))
        if not data:
            return None
        price = float(data.get("last_price") or 0)
        depth = data.get("depth", {})
        buys = depth.get("buy", []) or []
        sells = depth.get("sell", []) or []
        bid_qty = sum(float(l.get("quantity", 0) or 0) for l in buys) or None
        ask_qty = sum(float(l.get("quantity", 0) or 0) for l in sells) or None
        bid = float(buys[0]["price"]) if buys and buys[0].get("price") else None
        ask = float(sells[0]["price"]) if sells and sells[0].get("price") else None
        prev_close = data.get("ohlc", {}).get("close") or None

        closes, volumes = [], []
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            frm = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d")
            hist = dhan.intraday_minute_data(
                security_id=str(sec_id),
                exchange_segment="NSE_EQ",
                instrument_type="EQUITY",
                from_date=frm,
                to_date=today,
            )
            hd = hist.get("data", {})
            closes = [float(c) for c in (hd.get("close") or [])][-120:]
            volumes = [float(v) for v in (hd.get("volume") or [])][-120:]
        except Exception:
            pass

        return {
            "price": price,
            "bid": bid,
            "ask": ask,
            "bid_qty": bid_qty,
            "ask_qty": ask_qty,
            "closes": closes or [price],
            "volumes": volumes,
            "tick_size": NSE_TICK_SIZE,
            "prev_close": float(prev_close) if prev_close else None,
            "source": "dhan",
        }
    except Exception:
        return None


def _yfinance_fallback(symbol: str) -> dict:
    from . import us_provider
    # Index symbols (^NSEI, ^BSESN, ...) are Yahoo-native; equities get .NS
    yf_symbol = symbol if (symbol.startswith("^") or "." in symbol) else f"{symbol}.NS"
    snap = us_provider.get_snapshot(yf_symbol)
    snap["tick_size"] = NSE_TICK_SIZE
    snap["source"] = f"yfinance({yf_symbol})"
    return snap


def get_snapshot(symbol: str) -> dict:
    if symbol.startswith("^"):  # indexes are not tradable Dhan NSE_EQ ids
        return _yfinance_fallback(symbol)
    snap = _dhan_snapshot(symbol)
    if snap is not None:
        return snap
    return _yfinance_fallback(symbol)
