"""SQLAlchemy models: Alert definitions and triggered Events."""
import json
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

ALERT_TYPES = (
    "price_above",      # last price crosses above threshold
    "price_below",      # last price crosses below threshold
    "pct_change",       # |day % change| exceeds threshold
    "imbalance_long",   # paper LONG signal (I > thr, Y > 0)
    "imbalance_short",  # paper SHORT signal (I < -thr, Y < 0)
    "imbalance_any",    # either direction
)

MARKETS = ("us", "crypto", "india")


def utcnow():
    return datetime.now(timezone.utc)


def iso_utc(dt):
    """ISO string with explicit UTC offset (SQLite returns naive datetimes;
    without the offset the browser would misread them as local time)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


class Alert(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    market = db.Column(db.String(10), nullable=False)          # us / crypto / india
    symbol = db.Column(db.String(40), nullable=False)
    alert_type = db.Column(db.String(30), nullable=False)
    threshold = db.Column(db.Float, default=0.0)               # price level or pct

    # Model parameters (imbalance alerts)
    imbalance_threshold = db.Column(db.Float, default=0.15)
    y_threshold_ticks = db.Column(db.Float, default=0.0)
    eta = db.Column(db.Float, nullable=True)                   # None -> auto-estimate
    ema_period = db.Column(db.Integer, default=5)
    require_barrier = db.Column(db.Boolean, default=False)

    channels = db.Column(db.String(60), default="telegram,discord")
    interval_sec = db.Column(db.Integer, default=30)
    cooldown_sec = db.Column(db.Integer, default=300)
    active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)
    last_checked = db.Column(db.DateTime(timezone=True), nullable=True)
    last_triggered = db.Column(db.DateTime(timezone=True), nullable=True)
    trigger_count = db.Column(db.Integer, default=0)
    last_error = db.Column(db.String(300), nullable=True)

    def params(self):
        return {
            "imbalance_threshold": self.imbalance_threshold,
            "y_threshold_ticks": self.y_threshold_ticks,
            "eta": self.eta,
            "ema_period": self.ema_period,
            "require_barrier": self.require_barrier,
        }

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "market": self.market,
            "symbol": self.symbol,
            "alert_type": self.alert_type,
            "threshold": self.threshold,
            "imbalance_threshold": self.imbalance_threshold,
            "y_threshold_ticks": self.y_threshold_ticks,
            "eta": self.eta,
            "ema_period": self.ema_period,
            "require_barrier": self.require_barrier,
            "channels": self.channels.split(",") if self.channels else [],
            "interval_sec": self.interval_sec,
            "cooldown_sec": self.cooldown_sec,
            "active": self.active,
            "created_at": iso_utc(self.created_at),
            "last_checked": iso_utc(self.last_checked),
            "last_triggered": iso_utc(self.last_triggered),
            "trigger_count": self.trigger_count,
            "last_error": self.last_error,
        }


class Event(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    alert_id = db.Column(db.Integer, db.ForeignKey("alert.id"), nullable=True)
    alert_name = db.Column(db.String(120))
    market = db.Column(db.String(10))
    symbol = db.Column(db.String(40))
    message = db.Column(db.Text)
    data = db.Column(db.Text)  # JSON snapshot of the model state at trigger
    delivery = db.Column(db.Text)  # JSON per-channel delivery result
    created_at = db.Column(db.DateTime(timezone=True), default=utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "alert_id": self.alert_id,
            "alert_name": self.alert_name,
            "market": self.market,
            "symbol": self.symbol,
            "message": self.message,
            "data": json.loads(self.data) if self.data else None,
            "delivery": json.loads(self.delivery) if self.delivery else None,
            "created_at": iso_utc(self.created_at),
        }
