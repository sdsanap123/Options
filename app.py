import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
from datetime import datetime
import os
import sys

# Ensure local directories are in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.tickers import ALL_CATEGORIES
from utils.data_fetcher import analyze_ticker, fetch_batch_intraday_data
from utils.stock_utils import load_equity_data
from utils.ai_analyzer import score_signal, get_news_sentiment

# Set Streamlit page config
st.set_page_config(
    page_title="Intraday Fast Options Scanner",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom Styling (Rich dark theme, glassmorphic headers, status badges)
st.markdown("""
<style>
    .reportview-container {
        background: #0f1115;
    }
    .main-header {
        font-size: 2.2rem;
        font-weight: 800;
        text-align: center;
        background: linear-gradient(90deg, #ff4b4b, #29b6f6);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 0.5rem;
    }
    .sub-header {
        text-align: center;
        color: #8a99ad;
        font-size: 1rem;
        margin-bottom: 2rem;
    }
    .signal-card-long {
        background: rgba(40, 167, 69, 0.1);
        border-left: 5px solid #28a745;
        padding: 1.2rem;
        border-radius: 8px;
        margin-bottom: 1rem;
    }
    .signal-card-short {
        background: rgba(220, 53, 69, 0.1);
        border-left: 5px solid #dc3545;
        padding: 1.2rem;
        border-radius: 8px;
        margin-bottom: 1rem;
    }
    .metric-value {
        font-size: 1.5rem;
        font-weight: 700;
        color: #ffffff;
    }
    .metric-label {
        font-size: 0.85rem;
        color: #8a99ad;
    }
    .vol-breakout {
        color: #ffc107;
        font-weight: bold;
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 1rem;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 0.5rem 1.5rem;
        font-size: 1rem;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

# App Header
st.markdown('<div class="main-header">⚡ INTRADAY OPTION & BREAKOUT SCANNER ⚡</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">Anticipation + Trigger Strategy: Real-Time VWAP + 9 EMA + Supertrend (10, 1.2)</div>', unsafe_allow_html=True)


# Session state initialization
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = []
if 'last_scan_time' not in st.session_state:
    st.session_state.last_scan_time = "Never"
if 'scan_results' not in st.session_state:
    st.session_state.scan_results = []
if 'all_ticker_metrics' not in st.session_state:
    st.session_state.all_ticker_metrics = []
if 'running' not in st.session_state:
    st.session_state.running = False
if 'scan_params' not in st.session_state:
    st.session_state.scan_params = None
if 'groq_api_key' not in st.session_state:
    st.session_state.groq_api_key = os.getenv("GROQ_API_KEY", "")

# Sidebar Configuration
st.sidebar.markdown("### ⚙️ Scanner Settings")

# ── AI Settings ───────────────────────────────────────────────────────────────
st.sidebar.markdown("### 🤖 AI Settings (Groq)")
groq_key_input = st.sidebar.text_input(
    "Groq API Key",
    value=st.session_state.groq_api_key,
    type="password",
    help="Get a free key at console.groq.com"
)
if groq_key_input:
    st.session_state.groq_api_key = groq_key_input
    os.environ["GROQ_API_KEY"] = groq_key_input

ai_enabled = st.sidebar.checkbox(
    "🧠 Enable AI Analysis",
    value=bool(st.session_state.groq_api_key),
    help="Score each signal with Groq AI (0–100 confidence) and filter by news sentiment."
)
if ai_enabled and not st.session_state.groq_api_key:
    st.sidebar.warning("⚠️ Enter a Groq API key above to enable AI.")
    ai_enabled = False

min_ai_score = st.sidebar.slider(
    "Min AI Confidence Score",
    min_value=0, max_value=100, value=50, step=5,
    disabled=not ai_enabled,
    help="Only show signals with AI score ≥ this value."
) if ai_enabled else 0

block_negative_news = st.sidebar.checkbox(
    "🚫 Block Negative-News Signals",
    value=True,
    disabled=not ai_enabled,
    help="Suppress signals where Groq detects strongly negative news."
) if ai_enabled else False

st.sidebar.markdown("---")

# 1. Ticker Source Selection
ticker_source = st.sidebar.selectbox(
    "Select Ticker List",
    options=["Nifty 50", "Bank Nifty", "High Beta / Options Active", "All NSE Stocks", "Custom List"],
    index=2
)

if ticker_source == "Custom List":
    custom_input = st.sidebar.text_area(
        "Enter Tickers (comma-separated)",
        value="RELIANCE, HDFCBANK, TATAMOTORS, SBIN"
    )
    tickers = [t.strip().upper() for t in custom_input.split(",") if t.strip()]
    # Add NSE suffix if missing
    tickers = [t + ".NS" if not t.endswith(".NS") and "." not in t else t for t in tickers]
elif ticker_source == "All NSE Stocks":
    try:
        equity_df = load_equity_data()
        if not equity_df.empty:
            tickers = [f"{sym}.NS" for sym in equity_df['SYMBOL'].tolist()]
        else:
            tickers = ALL_CATEGORIES["Nifty 50"] # Fallback
            st.sidebar.error("Could not load NSE database. Defaulting to Nifty 50.")
    except Exception as e:
        tickers = ALL_CATEGORIES["Nifty 50"] # Fallback
        st.sidebar.error(f"Error loading NSE database: {e}")
else:
    tickers = ALL_CATEGORIES[ticker_source]

# 2. Timeframe Selection
timeframe_label = st.sidebar.radio(
    "Select Timeframe",
    options=["2-Minute (Ultra-Fast)", "5-Minute (Recommended)"],
    index=1
)
timeframe = "2m" if "2" in timeframe_label else "5m"

# 3. Setup parameters
st.sidebar.markdown("---")
st.sidebar.markdown("### 🎯 Criteria Thresholds")
vol_mult = st.sidebar.slider("Volume Breakout Multiplier", min_value=1.0, max_value=4.0, value=1.5, step=0.1)
st_mult = st.sidebar.slider("Supertrend Multiplier", min_value=1.0, max_value=3.0, value=1.2, step=0.1)

# Add Supertrend Flip Lookback slider
st_lookback = st.sidebar.slider(
    "Supertrend Flip Lookback (candles)",
    min_value=0,
    max_value=20,
    value=0,
    help="0 means Supertrend is ALREADY Bullish/Bearish (Trend State Filter). >0 checks for fresh flip within N candles."
)

# Add Volume Bypass checkbox (highly useful off-market)
ignore_vol = st.sidebar.checkbox(
    "Bypass Volume Filter",
    value=False,
    help="Useful for off-market testing or when trading volume is dry."
)

rr_ratio = st.sidebar.selectbox("Risk-to-Reward Ratio Target", options=["1:1.5", "1:2.0", "1:2.5"], index=0)
rr_factor = 1.5 if "1.5" in rr_ratio else (2.0 if "2.0" in rr_ratio else 2.5)

# Refresh Mode
st.sidebar.markdown("---")
auto_refresh = st.sidebar.checkbox("🔄 Enable Auto-Refresh", value=False)
refresh_interval = st.sidebar.slider("Refresh Interval (seconds)", min_value=10, max_value=120, value=30, step=5)

# Main Scan Trigger Button
col_btn1, col_btn2 = st.sidebar.columns(2)
with col_btn1:
    scan_clicked = st.button("🔍 Scan Now", width='stretch')
with col_btn2:
    clear_clicked = st.button("🧹 Clear Logs", width='stretch')

if clear_clicked:
    st.session_state.scan_results = []
    st.session_state.all_ticker_metrics = []
    st.session_state.last_scan_time = "Never"
    st.session_state.scan_params = None
    st.rerun()

# Run scan function
def run_scan():
    found_signals = []
    all_metrics = []
    total = len(tickers)

    my_bar = st.progress(0, text="Fetching batch data from yfinance...")
    
    # Batch fetch all tickers at once
    batch_data, err_msg = fetch_batch_intraday_data(tickers, interval=timeframe, period="5d")

    if err_msg:
        my_bar.progress(0, text=f"❌ Error: {err_msg}")
        st.error(f"Data Fetching Failed: {err_msg}")
        st.session_state.running = False
        return

    for idx, ticker in enumerate(tickers):
        symbol_name = ticker.replace(".NS", "")
        # Scale progress between 10% and 100%
        progress_val = 0.1 + 0.9 * ((idx + 1) / total)
        my_bar.progress(progress_val, text=f"Analyzing {symbol_name} ({idx+1}/{total})")

        pre_fetched = batch_data.get(ticker)
        res = analyze_ticker(
            ticker, 
            interval=timeframe, 
            period="5d", 
            st_lookback=st_lookback, 
            ignore_volume=ignore_vol,
            pre_fetched_df=pre_fetched,
            st_period=10,
            st_multiplier=st_mult,
            min_vol_ratio=vol_mult
        )

        if res:
            if res['setup_triggered']:
                if ignore_vol or res['volume_ratio'] >= vol_mult:
                    # Recalculate target with dynamic risk ratio
                    risk = res['risk']
                    close = res['current_price']
                    res['target_custom'] = close + (rr_factor * risk) if res['direction'] == 'LONG' else close - (rr_factor * risk)

                    # ── AI: News Sentiment ──────────────────────────────────────
                    if ai_enabled:
                        my_bar.progress((idx + 1) / total, text=f"🤖 AI: News for {symbol_name}...")
                        news = get_news_sentiment(ticker)
                        res['ai_news'] = news

                        # Block if strongly negative news
                        if block_negative_news and not news.get('should_trade', True):
                            res['ai_blocked'] = True
                            res['ai_block_reason'] = f"Blocked: {news.get('sentiment')} news — {news.get('summary', '')}"
                        else:
                            res['ai_blocked'] = False

                        # ── AI: Signal Scoring ──────────────────────────────────
                        my_bar.progress((idx + 1) / total, text=f"🤖 AI: Scoring {symbol_name}...")
                        ai_score = score_signal(res)
                        res['ai_score'] = ai_score

                        # Filter by minimum AI score
                        if ai_score['score'] < min_ai_score:
                            res['ai_blocked'] = True
                            res['ai_block_reason'] = res.get('ai_block_reason', '') or f"Score {ai_score['score']} below threshold {min_ai_score}."
                    else:
                        res['ai_news'] = None
                        res['ai_score'] = None
                        res['ai_blocked'] = False

                    if not res.get('ai_blocked', False):
                        found_signals.append(res)

            # ── Metrics table entry ─────────────────────────────────────────────
            ai_col = ""
            if ai_enabled and res.get('ai_score'):
                s = res['ai_score']
                ai_col = f"{s['score']}/100 ({s['grade']})"
            elif ai_enabled and res.get('ai_blocked'):
                ai_col = "🚫 Blocked"

            all_metrics.append({
                "Symbol": symbol_name,
                "Price (₹)": f"₹{res['current_price']:.2f}",
                "VWAP": f"₹{res['vwap']:.2f}" if res['vwap'] else "N/A",
                "9 EMA": f"₹{res['ema_9']:.2f}" if res['ema_9'] else "N/A",
                "Volume Ratio": f"{res['volume_ratio']:.2f}x" if res['vol_sma20'] > 0 else "N/A",
                "Direction": res['direction'] if res['setup_triggered'] else "N/A",
                "Setup Triggered": "✅ Yes" if res['setup_triggered'] else "❌ No",
                "AI Score": ai_col if ai_col else "—",
            })
        else:
            all_metrics.append({
                "Symbol": symbol_name,
                "Price (₹)": "N/A",
                "VWAP": "N/A",
                "9 EMA": "N/A",
                "Volume Ratio": "N/A",
                "Direction": "N/A",
                "Setup Triggered": "❌ No (No Data)",
                "AI Score": "—",
            })

    my_bar.empty()

    # Sort signals by AI score descending (highest conviction first)
    if ai_enabled:
        found_signals.sort(key=lambda s: s.get('ai_score', {}).get('score', 0) if s.get('ai_score') else 0, reverse=True)

    st.session_state.scan_results = found_signals
    st.session_state.all_ticker_metrics = all_metrics
    st.session_state.last_scan_time = datetime.now().strftime("%H:%M:%S")
    st.session_state.scan_params = {
        'timeframe': timeframe,
        'ticker_source': ticker_source,
        'tickers_count': len(tickers),
        'vol_mult': vol_mult,
        'st_lookback': st_lookback,
        'ignore_vol': ignore_vol,
        'rr_ratio': rr_ratio,
        'rr_factor': rr_factor,
        'ai_enabled': ai_enabled,
    }

# Handle Trigger
if scan_clicked or (auto_refresh and not st.session_state.running):
    st.session_state.running = True
    run_scan()
    st.session_state.running = False

# ── Reusable Breakout Chart Renderer ──────────────────────────────────────────
def render_breakout_chart(sig_data: dict):
    """Renders a clean interactive Plotly chart with candlesticks, VWAP, and VWAP ±0.3% bands."""
    df = sig_data['df']
    symbol = sig_data['symbol'].replace('.NS', '')

    fig = go.Figure()

    # Candlesticks
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
        name="Price"
    ))

    # VWAP (solid orange)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['VWAP'],
        line=dict(color='#ff9800', width=2),
        name="VWAP",
        text=[f"VWAP: ₹{v:.2f}" for v in df['VWAP']],
        hoverinfo='text'
    ))

    # VWAP +0.1% band (upper - cyan)
    vwap_upper_01 = df['VWAP'] * 1.001
    fig.add_trace(go.Scatter(
        x=df.index, y=vwap_upper_01,
        line=dict(color='#00e5ff', width=1.2, dash='dash'),
        opacity=0.75,
        name="VWAP +0.1%",
        text=[f"VWAP +0.1%: ₹{v:.2f}" for v in vwap_upper_01],
        hoverinfo='text'
    ))

    # VWAP -0.1% band (lower - cyan)
    vwap_lower_01 = df['VWAP'] * 0.999
    fig.add_trace(go.Scatter(
        x=df.index, y=vwap_lower_01,
        line=dict(color='#00e5ff', width=1.2, dash='dash'),
        opacity=0.75,
        name="VWAP -0.1%",
        text=[f"VWAP -0.1%: ₹{v:.2f}" for v in vwap_lower_01],
        hoverinfo='text'
    ))

    # VWAP +0.3% band (upper - orange dot)
    vwap_upper = df['VWAP'] * 1.003
    fig.add_trace(go.Scatter(
        x=df.index, y=vwap_upper,
        line=dict(color='#ff9800', width=1, dash='dot'),
        opacity=0.55,
        name="VWAP +0.3%",
        text=[f"VWAP +0.3%: ₹{v:.2f}" for v in vwap_upper],
        hoverinfo='text'
    ))

    # VWAP -0.3% band (lower - orange dot)
    vwap_lower = df['VWAP'] * 0.997
    fig.add_trace(go.Scatter(
        x=df.index, y=vwap_lower,
        line=dict(color='#ff9800', width=1, dash='dot'),
        opacity=0.55,
        name="VWAP -0.3%",
        text=[f"VWAP -0.3%: ₹{v:.2f}" for v in vwap_lower],
        hoverinfo='text'
    ))

    # Layout
    rangebreak_cfg = [
        dict(bounds=["sat", "mon"]),
        dict(bounds=[15.5, 9.25], pattern="hour")
    ]
    fig.update_layout(
        height=600,
        xaxis_rangeslider_visible=False,
        paper_bgcolor='#11151c', plot_bgcolor='#11151c',
        font_color='#8a99ad',
        margin=dict(t=16, b=40, l=30, r=30),
        legend=dict(orientation="h", yanchor="top", y=1.0, xanchor="left", x=0)
    )
    fig.update_xaxes(gridcolor='rgba(255,255,255,0.05)', rangebreaks=rangebreak_cfg)
    fig.update_yaxes(gridcolor='rgba(255,255,255,0.05)')

    st.plotly_chart(fig, width='stretch')

    display_tf = st.session_state.scan_params['timeframe'] if st.session_state.scan_params else timeframe
    chart_title = f"{symbol} — Intraday Price + VWAP ±0.1% & ±0.3% ({display_tf})"
    st.markdown(
        f'<div style="text-align:center;color:#8a99ad;font-size:0.9rem;margin-top:-0.5rem;margin-bottom:0.8rem;">'
        f'📊 {chart_title}</div>',
        unsafe_allow_html=True
    )



# ── Layout Tabs ────────────────────────────────────────────────────────────────

tab1, tab2 = st.tabs(["🔥 Active Signals & Charts", "📋 All Scanned Tickers"])

# TAB 1: Active Signals
with tab1:
    with st.expander("📖 Anticipation + Early Trigger Evaluation Engine (Supertrend 10, 1.2)", expanded=True):
        _strategy_html = (
            '<div style="background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:1.2rem;">'
            '<p style="color:#8a99ad;font-size:0.95rem;margin-bottom:1rem;line-height:1.5;">'
            '⚡ <strong>Anticipation + Early Trigger Engine:</strong> '
            'Fires signals on the <strong>Live Candle (offset -1)</strong> as soon as trend state, proximity to value, micro-breakout, and time-weighted volume velocity align.'
            '</p>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem;">'

            '<div style="background:rgba(40,167,69,0.07);border-left:4px solid #28a745;border-radius:6px;padding:1rem;">'
            '<div style="color:#28a745;font-weight:700;font-size:1rem;margin-bottom:0.6rem;">🟢 Long Trigger Criteria</div>'
            '<ul style="margin:0;padding-left:1.2rem;color:#e5e7eb;font-size:0.88rem;line-height:1.55;">'
            '<li style="margin-bottom:0.35rem;"><strong>Background State:</strong> Supertrend (10, 1.2) direction is Bullish (1).</li>'
            '<li style="margin-bottom:0.35rem;"><strong>Proximity to Value:</strong> Close &gt; VWAP &amp; within 0.1% distance of VWAP or 9 EMA.</li>'
            '<li style="margin-bottom:0.35rem;"><strong>Micro-Breakout:</strong> Live Close &gt; High of previous closed candle (offset -2).</li>'
            '<li style="margin-bottom:0.35rem;"><strong>Volume Velocity:</strong> Time-Weighted Projected Vol &gt; 1.5x Volume SMA 20.</li>'
            '<li><strong>Stop Loss:</strong> min(9 EMA, VWAP) with 0.05% risk floor.</li>'
            '</ul></div>'

            '<div style="background:rgba(220,53,69,0.07);border-left:4px solid #dc3545;border-radius:6px;padding:1rem;">'
            '<div style="color:#dc3545;font-weight:700;font-size:1rem;margin-bottom:0.6rem;">🔴 Short Trigger Criteria</div>'
            '<ul style="margin:0;padding-left:1.2rem;color:#e5e7eb;font-size:0.88rem;line-height:1.55;">'
            '<li style="margin-bottom:0.35rem;"><strong>Background State:</strong> Supertrend (10, 1.2) direction is Bearish (-1).</li>'
            '<li style="margin-bottom:0.35rem;"><strong>Proximity to Value:</strong> Close &lt; VWAP &amp; within 0.1% distance of VWAP or 9 EMA.</li>'
            '<li style="margin-bottom:0.35rem;"><strong>Micro-Breakout:</strong> Live Close &lt; Low of previous closed candle (offset -2).</li>'
            '<li style="margin-bottom:0.35rem;"><strong>Volume Velocity:</strong> Time-Weighted Projected Vol &gt; 1.5x Volume SMA 20.</li>'
            '<li><strong>Stop Loss:</strong> max(9 EMA, VWAP) with 0.05% risk floor.</li>'
            '</ul></div>'

            '</div>'

            '<div style="background:rgba(255,193,7,0.05);border-left:4px solid #ffc107;border-radius:6px;padding:1rem;">'
            '<div style="color:#ffc107;font-weight:700;font-size:1rem;margin-bottom:0.6rem;">⚡ Elimination of Entry Lag</div>'
            '<div style="font-size:0.88rem;color:#d1d5db;line-height:1.55;">'
            '<p style="margin:0 0 0.4rem;"><strong>Proximity Entry:</strong> Solves overextension by enforcing entries within 0.1% of value (VWAP / 9 EMA).</p>'
            '<p style="margin:0 0 0.4rem;"><strong>Time-Weighted Volume Projection:</strong> Projects full candle volume mid-candle: <code>(Live Vol / Elapsed Sec) * Duration</code>.</p>'
            '<p style="margin:0;"><strong>Micro-Breakout Trigger:</strong> Captures early momentum as soon as price breaks previous candle range.</p>'
            '</div></div>'

            '</div>'
        )
        st.markdown(_strategy_html, unsafe_allow_html=True)

    col_stat1, col_stat2 = st.columns([3, 1])
    with col_stat1:
        display_timeframe = st.session_state.scan_params['timeframe'] if st.session_state.scan_params else timeframe
        display_tickers_count = st.session_state.scan_params['tickers_count'] if st.session_state.scan_params else len(tickers)
        st.markdown(f"**Last Scanned At:** `{st.session_state.last_scan_time}` | **Timeframe:** `{display_timeframe}` | **Total Tickers:** `{display_tickers_count}`")
    with col_stat2:
        if st.session_state.last_scan_time != "Never":
            st.success(f"Found {len(st.session_state.scan_results)} setups!")

    if not st.session_state.scan_results:
        st.info("No breakout setups found yet. Adjust thresholds or click 'Scan Now' to run a fresh scan.")
    else:
        for idx, signal in enumerate(st.session_state.scan_results):
            symbol     = signal['symbol'].replace(".NS", "")
            direction  = signal['direction']
            curr_price = signal['current_price']
            vwap       = signal['vwap']
            ema_9      = signal['ema_9']
            stop_loss  = signal['stop_loss']
            target     = signal.get('target_custom', signal['target_2_0'])
            vol_ratio  = signal['volume_ratio']
            sl_source  = signal['sl_source']
            c_type     = signal['candle_type']

            card_class = "signal-card-long" if direction == "LONG" else "signal-card-short"
            badge      = "🟢 LONG BREAKOUT" if direction == "LONG" else "🔴 SHORT BREAKOUT"
            text_color = "#28a745" if direction == "LONG" else "#dc3545"

            # ── AI Score badge ──────────────────────────────────────────────────
            ai_score_data = signal.get('ai_score')
            ai_news_data  = signal.get('ai_news')

            score_badge_html = ""
            if ai_score_data:
                score     = ai_score_data['score']
                grade     = ai_score_data['grade']
                # Color the badge by grade
                grade_colors = {'A': '#00e676', 'B': '#69f0ae', 'C': '#ffc107', 'D': '#ef5350'}
                sc = grade_colors.get(grade, '#8a99ad')
                score_badge_html = (
                    f'<span style="background:rgba(0,0,0,0.35);border:1px solid {sc};'
                    f'color:{sc};padding:3px 12px;border-radius:20px;font-weight:700;font-size:0.9rem;margin-left:8px;">'
                    f'🤖 {score}/100 · {grade}</span>'
                )

            news_badge_html = ""
            if ai_news_data:
                emoji     = ai_news_data.get('emoji', '🟡')
                sentiment = ai_news_data.get('sentiment', 'Neutral')
                risk_flag = ai_news_data.get('risk_flag', 'None')
                rf_str    = f" · {risk_flag}" if risk_flag and risk_flag != 'None' else ""
                news_badge_html = (
                    f'<span style="background:rgba(0,0,0,0.25);border:1px solid rgba(255,255,255,0.12);'
                    f'color:#cdd5e0;padding:3px 10px;border-radius:20px;font-size:0.85rem;margin-left:6px;">'
                    f'{emoji} {sentiment}{rf_str}</span>'
                )

            html_content = f"""<div class="{card_class}">
<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.8rem; flex-wrap: wrap; gap: 0.4rem;">
<div style="display:flex;align-items:center;flex-wrap:wrap;gap:0.3rem;">
<span style="font-size: 1.4rem; font-weight: bold; color: #fff;">{symbol}</span>{score_badge_html}{news_badge_html}
</div>
<span style="background: rgba(255,255,255,0.15); padding: 3px 10px; border-radius: 20px; font-weight: bold; color: {text_color};">{badge}</span>
</div>
<div style="display: grid; grid-template-columns: repeat(4, 1fr); gap: 1rem;">
<div>
<div class="metric-label">Live Price</div>
<div class="metric-value">₹{curr_price:.2f}</div>
</div>
<div>
<div class="metric-label">Volume Breakout</div>
<div class="metric-value vol-breakout">{vol_ratio:.1f}x</div>
</div>
<div>
<div class="metric-label">Entry Range (9EMA – VWAP)</div>
<div class="metric-value" style="font-size: 1.1rem; font-weight: 500;">₹{min(ema_9, vwap):.2f} – ₹{max(ema_9, vwap):.2f}</div>
</div>
<div>
<div class="metric-label">Candle Type</div>
<div class="metric-value" style="font-size: 1.1rem; font-weight: 500; color: #ffc107;">{c_type}</div>
</div>
</div>
<div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; margin-top: 1rem; border-top: 1px solid rgba(255,255,255,0.1); padding-top: 0.8rem;">
<div>
<div class="metric-label">Stop-Loss (Source: {sl_source})</div>
<div class="metric-value" style="color: #dc3545;">₹{stop_loss:.2f} ({abs(curr_price-stop_loss)/curr_price*100:.2f}%)</div>
</div>
<div>
<div class="metric-label">Breakout Target ({st.session_state.scan_params['rr_ratio'] if st.session_state.scan_params else rr_ratio})</div>
<div class="metric-value" style="color: #28a745;">₹{target:.2f}</div>
</div>
<div>
<div class="metric-label">Risk-to-Reward Setup</div>
<div class="metric-value" style="font-size: 1.1rem; font-weight: 500; color: #29b6f6;">1 : {st.session_state.scan_params['rr_factor'] if st.session_state.scan_params else rr_factor} Ratio</div>
</div>
</div>
</div>"""
            st.markdown(html_content, unsafe_allow_html=True)

            # ── Inline Interactive Breakout Chart ───────────────────────────────
            with st.expander(f"📈 View Chart & 1-Hour Projection — {symbol}", expanded=False):
                render_breakout_chart(signal)

            # ── AI Detail Expander ──────────────────────────────────────────────
            if ai_score_data or ai_news_data:
                with st.expander(f"🤖 AI Analysis — {symbol}", expanded=False):
                    col_ai1, col_ai2 = st.columns(2)
                    if ai_score_data:
                        with col_ai1:
                            st.markdown("**📊 Signal Confidence**")
                            st.metric("Score", f"{ai_score_data['score']}/100", delta=f"Grade {ai_score_data['grade']}")
                            st.markdown("**Reasoning:**")
                            for bullet in ai_score_data.get('reasoning', '').split('|'):
                                if bullet.strip():
                                    st.markdown(f"• {bullet.strip()}")
                            st.info(f"💡 {ai_score_data.get('trade_advice', '')}")
                    if ai_news_data:
                        with col_ai2:
                            st.markdown("**📰 News Sentiment**")
                            sent_emoji = ai_news_data.get('emoji', '🟡')
                            st.markdown(f"### {sent_emoji} {ai_news_data.get('sentiment', 'Neutral')}")
                            st.markdown(ai_news_data.get('summary', ''))
                            if ai_news_data.get('risk_flag') and ai_news_data['risk_flag'] != 'None':
                                st.warning(f"⚠️ Risk Flag: {ai_news_data['risk_flag']}")
                            headlines = ai_news_data.get('headlines', [])
                            if headlines:
                                st.markdown("**Recent Headlines:**")
                                for h in headlines[:5]:
                                    st.markdown(f"› {h}")

# TAB 2: All Scanned Tickers
with tab2:
    st.markdown("### 📋 Current Scan Log & Metrics")
    st.write("Below is the list of all checked tickers with their live metrics for the current session.")
    
    if st.session_state.last_scan_time == "Never":
        st.info("Run a scan to view ticker metrics.")
    else:
        if st.session_state.all_ticker_metrics:
            df_log = pd.DataFrame(st.session_state.all_ticker_metrics)
            st.dataframe(df_log, width='stretch')
        else:
            st.warning("No data retrieved for scanned tickers.")

# Autorefresh runner logic
if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()
