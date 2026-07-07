"""Symbol catalogs per market, for the UI's autocomplete.

Each entry: {"symbol": tradable id, "name": display name, "type": kind}.
- crypto: live Binance exchangeInfo (TRADING pairs), cached 12h
- india:  NSE indexes (Yahoo ^ symbols) + Dhan scrip-master equities
- us:     index/ETF/mega-cap curated list (Yahoo has no free full listing)
"""
import csv
import threading
import time

import requests

from . import dhan_provider

_lock = threading.Lock()
_cache: dict[str, tuple[float, list]] = {}
_TTL = 12 * 3600

INDIA_INDEXES = [
    {"symbol": "^NSEI", "name": "NIFTY 50 (index)", "type": "index"},
    {"symbol": "^NSEBANK", "name": "NIFTY Bank (index)", "type": "index"},
    {"symbol": "^CNXIT", "name": "NIFTY IT (index)", "type": "index"},
    {"symbol": "^NSMIDCP", "name": "NIFTY Midcap 100 (index)", "type": "index"},
    {"symbol": "^BSESN", "name": "BSE SENSEX (index)", "type": "index"},
    {"symbol": "^INDIAVIX", "name": "India VIX (index)", "type": "index"},
]

US_INDEXES_ETFS = [
    {"symbol": "^GSPC", "name": "S&P 500 (index)", "type": "index"},
    {"symbol": "^DJI", "name": "Dow Jones Industrial (index)", "type": "index"},
    {"symbol": "^IXIC", "name": "NASDAQ Composite (index)", "type": "index"},
    {"symbol": "^NDX", "name": "NASDAQ 100 (index)", "type": "index"},
    {"symbol": "^RUT", "name": "Russell 2000 (index)", "type": "index"},
    {"symbol": "^VIX", "name": "CBOE Volatility (index)", "type": "index"},
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF", "type": "etf"},
    {"symbol": "QQQ", "name": "Invesco QQQ (Nasdaq-100) ETF", "type": "etf"},
    {"symbol": "DIA", "name": "SPDR Dow Jones ETF", "type": "etf"},
    {"symbol": "IWM", "name": "iShares Russell 2000 ETF", "type": "etf"},
    {"symbol": "VTI", "name": "Vanguard Total Market ETF", "type": "etf"},
    {"symbol": "VOO", "name": "Vanguard S&P 500 ETF", "type": "etf"},
    {"symbol": "GLD", "name": "SPDR Gold Shares ETF", "type": "etf"},
    {"symbol": "SLV", "name": "iShares Silver Trust ETF", "type": "etf"},
    {"symbol": "USO", "name": "United States Oil Fund ETF", "type": "etf"},
    {"symbol": "TLT", "name": "iShares 20+yr Treasury ETF", "type": "etf"},
    {"symbol": "HYG", "name": "iShares High Yield Corp ETF", "type": "etf"},
    {"symbol": "XLF", "name": "Financial Select Sector ETF", "type": "etf"},
    {"symbol": "XLK", "name": "Technology Select Sector ETF", "type": "etf"},
    {"symbol": "XLE", "name": "Energy Select Sector ETF", "type": "etf"},
    {"symbol": "XLV", "name": "Health Care Select Sector ETF", "type": "etf"},
    {"symbol": "XLI", "name": "Industrial Select Sector ETF", "type": "etf"},
    {"symbol": "XLY", "name": "Consumer Discretionary ETF", "type": "etf"},
    {"symbol": "XLP", "name": "Consumer Staples ETF", "type": "etf"},
    {"symbol": "SMH", "name": "VanEck Semiconductor ETF", "type": "etf"},
    {"symbol": "ARKK", "name": "ARK Innovation ETF", "type": "etf"},
]

