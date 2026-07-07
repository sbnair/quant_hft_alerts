/* Quant Alert dashboard — polls the REST API and renders live state. */
const $ = (s) => document.querySelector(s);

const SYMBOL_HINTS = {
  crypto: "e.g. BTCUSDT, ETHUSDT, SOLUSDT",
  us: "e.g. AAPL, TSLA, NVDA, SPY",
  india: "e.g. RELIANCE, TCS, INFY, HDFCBANK",
};

const TYPE_LABELS = {
  imbalance_any: "Imbalance ±",
  imbalance_long: "Imbalance LONG",
  imbalance_short: "Imbalance SHORT",
  price_above: "Price >",
  price_below: "Price <",
  pct_change: "Δ% day",
};

/* ── symbol autocomplete: catalog per market ── */
const MAX_DATALIST = 4000;
let symbolNames = {}; // SYMBOL -> display name (for auto alert name)
const symbolCache = {}; // market -> [{symbol,name,type}]

async function loadSymbols(market) {
  const hint = $("#symbolHint");
  hint.textContent = "Loading symbols…";
  try {
    if (!symbolCache[market]) {
      const r = await fetch(`/api/symbols?market=${market}`);
      if (!r.ok) throw new Error((await r.json()).error || r.status);
      symbolCache[market] = await r.json();
    }
    const syms = symbolCache[market];
    symbolNames = {};
    const dl = $("#symbolList");
    dl.innerHTML = "";
    const frag = document.createDocumentFragment();
    syms.slice(0, MAX_DATALIST).forEach((s) => {
      symbolNames[s.symbol] = s.name;
      const opt = document.createElement("option");
      opt.value = s.symbol;
      opt.label = `${s.name}${s.type === "index" ? "" : ` (${s.type})`}`;
      frag.appendChild(opt);
    });
    dl.appendChild(frag);
    const indexes = syms.filter((s) => s.type === "index").length;
    hint.textContent = `${syms.length.toLocaleString()} symbols loaded` +
      (indexes ? ` (incl. ${indexes} indexes)` : "") + ` — type to search. ${SYMBOL_HINTS[market]}`;
  } catch (e) {
    hint.textContent = `Symbol list unavailable (${e.message}) — type manually. ${SYMBOL_HINTS[market]}`;
  }
}

/* ── auto alert name: "<SYMBOL> · <Name> · <type label>" ── */
let nameEdited = false;
$("#alertName").addEventListener("input", () => {
  nameEdited = $("#alertName").value.trim() !== "";
});

function updateAutoName() {
  if (nameEdited) return;
  const sym = $("#symbol").value.trim().toUpperCase();
  if (!sym) { $("#alertName").value = ""; return; }
  const type = TYPE_LABELS[$("#alertType").value] || "";
  const pretty = symbolNames[sym];
  $("#alertName").value = pretty ? `${sym} · ${pretty} · ${type}` : `${sym} · ${type}`;
}

$("#market").addEventListener("change", (e) => {
  $("#symbol").value = "";
  updateAutoName();
  loadSymbols(e.target.value);
});

$("#symbol").addEventListener("input", updateAutoName);
$("#symbol").addEventListener("change", updateAutoName);

$("#alertType").addEventListener("change", (e) => {
  const imb = e.target.value.startsWith("imbalance");
  $("#modelParams").open = imb;
  updateAutoName();
});

/* All times are displayed in IST regardless of the browser's timezone */
function fmtIST(iso, timeOnly = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  const base = { timeZone: "Asia/Kolkata", hour12: true };
  const s = timeOnly
    ? d.toLocaleTimeString("en-IN", base)
    : d.toLocaleString("en-IN", {
        ...base, day: "2-digit", month: "short", year: "numeric",
        hour: "2-digit", minute: "2-digit", second: "2-digit",
      });
  return `${s} IST`;
}

function fmt(v, d = 4) {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") {
    const abs = Math.abs(v);
    const digits = abs >= 1000 ? 2 : d;
    return v.toLocaleString(undefined, { maximumFractionDigits: digits });
  }
  return v;
}

/* ── status bar ── */
async function loadStatus() {
  const st = await (await fetch("/api/status")).json();
  const items = [
    ["Binance", true],
    ["Dhan", st.dhan],
    ["Telegram", st.telegram],
    ["Discord", st.discord],
    ["US mkt " + (st.markets_open.us ? "OPEN" : "closed"), st.markets_open.us],
    ["NSE " + (st.markets_open.india ? "OPEN" : "closed"), st.markets_open.india],
  ];
  $("#statusBar").innerHTML = items
    .map(([label, ok]) => `<span class="badge ${ok ? "ok" : "warn"}">${label}</span>`)
    .join("");
}

/* ── alerts table ── */
let alertsCache = [];

