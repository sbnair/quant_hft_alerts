"""
Signal engine — Python port of the volume-imbalance / uncertainty-zones model.

Based on: Pulido, Rosenbaum & Sfendourakis (2026),
"Understanding the worst-kept secret of high-frequency trading",
Finance & Stochastics 30:329-396.

Core quantities
---------------
I        = (qb - qa) / (qb + qa)          volume imbalance in [-1, 1]
S        = EMA(close, n)                  efficient-price proxy
P        = tick-grid snapped mid-price
Y        = S - P                          key state variable
barriers = P ± delta * (0.5 + eta)        uncertainty-zone boundaries
eta_hat  = Nc / (2 * Na)                  Robert & Rosenbaum (2011)
P_micro  = ((I+1)/2)*Pa + ((1-I)/2)*Pb    Stoikov (2018) micro-price

Trading rule (paper Fig. 4 — optimal I* is monotonically increasing in Y):
LONG  when I >  threshold AND Y >  y_threshold (AND near upper barrier)
SHORT when I < -threshold AND Y < -y_threshold (AND near lower barrier)
"""
from __future__ import annotations


def ema(values, period: int):
    """Exponential moving average; returns the latest EMA value."""
    if not values:
        return None
    k = 2.0 / (period + 1.0)
    e = float(values[0])
    for v in values[1:]:
        e = float(v) * k + e * (1.0 - k)
    return e


def estimate_eta(closes, default: float = 0.2) -> float:
    """eta_hat = Nc / (2*Na) — continuations vs alternations of price moves."""
    continuations, alternations = 0, 0
    prev_dir = 0
    for i in range(1, len(closes)):
        if closes[i] == closes[i - 1]:
            continue
        d = 1 if closes[i] > closes[i - 1] else -1
        if prev_dir != 0:
            if d == prev_dir:
                continuations += 1
            else:
                alternations += 1
        prev_dir = d
    if alternations == 0:
        return default
    return max(0.01, min(0.49, continuations / (2.0 * alternations)))


def imbalance_from_depth(bid_qty, ask_qty):
    """True order-book imbalance when L2 depth is available (Binance, Dhan)."""
    if bid_qty is None or ask_qty is None:
        return None
    total = float(bid_qty) + float(ask_qty)
    if total <= 0:
        return None
    return (float(bid_qty) - float(ask_qty)) / total


def imbalance_from_bars(closes, volumes):
    """Retail approximation when no depth feed exists (US via yfinance):
    qb proxy = volume of up-bars, qa proxy = volume of down-bars."""
    if not closes or not volumes or len(closes) < 3:
        return None
    buy_vol, sell_vol = 0.0, 0.0
    for i in range(1, min(len(closes), len(volumes))):
        v = float(volumes[i] or 0)
        if closes[i] > closes[i - 1]:
            buy_vol += v
        elif closes[i] < closes[i - 1]:
            sell_vol += v
    total = buy_vol + sell_vol
    if total <= 0:
        return None
    return (buy_vol - sell_vol) / total


def compute_state(snapshot: dict, params: dict | None = None) -> dict:
    """Full model state from a provider snapshot.

    snapshot: price, bid, ask, bid_qty, ask_qty, closes[], volumes[],
              tick_size, prev_close
    params:   ema_period, eta (None -> auto), imbalance_threshold,
              y_threshold_ticks, require_barrier, barrier_fraction
    """
    p = params or {}
    ema_period = int(p.get("ema_period", 5))
    imb_threshold = float(p.get("imbalance_threshold", 0.15))
    y_threshold_ticks = float(p.get("y_threshold_ticks", 0.0))
    require_barrier = bool(p.get("require_barrier", False))
    barrier_fraction = float(p.get("barrier_fraction", 0.7))

    closes = snapshot.get("closes") or []
    volumes = snapshot.get("volumes") or []
    price = snapshot.get("price")
    bid = snapshot.get("bid")
    ask = snapshot.get("ask")
    delta = float(snapshot.get("tick_size") or 0.01)

    # --- Imbalance I: real depth if we have it, tick proxy otherwise ---
    I = imbalance_from_depth(snapshot.get("bid_qty"), snapshot.get("ask_qty"))
    imbalance_source = "orderbook"
    if I is None:
        I = imbalance_from_bars(closes, volumes)
        imbalance_source = "bars"
    if I is None:
        I = 0.0
        imbalance_source = "none"

    # --- Efficient price S and grid mid-price P ---
    S = ema(closes, ema_period) if closes else price
    if bid and ask:
        raw_mid = (float(bid) + float(ask)) / 2.0
    else:
        raw_mid = float(price) if price else 0.0
    P = delta * round(raw_mid / delta) if delta > 0 else raw_mid

    Y = (S - P) if (S is not None and P is not None) else 0.0

    # --- eta and uncertainty-zone barriers ---
    eta = p.get("eta")
    eta = float(eta) if eta not in (None, "", 0) else estimate_eta(closes)
    zone_width = delta * (0.5 + eta)
    upper_barrier = P + zone_width
    lower_barrier = P - zone_width
    near_upper = Y > zone_width * barrier_fraction
    near_lower = Y < -zone_width * barrier_fraction

    # --- Stoikov micro-price ---
    if bid and ask:
        micro_price = ((I + 1.0) / 2.0) * float(ask) + ((1.0 - I) / 2.0) * float(bid)
    else:
        micro_price = price

    # --- Signal (paper Section 5 / Figure 4) ---
    y_thr = y_threshold_ticks * delta
    long_ok = I > imb_threshold and Y > y_thr
    short_ok = I < -imb_threshold and Y < -y_thr
    if require_barrier:
        long_ok = long_ok and near_upper
        short_ok = short_ok and near_lower
    signal = "LONG" if long_ok else ("SHORT" if short_ok else None)

    prev_close = snapshot.get("prev_close")
    pct_change = None
    if prev_close and price:
        pct_change = (float(price) - float(prev_close)) / float(prev_close) * 100.0

    return {
        "price": price,
        "bid": bid,
        "ask": ask,
        "imbalance": round(I, 4),
        "imbalance_source": imbalance_source,
        "S": S,
        "P": P,
        "Y": Y,
        "Y_ticks": (Y / delta) if delta > 0 else None,
        "eta": round(eta, 4),
        "delta": delta,
        "upper_barrier": upper_barrier,
        "lower_barrier": lower_barrier,
        "near_upper_barrier": near_upper,
        "near_lower_barrier": near_lower,
        "micro_price": micro_price,
        "signal": signal,
        "pct_change": round(pct_change, 3) if pct_change is not None else None,
        "ts": snapshot.get("ts"),
    }