US_STOCKS = [
    ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet A"),
    ("AMZN", "Amazon"), ("NVDA", "NVIDIA"), ("META", "Meta Platforms"),
    ("TSLA", "Tesla"), ("BRK-B", "Berkshire Hathaway B"), ("AVGO", "Broadcom"),
    ("JPM", "JPMorgan Chase"), ("V", "Visa"), ("MA", "Mastercard"),
    ("UNH", "UnitedHealth"), ("XOM", "Exxon Mobil"), ("CVX", "Chevron"),
    ("LLY", "Eli Lilly"), ("JNJ", "Johnson & Johnson"), ("PFE", "Pfizer"),
    ("MRK", "Merck"), ("ABBV", "AbbVie"), ("WMT", "Walmart"),
    ("COST", "Costco"), ("HD", "Home Depot"), ("PG", "Procter & Gamble"),
    ("KO", "Coca-Cola"), ("PEP", "PepsiCo"), ("MCD", "McDonald's"),
    ("NKE", "Nike"), ("SBUX", "Starbucks"), ("DIS", "Walt Disney"),
    ("NFLX", "Netflix"), ("CRM", "Salesforce"), ("ORCL", "Oracle"),
    ("ADBE", "Adobe"), ("AMD", "AMD"), ("INTC", "Intel"),
    ("QCOM", "Qualcomm"), ("TXN", "Texas Instruments"), ("MU", "Micron"),
    ("AMAT", "Applied Materials"), ("TSM", "TSMC (ADR)"), ("ASML", "ASML (ADR)"),
    ("IBM", "IBM"), ("CSCO", "Cisco"), ("NOW", "ServiceNow"),
    ("UBER", "Uber"), ("ABNB", "Airbnb"), ("SHOP", "Shopify"),
    ("PYPL", "PayPal"), ("SQ", "Block"), ("COIN", "Coinbase"),
    ("MSTR", "MicroStrategy"), ("PLTR", "Palantir"), ("SNOW", "Snowflake"),
    ("PANW", "Palo Alto Networks"), ("CRWD", "CrowdStrike"),
    ("BAC", "Bank of America"), ("WFC", "Wells Fargo"), ("GS", "Goldman Sachs"),
    ("MS", "Morgan Stanley"), ("C", "Citigroup"), ("BLK", "BlackRock"),
    ("T", "AT&T"), ("VZ", "Verizon"), ("TMUS", "T-Mobile"),
    ("BA", "Boeing"), ("CAT", "Caterpillar"), ("DE", "Deere"),
    ("GE", "GE Aerospace"), ("LMT", "Lockheed Martin"), ("RTX", "RTX"),
    ("UPS", "UPS"), ("FDX", "FedEx"), ("F", "Ford"), ("GM", "General Motors"),
    ("RIVN", "Rivian"), ("LCID", "Lucid"), ("SOFI", "SoFi"),
    ("HOOD", "Robinhood"), ("GME", "GameStop"), ("AMC", "AMC Entertainment"),
]

_QUOTE_PRIORITY = ("USDT", "USDC", "FDUSD", "BTC", "ETH", "BNB", "INR")


def _crypto_symbols() -> list[dict]:
    import config
    r = requests.get(f"{config.BINANCE_BASE_URL.rstrip('/')}/api/v3/exchangeInfo",
                     timeout=20)
    r.raise_for_status()
    out = []
    for s in r.json().get("symbols", []):
        if s.get("status") != "TRADING":
            continue
        quote = s.get("quoteAsset", "")
        if quote not in _QUOTE_PRIORITY:
            continue
        out.append({
            "symbol": s["symbol"],
            "name": f"{s['baseAsset']}/{quote}",
            "type": "crypto",
            "_prio": _QUOTE_PRIORITY.index(quote),
        })
    out.sort(key=lambda x: (x["_prio"], x["symbol"]))
    for x in out:
        x.pop("_prio", None)
    return out


def _india_symbols() -> list[dict]:
    out = list(INDIA_INDEXES)
    try:
        # Ensure the scrip master is downloaded/cached, then read names
        dhan_provider._load_symbol_map()
        with open(dhan_provider.SCRIP_CACHE) as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("SEM_SERIES", "").strip() != "EQ":
                    continue
                sym = row.get("SEM_TRADING_SYMBOL", "").strip()
                name = (row.get("SEM_CUSTOM_SYMBOL") or
                        row.get("SM_SYMBOL_NAME") or sym).strip()
                if sym:
                    out.append({"symbol": sym, "name": name, "type": "stock"})
    except Exception:
        # Offline / no scrip master: still offer the most-traded names
        for sym, name in [
            ("RELIANCE", "Reliance Industries"), ("TCS", "Tata Consultancy"),
            ("HDFCBANK", "HDFC Bank"), ("ICICIBANK", "ICICI Bank"),
            ("INFY", "Infosys"), ("SBIN", "State Bank of India"),
            ("BHARTIARTL", "Bharti Airtel"), ("ITC", "ITC"),
            ("LT", "Larsen & Toubro"), ("HINDUNILVR", "Hindustan Unilever"),
            ("TATAMOTORS", "Tata Motors"), ("ADANIENT", "Adani Enterprises"),
            ("AXISBANK", "Axis Bank"), ("KOTAKBANK", "Kotak Mahindra Bank"),
            ("WIPRO", "Wipro"), ("MARUTI", "Maruti Suzuki"),
        ]:
            out.append({"symbol": sym, "name": name, "type": "stock"})
    return out


def _us_symbols() -> list[dict]:
    return US_INDEXES_ETFS + [
        {"symbol": s, "name": n, "type": "stock"} for s, n in US_STOCKS
    ]


def get_symbols(market: str) -> list[dict]:
    with _lock:
        hit = _cache.get(market)
        if hit and time.time() - hit[0] < _TTL:
            return hit[1]
    if market == "crypto":
        syms = _crypto_symbols()
    elif market == "india":
        syms = _india_symbols()
    elif market == "us":
        syms = _us_symbols()
    else:
        raise ValueError(f"Unknown market: {market}")
    with _lock:
        _cache[market] = (time.time(), syms)
    return syms
