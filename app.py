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
from utils.data_fetcher import analyze_ticker
from utils.stock_utils import load_equity_data

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
st.markdown('<div class="sub-header">Real-Time VWAP + 9 EMA + Supertrend (7, 3) Intraday Setup</div>', unsafe_allow_html=True)

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

# Sidebar Configuration
st.sidebar.markdown("### ⚙️ Scanner Settings")

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
vol_mult = st.sidebar.slider("Volume Breakout Multiplier", min_value=1.5, max_value=4.0, value=2.0, step=0.1)

# Add Supertrend Flip Lookback slider
st_lookback = st.sidebar.slider(
    "Supertrend Flip Lookback (candles)",
    min_value=0,
    max_value=20,
    value=5,
    help="How many candles ago the Supertrend crossover occurred. 0 means any active green/red trend."
)

# Add Volume Bypass checkbox (highly useful off-market)
ignore_vol = st.sidebar.checkbox(
    "Bypass Volume Filter",
    value=False,
    help="Useful for off-market testing or when trading volume is dry."
)

rr_ratio = st.sidebar.selectbox("Risk-to-Reward Ratio Target", options=["1:1.5", "1:2.0", "1:2.5"], index=1)
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
    progress_text = "Scanning tickers..."
    my_bar = st.progress(0, text=progress_text)
    
    found_signals = []
    all_metrics = []
    total = len(tickers)
    
    for idx, ticker in enumerate(tickers):
        symbol_name = ticker.replace(".NS", "")
        my_bar.progress((idx + 1) / total, text=f"Analyzing {symbol_name} ({idx+1}/{total})")
        
        # Analyze with user thresholds
        res = analyze_ticker(ticker, interval=timeframe, period="5d", st_lookback=st_lookback, ignore_volume=ignore_vol)
        if res:
            # Check if breakout setup was triggered
            if res['setup_triggered']:
                # Filter by volume breakout multiplier
                if ignore_vol or res['volume_ratio'] >= vol_mult:
                    # Recalculate target with dynamic risk ratio
                    risk = res['risk']
                    close = res['current_price']
                    if res['direction'] == 'LONG':
                        res['target_custom'] = close + (rr_factor * risk)
                    else:
                        res['target_custom'] = close - (rr_factor * risk)
                    found_signals.append(res)
            
            # Save to all ticker metrics list
            all_metrics.append({
                "Symbol": symbol_name,
                "Price (₹)": f"₹{res['current_price']:.2f}",
                "VWAP": f"₹{res['vwap']:.2f}" if res['vwap'] else "N/A",
                "9 EMA": f"₹{res['ema_9']:.2f}" if res['ema_9'] else "N/A",
                "Volume Ratio": f"{res['volume_ratio']:.2f}x" if res['vol_sma20'] > 0 else "N/A",
                "Direction": res['direction'] if res['setup_triggered'] else "N/A",
                "Setup Triggered": "✅ Yes" if res['setup_triggered'] else "❌ No"
            })
        else:
            # Handle API error or empty data gracefully in the summary table
            all_metrics.append({
                "Symbol": symbol_name,
                "Price (₹)": "N/A",
                "VWAP": "N/A",
                "9 EMA": "N/A",
                "Volume Ratio": "N/A",
                "Direction": "N/A",
                "Setup Triggered": "❌ No (No Data)"
            })
                
    my_bar.empty()
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
        'rr_factor': rr_factor
    }

# Handle Trigger
if scan_clicked or (auto_refresh and not st.session_state.running):
    st.session_state.running = True
    run_scan()
    st.session_state.running = False

# Layout Tabs
tab1, tab2, tab3 = st.tabs(["🔥 Active Signals", "📈 Live Interactive Charting", "📋 All Scanned Tickers"])

