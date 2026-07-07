"""Built-in market scanner.

Continuously scans the default watchlists (top-50 high-volume names + famous
indexes per market) without the user creating any alerts:

- crypto: two batched Binance calls (24h tickers + bookTicker) cover all 50
  pairs; symbols whose top-of-book |I| passes the threshold get a full
  depth+klines check before signalling.
- india : one batched Dhan quote_data call for all NIFTY 50 constituents
  (real 5-level depth), plus a Yahoo batch for NIFTY 50 / Bank NIFTY indexes.
- us    : one batched yfinance download for all symbols + indexes; imbalance
  uses the up/down-bar volume proxy.

Signals fire Telegram/Discord notifications with a per-symbol cooldown and
are logged as Events (alert_name "Scanner"). Results are exposed to the UI
via get_scanner_state() → /api/scanner.
"""
import json
import logging
import threading
import time
from datetime import datetime, timezone

import requests

import config
import notifiers
import providers
from providers import watchlists, binance_provider, dhan_provider
from engine import signal_engine
from engine.models import db, Event

log = logging.getLogger("scanner")

_state_lock = threading.Lock()
_scanner_state: dict[str, dict] = {}   # market -> {ts, rows, error}
_last_notified: dict[tuple, float] = {}  # (market, symbol) -> unix ts

_MARKET_EMOJI = {"us": "🇺🇸", "india": "🇮🇳", "crypto": "🪙"}


def get_scanner_state() -> dict:
    with _state_lock:
        return {m: dict(v) for m, v in _scanner_state.items()}


def _set_state(market: str, rows: list, error: str | None = None):
    with _state_lock:
        _scanner_state[market] = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "rows": rows,
            "error": error,
            "market_open": providers.is_market_open(market),
        }


def _row(symbol, name, price=None, pct=None, imbalance=None, signal=None,
         kind="stock", source="", extra=None):
    r = {"symbol": symbol, "name": name, "price": price, "pct": pct,
         "imbalance": imbalance, "signal": signal, "kind": kind,
         "source": source}
    if extra:
        r.update(extra)
    return r


