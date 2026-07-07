"""Quant Alert — multi-market alert app (US / Crypto / India).

Flask UI + REST API, background monitor thread, Telegram/Discord delivery.
Signal engine implements the volume-imbalance / uncertainty-zones model of
Pulido, Rosenbaum & Sfendourakis (2026).
"""
import logging
import os

from flask import Flask, jsonify, render_template, request

import config
import notifiers
import providers
from engine import signal_engine
from engine.models import db, Alert, Event, ALERT_TYPES, MARKETS
from engine.monitor import MonitorThread, get_live_state
from engine.scanner import ScannerThread, get_scanner_state

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = Flask(__name__)
app.config["SECRET_KEY"] = config.FLASK_SECRET_KEY
app.config["SQLALCHEMY_DATABASE_URI"] = config.DATABASE_URI
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

with app.app_context():
    db.create_all()


# ────────────────────────── UI ──────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ─────────────────────── REST API ───────────────────────
@app.route("/api/status")
def api_status():
    st = config.integrations_status()
    st["markets_open"] = {m: providers.is_market_open(m) for m in MARKETS}
    return jsonify(st)


@app.route("/api/alerts", methods=["GET"])
def list_alerts():
    alerts = Alert.query.order_by(Alert.created_at.desc()).all()
    return jsonify([a.to_dict() for a in alerts])


def _apply_alert_fields(alert: Alert, data: dict):
    if data.get("market") not in MARKETS:
        raise ValueError(f"market must be one of {MARKETS}")
    if data.get("alert_type") not in ALERT_TYPES:
        raise ValueError(f"alert_type must be one of {ALERT_TYPES}")
    if not data.get("symbol", "").strip():
        raise ValueError("symbol is required")

    alert.market = data["market"]
    alert.symbol = data["symbol"].strip().upper()
    alert.alert_type = data["alert_type"]
    alert.name = data.get("name", "").strip() or f"{alert.symbol} {alert.alert_type}"
    alert.threshold = float(data.get("threshold") or 0)
    alert.imbalance_threshold = float(data.get("imbalance_threshold") or 0.15)
    alert.y_threshold_ticks = float(data.get("y_threshold_ticks") or 0)
    eta = data.get("eta")
    alert.eta = float(eta) if eta not in (None, "", "auto", 0) else None
    alert.ema_period = int(data.get("ema_period") or 5)
    alert.require_barrier = bool(data.get("require_barrier"))
    channels = data.get("channels") or ["telegram", "discord"]
    if isinstance(channels, str):
        channels = [c.strip() for c in channels.split(",") if c.strip()]
    alert.channels = ",".join(channels)
    alert.interval_sec = max(5, int(data.get("interval_sec") or 30))
    alert.cooldown_sec = max(0, int(data.get("cooldown_sec") or 300))
    if "active" in data:
        alert.active = bool(data["active"])


@app.route("/api/alerts", methods=["POST"])
def create_alert():
    data = request.get_json(force=True)
    alert = Alert()
    try:
        _apply_alert_fields(alert, data)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    db.session.add(alert)
    db.session.commit()
    return jsonify(alert.to_dict()), 201


@app.route("/api/alerts/<int:alert_id>", methods=["PUT"])
def update_alert(alert_id):
    alert = db.session.get(Alert, alert_id)
    if alert is None:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True)
    # Partial update: merge onto existing values
    merged = alert.to_dict()
    merged.update(data)
    try:
        _apply_alert_fields(alert, merged)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    db.session.commit()
    return jsonify(alert.to_dict())


@app.route("/api/alerts/<int:alert_id>/toggle", methods=["POST"])
def toggle_alert(alert_id):
    alert = db.session.get(Alert, alert_id)
    if alert is None:
        return jsonify({"error": "not found"}), 404
    alert.active = not alert.active
    db.session.commit()
    return jsonify(alert.to_dict())


@app.route("/api/alerts/<int:alert_id>", methods=["DELETE"])
def delete_alert(alert_id):
    alert = db.session.get(Alert, alert_id)
    if alert is None:
        return jsonify({"error": "not found"}), 404
    db.session.delete(alert)
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/symbols")
def api_symbols():
    """Symbol catalog for the selected market (UI autocomplete)."""
    from providers import symbols as symbol_catalog
    market = request.args.get("market", "crypto")
    if market not in MARKETS:
        return jsonify({"error": f"market must be one of {MARKETS}"}), 400
    try:
        return jsonify(symbol_catalog.get_symbols(market))
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/live")
def api_live():
    state = get_live_state()
    valid_ids = {a.id for a in Alert.query.with_entities(Alert.id).all()}
    return jsonify({k: v for k, v in state.items() if int(k) in valid_ids})


@app.route("/api/scanner")
def api_scanner():
    """Latest built-in scanner results per market (top-N + indexes)."""
    return jsonify({
        "config": {
            "enabled": config.SCANNER_ENABLED,
            "top_n": config.SCANNER_TOP_N,
            "interval_sec": config.SCANNER_INTERVAL_SEC,
            "imbalance_threshold": config.SCANNER_IMBALANCE_THRESHOLD,
            "index_move_pct": config.SCANNER_INDEX_MOVE_PCT,
        },
        "markets": get_scanner_state(),
    })


@app.route("/api/events")
def api_events():
    limit = min(int(request.args.get("limit", 50)), 200)
    events = Event.query.order_by(Event.created_at.desc()).limit(limit).all()
    return jsonify([e.to_dict() for e in events])


@app.route("/api/preview")
def api_preview():
    """Fetch a symbol once and show the full model state (used by the
    'Preview signal' button before saving an alert)."""
    market = request.args.get("market", "crypto")
    symbol = request.args.get("symbol", "").strip()
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    try:
        snap = providers.get_snapshot(market, symbol)
        state = signal_engine.compute_state(snap, {
            "imbalance_threshold": float(request.args.get("imbalance_threshold", 0.15)),
            "y_threshold_ticks": float(request.args.get("y_threshold_ticks", 0)),
            "ema_period": int(request.args.get("ema_period", 5)),
            "eta": request.args.get("eta") or None,
        })
        state["source"] = snap.get("source")
        state["market_open"] = providers.is_market_open(market)
        return jsonify(state)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@app.route("/api/test_notification", methods=["POST"])
def test_notification():
    data = request.get_json(force=True) or {}
    channels = data.get("channels") or ["telegram", "discord"]
    result = notifiers.send(
        channels,
        "Quant Alert — test notification",
        "If you can read this, your alert channel is wired up correctly. ✅",
    )
    return jsonify(result)


# ─────────────────────── startup ────────────────────────
monitor = MonitorThread(app)
scanner = ScannerThread(app)

if __name__ == "__main__":
    # Avoid double monitor threads under the Werkzeug reloader
    if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
        monitor.start()
        scanner.start()
    app.run(host=config.FLASK_HOST, port=config.FLASK_PORT,
            debug=False, use_reloader=False)
