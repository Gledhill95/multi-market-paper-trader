import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
import yfinance as yf

DB_PATH = Path("paper_trader.sqlite3")
STARTING_CASH = 2500.0
FEE_RATE = 0.001  # illustrative 0.10% per side; change to match a real venue
MAX_POSITION_FRACTION = 0.20
RISK_FRACTION = 0.005
MAX_DAILY_LOSS_FRACTION = 0.015  # conservative guardrail for future live integration; paper prototype only

ASSETS = {
    "Crypto": ["BTC-USD", "ETH-USD"],
    "ASX shares": ["BHP.AX", "CBA.AX", "CSL.AX"],
    "US shares": ["AAPL", "MSFT", "NVDA"],
    "Forex": ["EURUSD=X", "GBPUSD=X", "USDJPY=X"],
    "Metals": ["GC=F", "SI=F"],
}
SYMBOLS = [s for group in ASSETS.values() for s in group]

BOTS = {
    "Steady trend": "trend",
    "Income-style mean reversion": "mean_reversion",
    "Aggressive breakout": "breakout",
    "Experimental momentum": "momentum",
}

def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""CREATE TABLE IF NOT EXISTS portfolios (
        bot TEXT PRIMARY KEY, cash REAL NOT NULL, initial_cash REAL NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS positions (
        bot TEXT NOT NULL, symbol TEXT NOT NULL, qty REAL NOT NULL,
        entry_price REAL NOT NULL, invested_aud REAL NOT NULL,
        opened_at TEXT NOT NULL, PRIMARY KEY(bot, symbol))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
        bot TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
        price REAL NOT NULL, notional_aud REAL NOT NULL, pnl_aud REAL,
        reason TEXT NOT NULL)""")
    for bot in BOTS:
        conn.execute("INSERT OR IGNORE INTO portfolios(bot,cash,initial_cash) VALUES(?,?,?)",
                     (bot, STARTING_CASH, STARTING_CASH))
    conn.commit()
    return conn

def fetch_history(symbol):
    try:
        df = yf.Ticker(symbol).history(period="9mo", interval="1d", auto_adjust=True)
        if df is None or df.empty or "Close" not in df:
            return None
        close = df["Close"].dropna()
        if len(close) < 60:
            return None
        return close
    except Exception:
        return None

def indicators(close):
    out = pd.DataFrame({"close": close})
    out["sma20"] = out.close.rolling(20).mean()
    out["sma50"] = out.close.rolling(50).mean()
    delta = out.close.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, pd.NA)
    out["rsi"] = 100 - (100 / (1 + rs))
    out["high20_prev"] = out.close.shift(1).rolling(20).max()
    out["low10_prev"] = out.close.shift(1).rolling(10).min()
    out["ret5"] = out.close.pct_change(5)
    out["ret20"] = out.close.pct_change(20)
    out["atr_pct"] = out.close.pct_change().abs().rolling(14).mean() * 2
    return out

def signal_for(strategy, row):
    if strategy == "trend":
        buy = row.close > row.sma50 and row.sma20 > row.sma50
        sell = row.close < row.sma20
    elif strategy == "mean_reversion":
        buy = row.rsi < 35
        sell = row.rsi > 60
    elif strategy == "breakout":
        buy = row.close > row.high20_prev
        sell = row.close < row.low10_prev
    else:
        buy = row.ret5 > 0.01 and row.ret20 > 0
        sell = row.ret5 < 0 or row.ret20 < 0
    if pd.isna(row.close):
        return "HOLD", "No valid price"
    if buy:
        return "BUY", strategy.replace("_", " ") + " entry condition"
    if sell:
        return "SELL", strategy.replace("_", " ") + " exit condition"
    return "HOLD", "No strategy trigger"

def portfolio_state(conn, bot, prices):
    cash = conn.execute("SELECT cash FROM portfolios WHERE bot=?", (bot,)).fetchone()[0]
    positions = conn.execute("SELECT symbol,qty,entry_price,invested_aud FROM positions WHERE bot=?", (bot,)).fetchall()
    market_value = 0.0
    unrealized = 0.0
    rows = []
    for symbol, qty, entry, invested in positions:
        price = prices.get(symbol)
        if price is None:
            value = invested
            pnl = 0.0
        else:
            value = invested * (price / entry)
            pnl = value - invested
        market_value += value
        unrealized += pnl
        rows.append({"Symbol": symbol, "Entry": entry, "Latest": price, "Invested (AUD)": invested,
                     "Value (AUD)": value, "Unrealised P/L (AUD)": pnl})
    return cash, market_value, unrealized, rows

def execute_scan(conn, histories):
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    latest_prices = {s: float(h.iloc[-1]) for s, h in histories.items() if h is not None and len(h)}
    messages = []
    for bot, strategy in BOTS.items():
        cash = conn.execute("SELECT cash FROM portfolios WHERE bot=?", (bot,)).fetchone()[0]
        for symbol, close in histories.items():
            if close is None:
                continue
            ind = indicators(close)
            row = ind.iloc[-1]
            if row.isna().any():
                continue
            action, reason = signal_for(strategy, row)
            pos = conn.execute("SELECT qty,entry_price,invested_aud FROM positions WHERE bot=? AND symbol=?",
                               (bot, symbol)).fetchone()
            price = float(row.close)
            if action == "BUY" and pos is None:
                equity = cash
                risk_budget = equity * RISK_FRACTION
                stop_fraction = max(float(row.atr_pct), 0.01)
                notional = min(cash, equity * MAX_POSITION_FRACTION, risk_budget / stop_fraction)
                notional = max(0.0, notional)
                fee = notional * FEE_RATE
                if notional > 10 and cash >= notional + fee:
                    qty = notional / price
                    conn.execute("UPDATE portfolios SET cash=cash-? WHERE bot=?", (notional + fee, bot))
                    conn.execute("INSERT INTO positions VALUES(?,?,?,?,?,?)",
                                 (bot, symbol, qty, price, notional, now))
                    conn.execute("INSERT INTO trades(timestamp,bot,symbol,side,price,notional_aud,pnl_aud,reason) VALUES(?,?,?,?,?,?,?,?)",
                                 (now, bot, symbol, "BUY", price, notional, -fee, reason))
                    cash -= notional + fee
                    messages.append(f"{bot}: BUY {symbol} (paper)")
            elif action == "SELL" and pos is not None:
                qty, entry, invested = pos
                gross = invested * (price / entry)
                fee = gross * FEE_RATE
                pnl = gross - invested - fee - (invested * FEE_RATE)
                conn.execute("UPDATE portfolios SET cash=cash+? WHERE bot=?", (gross - fee, bot))
                conn.execute("DELETE FROM positions WHERE bot=? AND symbol=?", (bot, symbol))
                conn.execute("INSERT INTO trades(timestamp,bot,symbol,side,price,notional_aud,pnl_aud,reason) VALUES(?,?,?,?,?,?,?,?)",
                             (now, bot, symbol, "SELL", price, gross, pnl, reason))
                cash += gross - fee
                messages.append(f"{bot}: SELL {symbol} (paper)")
    conn.commit()
    return latest_prices, messages

st.set_page_config(page_title="Multi-market Paper Trader", page_icon="📈", layout="wide")
st.title("Multi-market paper trading lab")
st.caption("Simulation only • No broker connection • No real orders • Prices and signals are experimental")

conn = connect()
with st.sidebar:
    st.header("Controls")
    st.write("Four independent strategies, each starting with A$2,500.")
    st.warning("A scan only runs when you press the button. Free hosting may sleep; this is not a 24/7 execution service.")
    run_scan = st.button("Run market scan & paper trade", type="primary", use_container_width=True)
    st.caption("Data: Yahoo Finance via yfinance. Delays, gaps, symbol availability and data terms may vary.")
    if st.button("Reset all simulated portfolios", use_container_width=True):
        conn.execute("DELETE FROM positions")
        conn.execute("DELETE FROM trades")
        conn.execute("UPDATE portfolios SET cash=?, initial_cash=?", (STARTING_CASH, STARTING_CASH))
        conn.commit()
        st.success("Paper portfolios reset.")
        st.rerun()

if "histories" not in st.session_state:
    st.session_state.histories = None
if run_scan:
    with st.spinner("Downloading daily market data and evaluating strategies..."):
        histories = {s: fetch_history(s) for s in SYMBOLS}
        st.session_state.histories = histories
        prices, messages = execute_scan(conn, histories)
        st.session_state.prices = prices
        st.session_state.last_scan = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        st.session_state.scan_messages = messages

histories = st.session_state.histories
if histories is None:
    st.info("Press **Run market scan & paper trade** to fetch data, evaluate signals and make simulated trades.")
    histories = {}

prices = st.session_state.get("prices", {s: float(h.iloc[-1]) for s,h in histories.items() if h is not None and len(h)})
if st.session_state.get("last_scan"):
    st.caption(f"Last scan: {st.session_state.last_scan}")

summary = []
for bot in BOTS:
    cash, value, unrealized, _ = portfolio_state(conn, bot, prices)
    realized = conn.execute("SELECT COALESCE(SUM(pnl_aud),0) FROM trades WHERE bot=? AND side='SELL'", (bot,)).fetchone()[0]
    equity = cash + value
    summary.append({"Bot": bot, "Cash (AUD)": cash, "Positions (AUD)": value,
                    "Equity (AUD)": equity, "Total return": (equity / STARTING_CASH - 1) * 100,
                    "Unrealised P/L": unrealized, "Realised P/L": realized})
st.subheader("Strategy scoreboard")
st.dataframe(pd.DataFrame(summary).style.format({
    "Cash (AUD)": "A${:,.2f}", "Positions (AUD)": "A${:,.2f}", "Equity (AUD)": "A${:,.2f}",
    "Total return": "{:+.2f}%", "Unrealised P/L": "A${:+,.2f}", "Realised P/L": "A${:+,.2f}"
}), use_container_width=True, hide_index=True)

if st.session_state.get("scan_messages"):
    st.subheader("Latest simulated orders")
    for message in st.session_state.scan_messages:
        st.write("• " + message)
    if not st.session_state.scan_messages:
        st.write("No new orders on the latest scan.")

tabs = st.tabs(list(BOTS.keys()))
for tab, bot in zip(tabs, BOTS):
    with tab:
        cash, value, unrealized, positions = portfolio_state(conn, bot, prices)
        st.write(f"**Strategy:** `{BOTS[bot]}`")
        st.metric("Portfolio equity", f"A${cash+value:,.2f}", f"{(cash+value-STARTING_CASH):+,.2f} AUD")
        if positions:
            st.dataframe(pd.DataFrame(positions), use_container_width=True, hide_index=True)
        else:
            st.write("No open positions.")
        trades = pd.read_sql_query("SELECT timestamp,symbol,side,price,notional_aud,pnl_aud,reason FROM trades WHERE bot=? ORDER BY id DESC LIMIT 30",
                                   conn, params=(bot,))
        st.caption("Most recent 30 paper trades")
        st.dataframe(trades, use_container_width=True, hide_index=True)

st.subheader("Market coverage")
for category, symbols in ASSETS.items():
    cols = st.columns(len(symbols))
    for col, symbol in zip(cols, symbols):
        with col:
            price = prices.get(symbol)
            col.metric(symbol, f"{price:,.4f}" if price is not None else "No data")
    st.caption(category)

st.divider()
st.caption("Important: This is a prototype for learning and strategy evaluation, not financial advice. It uses daily closing data, simplified position sizing and illustrative fees. It does not model spreads, slippage, dividends, corporate actions, financing, tax, market impact or broker-specific execution. Historical or simulated performance does not predict future results. Do not connect real funds or API trading keys.")