class ScannerThread(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True, name="market-scanner")
        self.app = app
        self._stop = threading.Event()
        self._last_scan = {"crypto": 0.0, "us": 0.0, "india": 0.0}

    def stop(self):
        self._stop.set()

    def run(self):
        if not config.SCANNER_ENABLED:
            log.info("Scanner disabled (SCANNER_ENABLED=false)")
            return
        log.info("Market scanner started (top %d + indexes, every %ds)",
                 config.SCANNER_TOP_N, config.SCANNER_INTERVAL_SEC)
        while not self._stop.is_set():
            for market in ("crypto", "us", "india"):
                if self._stop.is_set():
                    break
                if time.time() - self._last_scan[market] < config.SCANNER_INTERVAL_SEC:
                    continue
                if (config.RESPECT_MARKET_HOURS and market != "crypto"
                        and not providers.is_market_open(market)):
                    self._last_scan[market] = time.time()
                    _set_state(market, self._stale_rows(market),
                               error="market closed — scan paused")
                    continue
                try:
                    scan = getattr(self, f"_scan_{market}")
                    rows = scan()
                    _set_state(market, rows)
                    self._notify_signals(market, rows)
                except Exception as e:
                    log.exception("scan %s failed", market)
                    _set_state(market, self._stale_rows(market), error=str(e)[:200])
                self._last_scan[market] = time.time()
            self._stop.wait(5.0)

    @staticmethod
    def _stale_rows(market):
        with _state_lock:
            return _scanner_state.get(market, {}).get("rows", [])

    # ── crypto ─────────────────────────────────────────────
    def _scan_crypto(self) -> list:
        base = config.BINANCE_BASE_URL.rstrip("/")
        watch = watchlists.get_watchlist("crypto", config.SCANNER_TOP_N)["stocks"]
        symbols = {s for s, _ in watch}

        tickers = requests.get(f"{base}/api/v3/ticker/24hr", timeout=20).json()
        tick_map = {t["symbol"]: t for t in tickers if t["symbol"] in symbols}
        books = requests.get(f"{base}/api/v3/ticker/bookTicker", timeout=20).json()
        book_map = {b["symbol"]: b for b in books if b["symbol"] in symbols}

        rows = []
        for sym, name in watch:
            t, b = tick_map.get(sym), book_map.get(sym)
            if not t:
                continue
            price = float(t["lastPrice"])
            pct = float(t.get("priceChangePercent") or 0)
            I = None
            if b:
                I = signal_engine.imbalance_from_depth(b.get("bidQty"), b.get("askQty"))
            row = _row(sym, name, price, pct,
                       round(I, 4) if I is not None else None,
                       kind="crypto", source="binance")
            # strong top-of-book imbalance → confirm with full depth+klines
            if I is not None and abs(I) >= config.SCANNER_IMBALANCE_THRESHOLD:
                try:
                    snap = binance_provider.get_snapshot(sym)
                    state = signal_engine.compute_state(
                        snap, {"imbalance_threshold": config.SCANNER_IMBALANCE_THRESHOLD})
                    row["imbalance"] = state["imbalance"]
                    row["signal"] = state["signal"]
                    row["Y_ticks"] = round(state["Y_ticks"], 2) if state["Y_ticks"] else None
                except Exception:
                    pass
            rows.append(row)
        return rows

    # ── india ──────────────────────────────────────────────
    def _scan_india(self) -> list:
        wl = watchlists.get_watchlist("india", config.SCANNER_TOP_N)
        rows = self._india_indexes(wl["indexes"])

        dhan = dhan_provider._get_client()
        id_map = {}
        for sym, _ in wl["stocks"]:
            sec_id = dhan_provider.resolve_security_id(sym)
            if sec_id:
                id_map[str(sec_id)] = sym

        if dhan is not None and id_map:
            quote = dhan.quote_data(
                securities={"NSE_EQ": [int(i) for i in id_map]})
            data = (quote.get("data", {}).get("data", {}) or {}).get("NSE_EQ", {}) or {}
            for sec_id, payload in data.items():
                sym = id_map.get(str(sec_id))
                if not sym or not payload:
                    continue
                price = float(payload.get("last_price") or 0)
                prev = (payload.get("ohlc") or {}).get("close")
                pct = ((price - float(prev)) / float(prev) * 100) if prev else None
                depth = payload.get("depth", {}) or {}
                qb = sum(float(l.get("quantity", 0) or 0) for l in depth.get("buy", []) or [])
                qa = sum(float(l.get("quantity", 0) or 0) for l in depth.get("sell", []) or [])
                I = signal_engine.imbalance_from_depth(qb or None, qa or None)
                row = _row(sym, sym, price, round(pct, 2) if pct is not None else None,
                           round(I, 4) if I is not None else None,
                           kind="stock", source="dhan")
                if I is not None and abs(I) >= config.SCANNER_IMBALANCE_THRESHOLD:
                    try:
                        snap = dhan_provider.get_snapshot(sym)
                        state = signal_engine.compute_state(
                            snap, {"imbalance_threshold": config.SCANNER_IMBALANCE_THRESHOLD})
                        row["signal"] = state["signal"]
                        row["Y_ticks"] = round(state["Y_ticks"], 2) if state["Y_ticks"] else None
                    except Exception:
                        pass
                rows.append(row)
        else:
            # No Dhan: batch the NIFTY names through Yahoo (.NS)
            rows += self._yahoo_batch(
                [(f"{s}.NS", s) for s, _ in wl["stocks"]], kind="stock",
                strip_suffix=".NS")
        return rows

    def _india_indexes(self, indexes) -> list:
        return self._yahoo_batch(indexes, kind="index")

    # ── us ─────────────────────────────────────────────────
    def _scan_us(self) -> list:
        wl = watchlists.get_watchlist("us", config.SCANNER_TOP_N)
        pairs = wl["indexes"] + wl["stocks"]
        return self._yahoo_batch(pairs, kind=None)

    # ── shared: batched yahoo download ─────────────────────
    def _yahoo_batch(self, pairs, kind=None, strip_suffix=None) -> list:
        """pairs: [(yahoo_symbol, display_name)]. One yf.download for all."""
        import yfinance as yf
        if not pairs:
            return []
        yf_syms = [p[0] for p in pairs]
        data = yf.download(yf_syms, period="2d", interval="1m",
                           group_by="ticker", progress=False, threads=True,
                           auto_adjust=False)
        rows = []
        for yf_sym, name in pairs:
            try:
                df = data[yf_sym] if len(yf_syms) > 1 else data
                closes_s = df["Close"].dropna()
                if closes_s.empty:
                    continue
                vols_s = df["Volume"].reindex(closes_s.index).fillna(0)
                dates = closes_s.index.normalize()
                last_day = dates[-1]
                today_mask = dates == last_day
                closes = [float(x) for x in closes_s[today_mask].tolist()][-120:]
                volumes = [float(x) for x in vols_s[today_mask].tolist()][-120:]
                prev_closes = closes_s[~today_mask]
                prev_close = float(prev_closes.iloc[-1]) if len(prev_closes) else None
                price = closes[-1]
                pct = ((price - prev_close) / prev_close * 100) if prev_close else None

                snap = {"price": price, "closes": closes, "volumes": volumes,
                        "tick_size": 0.05 if strip_suffix == ".NS" else 0.01,
                        "prev_close": prev_close}
                state = signal_engine.compute_state(
                    snap, {"imbalance_threshold": config.SCANNER_IMBALANCE_THRESHOLD})
                display = yf_sym[:-len(strip_suffix)] if (
                    strip_suffix and yf_sym.endswith(strip_suffix)) else yf_sym
                k = kind or ("index" if yf_sym.startswith("^") else "stock")
                rows.append(_row(
                    display, name, round(price, 4),
                    round(pct, 2) if pct is not None else None,
                    state["imbalance"] if state["imbalance_source"] != "none" else None,
                    state["signal"] if k != "index" else None,
                    kind=k, source="yfinance",
                    extra={"Y_ticks": round(state["Y_ticks"], 2) if state["Y_ticks"] else None}))
            except Exception:
                continue
        return rows

    # ── notifications ──────────────────────────────────────
    def _notify_signals(self, market: str, rows: list):
        if not config.SCANNER_NOTIFY:
            return
        now = time.time()
        channels = config.SCANNER_CHANNELS
        for row in rows:
            fire_reason = None
            if row.get("signal"):
                fire_reason = (
                    f"{row['signal']} signal — I={row.get('imbalance')}"
                    + (f", Y={row.get('Y_ticks')} ticks" if row.get("Y_ticks") is not None else "")
                )
            elif (row.get("kind") == "index" and row.get("pct") is not None
                    and abs(row["pct"]) >= config.SCANNER_INDEX_MOVE_PCT):
                fire_reason = f"Index move {row['pct']:+.2f}% (≥ ±{config.SCANNER_INDEX_MOVE_PCT}%)"
            if not fire_reason:
                continue
            key = (market, row["symbol"])
            if now - _last_notified.get(key, 0) < config.SCANNER_COOLDOWN_SEC:
                continue
            _last_notified[key] = now
            emoji = _MARKET_EMOJI.get(market, "")
            title = f"Scanner: {row['symbol']} ({row['name']}) {emoji}"
            body = (
                f"{fire_reason}\n\n"
                f"Market: {market.upper()}   Price: {row.get('price')}"
                + (f"   Day: {row['pct']:+.2f}%" if row.get("pct") is not None else "")
                + f"\nSource: {row.get('source')}   "
                f"Watchlist: top-{config.SCANNER_TOP_N} + indexes"
                f"\nTime: {config.ist_str()}"
            )
            delivery = notifiers.send(channels, title, body)
            log.info("SCANNER FIRED %s %s: %s (delivery=%s)",
                     market, row["symbol"], fire_reason, delivery)
            with self.app.app_context():
                db.session.add(Event(
                    alert_id=None, alert_name="Scanner",
                    market=market, symbol=row["symbol"],
                    message=fire_reason,
                    data=json.dumps(row),
                    delivery=json.dumps(delivery),
                ))
                db.session.commit()
