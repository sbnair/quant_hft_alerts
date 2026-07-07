"""Background monitor: polls market data for every active alert on its own
interval, runs the signal engine, and fires Telegram/Discord notifications
with a per-alert cooldown."""
import json
import logging
import threading
import time
from datetime import datetime, timezone

import config
import notifiers
import providers
from engine import signal_engine
from engine.models import db, Alert, Event

log = logging.getLogger("monitor")

# Latest computed state per alert id, for the live UI (/api/live)
live_state: dict[int, dict] = {}
_live_lock = threading.Lock()

_MARKET_EMOJI = {"us": "🇺🇸", "india": "🇮🇳", "crypto": "🪙"}


def _fmt(v, digits=4):
    if v is None:
        return "—"
    return f"{v:.{digits}f}" if isinstance(v, float) else str(v)


def evaluate_condition(alert: Alert, state: dict) -> tuple[bool, str]:
    """Returns (triggered, human message)."""
    price = state.get("price")
    t = alert.alert_type
    if t == "price_above" and price is not None:
        if price > alert.threshold:
            return True, f"Price {_fmt(price)} crossed ABOVE {_fmt(alert.threshold)}"
    elif t == "price_below" and price is not None:
        if price < alert.threshold:
            return True, f"Price {_fmt(price)} crossed BELOW {_fmt(alert.threshold)}"
    elif t == "pct_change":
        pc = state.get("pct_change")
        if pc is not None and abs(pc) >= alert.threshold:
            return True, f"Day change {pc:+.2f}% exceeded ±{alert.threshold}%"
    elif t in ("imbalance_long", "imbalance_short", "imbalance_any"):
        sig = state.get("signal")
        want = {"imbalance_long": ("LONG",),
                "imbalance_short": ("SHORT",),
                "imbalance_any": ("LONG", "SHORT")}[t]
        if sig in want:
            return True, (
                f"{sig} signal — I={_fmt(state['imbalance'])} "
                f"(src: {state['imbalance_source']}), "
                f"Y={_fmt(state['Y'], 5)} ({_fmt(state['Y_ticks'], 2)} ticks), "
                f"η={_fmt(state['eta'])}"
            )
    return False, ""


def build_notification(alert: Alert, state: dict, reason: str) -> tuple[str, str]:
    emoji = _MARKET_EMOJI.get(alert.market, "")
    title = f"{alert.name} — {alert.symbol} {emoji}"
    lines = [
        reason,
        "",
        f"Market: {alert.market.upper()}   Symbol: {alert.symbol}",
        f"Price: {_fmt(state.get('price'))}   Micro-price: {_fmt(state.get('micro_price'))}",
        f"Imbalance I: {_fmt(state.get('imbalance'))}   Y=S−P: {_fmt(state.get('Y'), 5)}",
        f"S (EMA): {_fmt(state.get('S'))}   P (grid mid): {_fmt(state.get('P'))}",
        f"η: {_fmt(state.get('eta'))}   Barriers: [{_fmt(state.get('lower_barrier'))}, {_fmt(state.get('upper_barrier'))}]",
        f"Signal: {state.get('signal') or '—'}",
        f"Time: {config.ist_str()}",
    ]
    return title, "\n".join(lines)


def check_alert(app, alert_id: int):
    """Fetch data, compute state, evaluate, notify. Runs inside app context."""
    with app.app_context():
        alert = db.session.get(Alert, alert_id)
        if alert is None or not alert.active:
            return
        now = datetime.now(timezone.utc)
        try:
            snapshot = providers.get_snapshot(alert.market, alert.symbol)
            state = signal_engine.compute_state(snapshot, alert.params())
            state["source"] = snapshot.get("source")
            state["market_open"] = providers.is_market_open(alert.market)
            alert.last_error = None
        except Exception as e:
            alert.last_checked = now
            alert.last_error = str(e)[:290]
            db.session.commit()
            with _live_lock:
                live_state[alert.id] = {"error": alert.last_error}
            return

        with _live_lock:
            live_state[alert.id] = state

        alert.last_checked = now
        triggered, reason = evaluate_condition(alert, state)

        in_cooldown = (
            alert.last_triggered is not None
            and (now - alert.last_triggered.replace(tzinfo=timezone.utc)
                 if alert.last_triggered.tzinfo is None else now - alert.last_triggered
                 ).total_seconds() < alert.cooldown_sec
        )

        market_gate = (
            config.RESPECT_MARKET_HOURS
            and alert.alert_type.startswith("imbalance")
            and not state["market_open"]
        )

        if triggered and not in_cooldown and not market_gate:
            title, body = build_notification(alert, state, reason)
            channels = alert.channels.split(",") if alert.channels else []
            delivery = notifiers.send(channels, title, body)
            alert.last_triggered = now
            alert.trigger_count = (alert.trigger_count or 0) + 1
            db.session.add(Event(
                alert_id=alert.id,
                alert_name=alert.name,
                market=alert.market,
                symbol=alert.symbol,
                message=reason,
                data=json.dumps({k: v for k, v in state.items()
                                 if isinstance(v, (int, float, str, bool, type(None)))}),
                delivery=json.dumps(delivery),
            ))
            log.info("ALERT FIRED [%s] %s: %s (delivery=%s)",
                     alert.id, alert.symbol, reason, delivery)
        db.session.commit()


class MonitorThread(threading.Thread):
    """One scheduler loop; per-alert checks run in short-lived workers so a
    slow provider never blocks the others."""

    def __init__(self, app):
        super().__init__(daemon=True, name="alert-monitor")
        self.app = app
        self._stop = threading.Event()
        self._inflight: set[int] = set()
        self._inflight_lock = threading.Lock()

    def stop(self):
        self._stop.set()

    def run(self):
        log.info("Alert monitor started")
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                log.exception("monitor tick failed")
            self._stop.wait(1.0)

    def _tick(self):
        with self.app.app_context():
            alerts = Alert.query.filter_by(active=True).all()
            due = []
            now = datetime.now(timezone.utc)
            for a in alerts:
                lc = a.last_checked
                if lc is not None and lc.tzinfo is None:
                    lc = lc.replace(tzinfo=timezone.utc)
                if lc is None or (now - lc).total_seconds() >= a.interval_sec:
                    due.append(a.id)

        for alert_id in due:
            with self._inflight_lock:
                if alert_id in self._inflight:
                    continue
                self._inflight.add(alert_id)

            def worker(aid=alert_id):
                try:
                    check_alert(self.app, aid)
                finally:
                    with self._inflight_lock:
                        self._inflight.discard(aid)

            threading.Thread(target=worker, daemon=True).start()


def get_live_state() -> dict:
    with _live_lock:
        return {k: dict(v) for k, v in live_state.items()}