# TAB 1: Active Signals
with tab1:
    with st.expander("📖 Trading Strategy & Entry Trigger Rules (09:20 AM - 10:00 AM)", expanded=True):
        _strategy_html = (
            '<div style="background:rgba(255,255,255,0.02);border:1px solid rgba(255,255,255,0.08);border-radius:10px;padding:1.2rem;">'
            '<p style="color:#8a99ad;font-size:0.95rem;margin-bottom:1rem;line-height:1.5;">'
            '⚡ <strong>Once a stock pops up on your scan between 09:20 AM and 10:00 AM:</strong> '
            'Open its chart and look for the exact entry trigger below.'
            '</p>'
            '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1rem;">'

            '<div style="background:rgba(40,167,69,0.07);border-left:4px solid #28a745;border-radius:6px;padding:1rem;">'
            '<div style="color:#28a745;font-weight:700;font-size:1rem;margin-bottom:0.6rem;">🟢 Long Setup — Call Options / Futures</div>'
            '<ul style="margin:0;padding-left:1.2rem;color:#e5e7eb;font-size:0.88rem;line-height:1.55;">'
            '<li style="margin-bottom:0.35rem;"><strong>Entry:</strong> 3-min candle closes <em>above</em> both VWAP &amp; 9 EMA while Supertrend is green.</li>'
            '<li style="margin-bottom:0.35rem;"><strong>Confirmation:</strong> Distinct tall green volume bar on the breakout candle.</li>'
            '<li style="margin-bottom:0.35rem;"><strong>Stop-Loss:</strong> At the 9 EMA or Supertrend (whichever is closer). Cut instantly on a close below.</li>'
            '<li><strong>Target:</strong> Exit when candle closes below 9 EMA, or 1:1.5–1:2 R:R.</li>'
            '</ul></div>'

            '<div style="background:rgba(220,53,69,0.07);border-left:4px solid #dc3545;border-radius:6px;padding:1rem;">'
            '<div style="color:#dc3545;font-weight:700;font-size:1rem;margin-bottom:0.6rem;">🔴 Short Setup — Put Options / Short Futures</div>'
            '<ul style="margin:0;padding-left:1.2rem;color:#e5e7eb;font-size:0.88rem;line-height:1.55;">'
            '<li style="margin-bottom:0.35rem;"><strong>Entry:</strong> 3-min candle closes <em>below</em> both VWAP &amp; 9 EMA while Supertrend is red.</li>'
            '<li style="margin-bottom:0.35rem;"><strong>Confirmation:</strong> Heavy surge in volume on the breakdown candle.</li>'
            '<li style="margin-bottom:0.35rem;"><strong>Stop-Loss:</strong> At the 9 EMA or Supertrend line.</li>'
            '<li><strong>Target:</strong> Exit when a candle closes back above the 9 EMA.</li>'
            '</ul></div>'

            '</div>'

            '<div style="background:rgba(255,193,7,0.05);border-left:4px solid #ffc107;border-radius:6px;padding:1rem;">'
            '<div style="color:#ffc107;font-weight:700;font-size:1rem;margin-bottom:0.6rem;">💡 Pro-Tips for the Indian Market</div>'
            '<div style="font-size:0.88rem;color:#d1d5db;line-height:1.55;">'
            '<p style="margin:0 0 0.4rem;"><strong>The 09:45 AM Trap:</strong> The first 15 min (09:15–09:30) are wildly erratic due to overnight order clearing. '
            'If a signal fires at 09:18, wait for the 09:25 candle to confirm above VWAP. Fake breakouts are common in the first 10 minutes.</p>'
            '<p style="margin:0 0 0.4rem;"><strong>The Nifty Alignment Rule:</strong> If you are long a stock but Nifty / Bank Nifty is crashing, skip the trade. '
            'Intraday stock trends rarely survive a fighting broader index.</p>'
            '<p style="margin:0;"><strong>Exit by 02:45 PM:</strong> Momentum dries up or violently reverses after 03:00 PM due to MIS auto-square-offs '
            'by brokers (Zerodha, Groww, Angel One). Wrap up all momentum trades well before then.</p>'
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
            symbol = signal['symbol'].replace(".NS", "")
            direction = signal['direction']
            curr_price = signal['current_price']
            vwap = signal['vwap']
            ema_9 = signal['ema_9']
            stop_loss = signal['stop_loss']
            target = signal.get('target_custom', signal['target_2_0'])
            vol_ratio = signal['volume_ratio']
            sl_source = signal['sl_source']
            c_type = signal['candle_type']
            
            card_class = "signal-card-long" if direction == "LONG" else "signal-card-short"
            badge = "🟢 LONG BREAKOUT" if direction == "LONG" else "🔴 SHORT BREAKOUT"
            text_color = "#28a745" if direction == "LONG" else "#dc3545"
            
            # Display setup details in a premium card format
            st.markdown(f"""
            <div class="{card_class}">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.8rem;">
                    <span style="font-size: 1.4rem; font-weight: bold; color: #fff;">{symbol}</span>
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
                        <div class="metric-label">Entry Range (9EMA - VWAP)</div>
                        <div class="metric-value" style="font-size: 1.1rem; font-weight: 500;">₹{min(ema_9, vwap):.2f} - ₹{max(ema_9, vwap):.2f}</div>
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
            </div>
            """, unsafe_allow_html=True)

# TAB 2: Live Charts
with tab2:
    if not st.session_state.scan_results:
        st.info("No active signals to chart. Run a scan to see breakout charts.")
    else:
        st.markdown("### 📊 Interactive Breakout Charts")
        selected_sig_symbol = st.selectbox(
            "Select Ticker to Chart",
            options=[s['symbol'] for s in st.session_state.scan_results],
            format_func=lambda x: x.replace(".NS", "")
        )
        
        # Get matching signal
        sig_data = next((s for s in st.session_state.scan_results if s['symbol'] == selected_sig_symbol), None)
        
        if sig_data:
            df = sig_data['df']
            
            # Subplots: Candlesticks/indicators + Volume
            fig = make_subplots(
                rows=2, cols=1, 
                shared_xaxes=True,
                vertical_spacing=0.08,
                row_width=[0.2, 0.8]
            )
            
            # Candlesticks
            fig.add_trace(go.Candlestick(
                x=df.index,
                open=df['Open'],
                high=df['High'],
                low=df['Low'],
                close=df['Close'],
                name="Price"
            ), row=1, col=1)
            
            # VWAP (orange)
            fig.add_trace(go.Scatter(
                x=df.index,
                y=df['VWAP'],
                line=dict(color='#ff9800', width=2),
                name="VWAP"
            ), row=1, col=1)
            
            # 9 EMA (blue)
            fig.add_trace(go.Scatter(
                x=df.index,
                y=df['EMA_9'],
                line=dict(color='#2196f3', width=1.5, dash='dash'),
                name="9 EMA"
            ), row=1, col=1)
            
            # Supertrend
            # Map colors: Green for direction=1, Red for direction=-1
            # We draw the Supertrend line
            fig.add_trace(go.Scatter(
                x=df.index,
                y=df['ST_Line'],
                line=dict(color='#4caf50', width=1.5),
                name="Supertrend"
            ), row=1, col=1)
            
            # Plot Volume SMA
            fig.add_trace(go.Bar(
                x=df.index,
                y=df['Volume'],
                marker_color=np.where(df['Close'] >= df['Open'], '#28a745', '#dc3545'),
                name="Volume"
            ), row=2, col=1)
            
            fig.add_trace(go.Scatter(
                x=df.index,
                y=df['Vol_SMA20'],
                line=dict(color='#ffc107', width=1.5),
                name="20-Vol SMA"
            ), row=2, col=1)
            
            # ── Projection: Next 30 min (10 × 3-min candles) ──────────────────
            PROJ_CANDLES   = 10          # 10 candles × 3 min = 30 min
            CANDLE_MINS    = 3           # minutes per candle for labelling
            EMA_K          = 2 / (9 + 1) # EMA smoothing factor

            # ── Price trend: linear slope from last 5 candles ──────────────────
            price_tail   = df['Close'].tail(5).values
            price_slope  = np.polyfit(range(len(price_tail)), price_tail, 1)[0]  # ₹ per candle

            # ── ATR (last 14 candles) for uncertainty band ─────────────────────
            atr_tail = df['ST_Line'].tail(14)  # Use ST line proximity as proxy
            raw_atr  = (df['High'] - df['Low']).tail(14).mean()

            # ── VWAP projection ────────────────────────────────────────────────
            # Back-calculate cumTPV and cumVol for today's session from the df
            # VWAP[t] = cumTPV[t] / cumVol[t]  →  cumTPV[-1] = VWAP[-1] * cumVol[-1]
            today = df.index[-1].date() if hasattr(df.index[-1], 'date') else None
            if today is not None:
                today_mask = pd.Series(df.index).apply(lambda x: x.date() == today if hasattr(x, 'date') else True).values
                df_today   = df[today_mask]
            else:
                df_today   = df

            cum_vol   = float(df_today['Volume'].sum())
            cum_tpv   = float(df['VWAP'].iloc[-1]) * cum_vol
            avg_vol   = float(df_today['Volume'].tail(10).mean()) if len(df_today) >= 10 else float(df_today['Volume'].mean())


            # ── Build future timestamps ─────────────────────────────────────────
            last_ts   = df.index[-1]
            display_tf = st.session_state.scan_params['timeframe'] if st.session_state.scan_params else timeframe
            freq_min  = int(display_tf.replace('m', ''))  # actual chart timeframe (2 or 5 min)
            future_ts = pd.date_range(start=last_ts, periods=PROJ_CANDLES + 1, freq=f'{freq_min}min')[1:]

            # ── Iterate candles and project ────────────────────────────────────
            proj_vwap = []
            proj_ema  = []
            proj_vwap_high = []  # optimistic: +0.5 ATR on price
            proj_vwap_low  = []  # pessimistic: −0.5 ATR on price
            proj_ema_high  = []
            proj_ema_low   = []

            last_ema  = float(df['EMA_9'].iloc[-1])
            last_price = float(df['Close'].iloc[-1])

            cum_vol_h  = cum_vol
            cum_tpv_h  = cum_tpv
            cum_vol_l  = cum_vol
            cum_tpv_l  = cum_tpv
            prev_ema_h = last_ema
            prev_ema_l = last_ema

            for i in range(1, PROJ_CANDLES + 1):
                # Central projection (trend)
                proj_price = last_price + price_slope * i
                cum_tpv   += proj_price * avg_vol
                cum_vol   += avg_vol
                v = cum_tpv / cum_vol if cum_vol > 0 else proj_price
                proj_vwap.append(round(v, 2))
                ema_c = proj_price * EMA_K + (proj_ema[-1] if proj_ema else last_ema) * (1 - EMA_K)
                proj_ema.append(round(ema_c, 2))

                # Optimistic (+0.5 ATR above trend)
                p_h = proj_price + 0.5 * raw_atr
                cum_tpv_h += p_h * avg_vol
                cum_vol_h += avg_vol
                v_h = cum_tpv_h / cum_vol_h if cum_vol_h > 0 else p_h
                proj_vwap_high.append(round(v_h, 2))
                ema_h = p_h * EMA_K + prev_ema_h * (1 - EMA_K)
                proj_ema_high.append(round(ema_h, 2))
                prev_ema_h = ema_h

                # Pessimistic (−0.5 ATR below trend)
                p_l = proj_price - 0.5 * raw_atr
                cum_tpv_l += p_l * avg_vol
                cum_vol_l += avg_vol
                v_l = cum_tpv_l / cum_vol_l if cum_vol_l > 0 else p_l
                proj_vwap_low.append(round(v_l, 2))
                ema_l = p_l * EMA_K + prev_ema_l * (1 - EMA_K)
                proj_ema_low.append(round(ema_l, 2))
                prev_ema_l = ema_l

            # Stitch historical last point → future (for continuous lines)
            x_proj = [last_ts] + list(future_ts)
            v_proj_vwap      = [float(df['VWAP'].iloc[-1])] + proj_vwap
            v_proj_ema       = [float(df['EMA_9'].iloc[-1])] + proj_ema
            v_proj_vwap_h    = [float(df['VWAP'].iloc[-1])] + proj_vwap_high
            v_proj_vwap_l    = [float(df['VWAP'].iloc[-1])] + proj_vwap_low
            v_proj_ema_h     = [float(df['EMA_9'].iloc[-1])] + proj_ema_high
            v_proj_ema_l     = [float(df['EMA_9'].iloc[-1])] + proj_ema_low

            hover_labels = [last_ts.strftime('%H:%M')] + [
                f"+{i * freq_min}m  {t.strftime('%H:%M')}" for i, t in enumerate(future_ts, 1)
            ]

            # ── VWAP prediction band (fill) ────────────────────────────────────
            fig.add_trace(go.Scatter(
                x=x_proj + x_proj[::-1],
                y=v_proj_vwap_h + v_proj_vwap_l[::-1],
                fill='toself',
                fillcolor='rgba(255,152,0,0.08)',
                line=dict(width=0),
                showlegend=False,
                hoverinfo='skip',
                name='VWAP Band'
            ), row=1, col=1)

            # ── EMA prediction band (fill) ─────────────────────────────────────
            fig.add_trace(go.Scatter(
                x=x_proj + x_proj[::-1],
                y=v_proj_ema_h + v_proj_ema_l[::-1],
                fill='toself',
                fillcolor='rgba(33,150,243,0.07)',
                line=dict(width=0),
                showlegend=False,
                hoverinfo='skip',
                name='EMA Band'
            ), row=1, col=1)

            # ── Projected VWAP line (dashed orange) ────────────────────────────
            fig.add_trace(go.Scatter(
                x=x_proj,
                y=v_proj_vwap,
                mode='lines+markers',
                line=dict(color='#ff9800', width=2, dash='dot'),
                marker=dict(size=5, symbol='circle-open', color='#ff9800'),
                name='Proj VWAP',
                text=[f"Proj VWAP: ₹{v:.2f}<br>{lbl}" for v, lbl in zip(v_proj_vwap, hover_labels)],
                hoverinfo='text'
            ), row=1, col=1)

            # ── Projected 9 EMA line (dashed blue) ────────────────────────────
            fig.add_trace(go.Scatter(
                x=x_proj,
                y=v_proj_ema,
                mode='lines+markers',
                line=dict(color='#64b5f6', width=2, dash='dot'),
                marker=dict(size=5, symbol='circle-open', color='#64b5f6'),
                name='Proj 9 EMA',
                text=[f"Proj EMA: ₹{v:.2f}<br>{lbl}" for v, lbl in zip(v_proj_ema, hover_labels)],
                hoverinfo='text'
            ), row=1, col=1)

            # ── Vertical separator at "now" ────────────────────────────────────
            fig.add_vline(
                x=last_ts.timestamp() * 1000,
                line=dict(color='rgba(255,255,255,0.25)', width=1.5, dash='dash'),
                row=1, col=1
            )
            fig.add_annotation(
                x=last_ts, y=1, yref='paper',
                text='<b>Now →</b>',
                showarrow=False,
                font=dict(color='rgba(255,255,255,0.5)', size=11),
                xanchor='left', xshift=6
            )
            # ──────────────────────────────────────────────────────────────────

            # Styling Layout (no title inside the figure — moved below chart to avoid legend overlap)
            fig.update_layout(
                height=700,
                xaxis_rangeslider_visible=False,
                paper_bgcolor='#11151c',
                plot_bgcolor='#11151c',
                font_color='#8a99ad',
                margin=dict(t=20, b=50, l=30, r=30),
                legend=dict(orientation="h", yanchor="top", y=1.0, xanchor="left", x=0)
            )

            fig.update_xaxes(gridcolor='rgba(255,255,255,0.05)', row=1, col=1)
            fig.update_yaxes(gridcolor='rgba(255,255,255,0.05)', row=1, col=1)
            fig.update_xaxes(gridcolor='rgba(255,255,255,0.05)', row=2, col=1)
            fig.update_yaxes(gridcolor='rgba(255,255,255,0.05)', row=2, col=1)

            st.plotly_chart(fig, width='stretch')

            # ── Chart title (below chart to avoid overlapping legend) ──────────
            display_tf = st.session_state.scan_params['timeframe'] if st.session_state.scan_params else timeframe
            chart_title = f"{selected_sig_symbol.replace('.NS', '')} — Intraday Setup + 30-Min Projection ({display_tf})"
            st.markdown(
                f'<div style="text-align:center;color:#8a99ad;font-size:0.95rem;margin-top:-0.5rem;margin-bottom:1rem;">'
                f'📊 {chart_title}</div>',
                unsafe_allow_html=True
            )

            # ── Projection data table ──────────────────────────────────────────
            with st.expander("📋 Projection Data Table (Next 30 min)", expanded=False):
                proj_df = pd.DataFrame({
                    'Time': [t.strftime('%H:%M') for t in future_ts],
                    'Candle': [f"+{i * freq_min}m" for i in range(1, PROJ_CANDLES + 1)],
                    'Proj Price (Trend)': [f"₹{last_price + price_slope * i:.2f}" for i in range(1, PROJ_CANDLES + 1)],
                    'Proj VWAP': [f"₹{v:.2f}" for v in proj_vwap],
                    'Proj 9 EMA': [f"₹{v:.2f}" for v in proj_ema],
                    'VWAP Range': [f"₹{l:.2f} – ₹{h:.2f}" for l, h in zip(proj_vwap_low, proj_vwap_high)],
                    'EMA Range':  [f"₹{l:.2f} – ₹{h:.2f}" for l, h in zip(proj_ema_low,  proj_ema_high)],
                })
                st.dataframe(proj_df, width='stretch', hide_index=True)
            
            st.caption(
                "⚠️ Projection assumes recent price trend continues. "
                "Shaded bands show ±0.5 ATR uncertainty. "
                "These are **statistical estimates**, not guaranteed levels — "
                "always confirm with live price action."
            )


# TAB 3: All Scanned Tickers reference
with tab3:
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