async function loadAlerts() {
  alertsCache = await (await fetch("/api/alerts")).json();
  const live = await (await fetch("/api/live")).json();
  renderAlerts(live);
}

function renderAlerts(live) {
  const tbody = $("#alertsTable tbody");
  $("#alertCount").textContent = `(${alertsCache.length})`;
  $("#noAlerts").hidden = alertsCache.length > 0;
  tbody.innerHTML = alertsCache
    .map((a) => {
      const s = live[a.id] || {};
      const sig = s.signal
        ? `<span class="sig-${s.signal.toLowerCase()}">${s.signal}</span>`
        : "—";
      const status = s.error
        ? `<span class="pill err" title="${s.error}">error</span>`
        : a.active
        ? `<span class="pill on">active</span>`
        : `<span class="pill off">paused</span>`;
      const thr = a.alert_type.startsWith("imbalance") ? "" : ` ${fmt(a.threshold)}`;
      return `<tr>
        <td>${a.name}</td>
        <td>${a.market.toUpperCase()}</td>
        <td><b>${a.symbol}</b></td>
        <td>${TYPE_LABELS[a.alert_type] || a.alert_type}${thr}</td>
        <td class="num">${fmt(s.price)}</td>
        <td class="num">${fmt(s.imbalance)}</td>
        <td class="num">${fmt(s.Y_ticks, 2)}</td>
        <td class="num">${fmt(s.eta)}</td>
        <td>${sig}</td>
        <td class="num">${a.trigger_count || 0}</td>
        <td>${status}</td>
        <td>
          <button class="btn small" onclick="toggleAlert(${a.id})">${a.active ? "Pause" : "Resume"}</button>
          <button class="btn small danger" onclick="deleteAlert(${a.id})">✕</button>
        </td>
      </tr>`;
    })
    .join("");
}

window.toggleAlert = async (id) => {
  await fetch(`/api/alerts/${id}/toggle`, { method: "POST" });
  loadAlerts();
};

window.deleteAlert = async (id) => {
  if (!confirm("Delete this alert?")) return;
  await fetch(`/api/alerts/${id}`, { method: "DELETE" });
  loadAlerts();
};

/* ── events feed ── */
async function loadEvents() {
  const events = await (await fetch("/api/events?limit=50")).json();
  $("#noEvents").hidden = events.length > 0;
  $("#eventsFeed").innerHTML = events
    .map(
      (e) => `<div class="event">
        <div class="ehead">
          <span class="ename">${e.alert_name} — ${e.symbol} (${(e.market || "").toUpperCase()})</span>
          <span class="etime">${fmtIST(e.created_at)}</span>
        </div>
        <div class="emsg">${e.message}</div>
      </div>`
    )
    .join("");
}

/* ── form ── */
$("#alertForm").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const f = new FormData(ev.target);
  const channels = [];
  if (f.get("ch_telegram")) channels.push("telegram");
  if (f.get("ch_discord")) channels.push("discord");
  const body = {
    market: f.get("market"),
    symbol: f.get("symbol"),
    name: f.get("name"),
    alert_type: f.get("alert_type"),
    threshold: f.get("threshold"),
    imbalance_threshold: f.get("imbalance_threshold"),
    y_threshold_ticks: f.get("y_threshold_ticks"),
    eta: f.get("eta"),
    ema_period: f.get("ema_period"),
    require_barrier: !!f.get("require_barrier"),
    channels,
    interval_sec: f.get("interval_sec"),
    cooldown_sec: f.get("cooldown_sec"),
  };
  const r = await fetch("/api/alerts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.ok) {
    ev.target.reset();
    nameEdited = false;
    $("#previewOut").hidden = true;
    loadSymbols($("#market").value);
    loadAlerts();
  } else {
    alert((await r.json()).error || "Failed to create alert");
  }
});

$("#previewBtn").addEventListener("click", async () => {
  const f = new FormData($("#alertForm"));
  const out = $("#previewOut");
  out.hidden = false;
  out.textContent = "Fetching…";
  const params = new URLSearchParams({
    market: f.get("market"),
    symbol: f.get("symbol"),
    imbalance_threshold: f.get("imbalance_threshold") || 0.15,
    y_threshold_ticks: f.get("y_threshold_ticks") || 0,
    ema_period: f.get("ema_period") || 5,
    eta: f.get("eta") || "",
  });
  const r = await fetch(`/api/preview?${params}`);
  const s = await r.json();
  if (s.error) {
    out.textContent = "Error: " + s.error;
    return;
  }
  out.textContent =
    `source: ${s.source}   market open: ${s.market_open}\n` +
    `price: ${fmt(s.price)}   bid/ask: ${fmt(s.bid)} / ${fmt(s.ask)}\n` +
    `I (imbalance): ${fmt(s.imbalance)}   [from ${s.imbalance_source}]\n` +
    `S (EMA proxy): ${fmt(s.S, 6)}   P (grid mid): ${fmt(s.P, 6)}\n` +
    `Y = S − P: ${fmt(s.Y, 6)}  (${fmt(s.Y_ticks, 2)} ticks)\n` +
    `η: ${fmt(s.eta)}   δ (tick): ${fmt(s.delta, 8)}\n` +
    `barriers: [${fmt(s.lower_barrier, 6)}, ${fmt(s.upper_barrier, 6)}]\n` +
    `near barrier: up=${s.near_upper_barrier} dn=${s.near_lower_barrier}\n` +
    `micro-price: ${fmt(s.micro_price, 6)}\n` +
    `SIGNAL: ${s.signal || "none"}`;
});

$("#testNotifBtn").addEventListener("click", async () => {
  const f = new FormData($("#alertForm"));
  const channels = [];
  if (f.get("ch_telegram")) channels.push("telegram");
  if (f.get("ch_discord")) channels.push("discord");
  const r = await fetch("/api/test_notification", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channels }),
  });
  const res = await r.json();
  alert(
    Object.entries(res)
      .map(([ch, x]) => `${ch}: ${x.ok ? "✅ sent" : "❌ " + x.error}`)
      .join("\n") || "No channels selected"
  );
});

/* ── built-in market scanner ── */
let scanMarket = "crypto";
let scannerData = null;

document.querySelectorAll("#scanTabs .tab").forEach((btn) =>
  btn.addEventListener("click", () => {
    document.querySelectorAll("#scanTabs .tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    scanMarket = btn.dataset.m;
    renderScanner();
  })
);

async function loadScanner() {
  try {
    scannerData = await (await fetch("/api/scanner")).json();
    renderScanner();
  } catch (e) { /* transient */ }
}

function renderScanner() {
  if (!scannerData) return;
  const cfg = scannerData.config || {};
  const m = (scannerData.markets || {})[scanMarket];
  const tbody = $("#scannerTable tbody");
  if (!m || !m.rows || !m.rows.length) {
    tbody.innerHTML = "";
    $("#scanEmpty").hidden = false;
    $("#scanEmpty").textContent = m && m.error ? `⏸ ${m.error}` : "Scanner warming up…";
    $("#scanMeta").textContent = "";
    return;
  }
  $("#scanEmpty").hidden = !m.error;
  if (m.error) $("#scanEmpty").textContent = `⏸ ${m.error}`;
  const when = m.ts ? fmtIST(m.ts, true) : "";
  $("#scanMeta").textContent =
    `(top ${cfg.top_n} by volume + indexes · |I| ≥ ${cfg.imbalance_threshold} · last scan ${when})`;

  const rows = [...m.rows].sort(
    (a, b) =>
      (b.signal ? 1 : 0) - (a.signal ? 1 : 0) ||
      (b.kind === "index" ? 1 : 0) - (a.kind === "index" ? 1 : 0) ||
      Math.abs(b.imbalance ?? 0) - Math.abs(a.imbalance ?? 0)
  );
  tbody.innerHTML = rows
    .map((r, i) => {
      const sig = r.signal
        ? `<span class="sig-${r.signal.toLowerCase()}">${r.signal}</span>` : "—";
      const pctCls = r.pct > 0 ? "pct-up" : r.pct < 0 ? "pct-down" : "";
      const pct = r.pct === null || r.pct === undefined ? "—"
        : `<span class="${pctCls}">${r.pct > 0 ? "+" : ""}${r.pct.toFixed(2)}%</span>`;
      const kindTag = r.kind === "index" ? " 📈" : "";
      return `<tr class="${r.signal ? "has-signal" : ""} kind-${r.kind}">
        <td class="num">${i + 1}</td>
        <td><b>${r.symbol}</b>${kindTag}</td>
        <td>${r.name || ""}</td>
        <td class="num">${fmt(r.price)}</td>
        <td class="num">${pct}</td>
        <td class="num">${fmt(r.imbalance)}</td>
        <td class="num">${fmt(r.Y_ticks, 2)}</td>
        <td>${sig}</td>
        <td><button class="btn small" title="Create alert for ${r.symbol}"
             onclick="prefillAlert('${scanMarket}','${r.symbol}')">＋ alert</button></td>
      </tr>`;
    })
    .join("");
}

window.prefillAlert = async (market, symbol) => {
  $("#market").value = market;
  await loadSymbols(market);
  $("#symbol").value = symbol;
  nameEdited = false;
  updateAutoName();
  $("#formPanel").scrollIntoView({ behavior: "smooth" });
};

/* ── polling loops ── */
loadStatus();
loadAlerts();
loadEvents();
loadSymbols($("#market").value);
loadScanner();
setInterval(loadAlerts, 4000);
setInterval(loadEvents, 8000);
setInterval(loadScanner, 15000);
setInterval(loadStatus, 60000);
