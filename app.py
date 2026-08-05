import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time
from datetime import datetime
import os
import sys
import json
import subprocess
import requests
import streamlit.components.v1 as components

# Ensure local directories are in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.tickers import ALL_CATEGORIES
from utils.data_fetcher import analyze_ticker, fetch_batch_intraday_data
from utils.stock_utils import load_equity_data
from utils.ai_analyzer import score_signal, get_news_sentiment
from utils.database import (
    init_db,
    save_recommendations_batch,
    get_recommendations,
    get_available_dates,
    get_db_stats,
    get_ist_now,
    auto_cleanup_old_recommendations,
    delete_recommendation,
    clear_all_recommendations
)

# ── Telegram Push Notification Support ─────────────────────────────────────────
def send_telegram_notification(bot_token: str, chat_id: str, message: str, html_mode: bool = True) -> tuple[bool, str]:
    """
    Send a formatted message via Telegram Bot API.
    Returns (success: bool, detail_msg: str) for precise UI diagnostics.
    """
    token = bot_token.strip() if bot_token else ""
    chat = chat_id.strip() if chat_id else ""
    
    if not token:
        return False, "Bot Token is empty."
    if not chat:
        return False, "Chat ID is empty."
    
    # Strip leading 'bot' prefix if user accidentally included it in token field
    if token.lower().startswith("bot") and ":" in token:
        token = token[3:]

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    # Use HTML formatting
    payload = {
        "chat_id": chat,
        "text": message,
        "parse_mode": "HTML" if html_mode else None,
        "disable_web_page_preview": True
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=8)
        if resp.status_code == 200:
            return True, "Message sent successfully!"
            
        try:
            data = resp.json()
            err_desc = data.get("description", resp.text)
        except Exception:
            err_desc = resp.text
        
        # Provide user-friendly hints for common Telegram API status codes
        if resp.status_code == 401:
            return False, f"HTTP 401 (Unauthorized): Invalid Bot Token. Please check token from @BotFather."
        elif resp.status_code == 400:
            if "chat not found" in err_desc.lower():
                return False, (
                    "HTTP 400 (Chat Not Found): Telegram couldn't find this chat.\n"
                    "👉 STEP 1: Open your bot in Telegram and tap /START.\n"
                    "👉 STEP 2: Use your numeric Chat ID from @userinfobot (e.g. 987654321), NOT your bot's name."
                )
            # Fallback to plain text if HTML entity parsing failed
            if html_mode and ("parse" in err_desc.lower() or "entity" in err_desc.lower()):
                payload.pop("parse_mode", None)
                resp_plain = requests.post(url, json=payload, timeout=8)
                if resp_plain.status_code == 200:
                    return True, "Message sent (fallback to plain text)."
            return False, f"HTTP 400 (Bad Request): {err_desc}"
        elif resp.status_code == 403:
            return False, f"HTTP 403 (Forbidden): {err_desc}. 👉 Open your Telegram bot and press /START first!"
        else:
            return False, f"HTTP {resp.status_code}: {err_desc}"
            
    except requests.exceptions.Timeout:
        return False, "Network Timeout: Could not reach Telegram API within 8 seconds."
    except Exception as e:
        return False, f"Connection Error: {str(e)}"
def send_windows_notification(title: str, message: str, duration_ms: int = 8000):
    """Fires a Windows system tray balloon notification using PowerShell (no extra packages needed)."""
    # Truncate to safe lengths for the balloon tip
    title_safe   = title[:63]   if len(title)   > 63   else title
    message_safe = message[:255] if len(message) > 255 else message

    ps_script = f"""
$ErrorActionPreference = 'SilentlyContinue'
Add-Type -AssemblyName System.Windows.Forms
$ico = [System.Drawing.SystemIcons]::Information
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = $ico
$n.BalloonTipTitle = "{title_safe}"
$n.BalloonTipText  = "{message_safe}"
$n.BalloonTipIcon  = "Info"
$n.Visible = $true
$n.ShowBalloonTip({duration_ms})
Start-Sleep -Milliseconds {duration_ms + 500}
$n.Dispose()
"""
    try:
        # Fire and forget — runs in background so Streamlit is never blocked
        subprocess.Popen(
            ['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', ps_script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
    except Exception:
        pass  # Silently fail — notification is best-effort


# Notification helper function
def render_notification_trigger(symbols_list, sound_enabled=True, desktop_enabled=True):
    """Fires Web Audio API chime sound & HTML5 Desktop Notification when setups are found."""
    symbols_json = json.dumps(symbols_list)
    sound_js = "true" if sound_enabled else "false"
    desktop_js = "true" if desktop_enabled else "false"
    
    html_code = f"""
    <script>
    (function() {{
        const symbols = {symbols_json};
        console.log("Triggering setup notification for:", symbols);
        
        // 1. Play Web Audio API Chime Sound
        if ({sound_js}) {{
            try {{
                const AudioCtx = window.AudioContext || window.webkitAudioContext;
                if (AudioCtx) {{
                    const ctx = new AudioCtx();
                    if (ctx.state === 'suspended') {{
                        ctx.resume();
                    }}
                    
                    function playTone(freq, type, startTime, duration) {{
                        const osc = ctx.createOscillator();
                        const gain = ctx.createGain();
                        osc.type = type;
                        osc.frequency.setValueAtTime(freq, ctx.currentTime + startTime);
                        gain.gain.setValueAtTime(0.4, ctx.currentTime + startTime);
                        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + startTime + duration);
                        osc.connect(gain);
                        gain.connect(ctx.destination);
                        osc.start(ctx.currentTime + startTime);
                        osc.stop(ctx.currentTime + startTime + duration);
                    }}
                    
                    // Multi-tone chime sound alert (C5 -> E5 -> G5 -> C6)
                    playTone(523.25, 'sine', 0.0, 0.15);
                    playTone(659.25, 'sine', 0.12, 0.15);
                    playTone(783.99, 'sine', 0.24, 0.15);
                    playTone(1046.50, 'sine', 0.36, 0.35);
                }}
            }} catch(e) {{
                console.error("Audio notification error:", e);
            }}
        }}

        // 2. Desktop Browser Notification API
        if ({desktop_js}) {{
            try {{
                const title = "⚡ Breakout Setup Found! (" + symbols.length + ")";
                const body = "Setup detected for: " + symbols.join(", ");
                
                function showNotification() {{
                    try {{
                        new Notification(title, {{
                            body: body,
                            icon: "https://fav.farm/⚡",
                            requireInteraction: true
                        }});
                    }} catch(err) {{
                        console.log("Direct notification error:", err);
                    }}
                }}

                if ("Notification" in window) {{
                    if (Notification.permission === "granted") {{
                        showNotification();
                    }} else if (Notification.permission !== "denied") {{
                        Notification.requestPermission().then(perm => {{
                            if (perm === "granted") showNotification();
                        }});
                    }}
                }}
                
                if (window.parent && window.parent !== window && window.parent.Notification) {{
                    if (window.parent.Notification.permission === "granted") {{
                        try {{ new window.parent.Notification(title, {{ body: body }}); }} catch(e){{}}
                    }} else if (window.parent.Notification.permission !== "denied") {{
                        window.parent.Notification.requestPermission().then(perm => {{
                            if (perm === "granted") try {{ new window.parent.Notification(title, {{ body: body }}); }} catch(e){{}}
                        }});
                    }}
                }}
            }} catch(e) {{
                console.error("Desktop notification error:", e);
            }}
        }}
    }})();
    </script>
    """
    if hasattr(st, "html"):
        st.html(html_code)
    else:
        components.html(html_code, height=0, width=0)


# Initialize SQLite Database & Auto-cleanup (>15 days old)
init_db()

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
    groq_def = os.getenv("GROQ_API_KEY", "")
    try:
        groq_def = groq_def or st.secrets.get("GROQ_API_KEY", "")
    except Exception:
        pass
    st.session_state.groq_api_key = groq_def

if 'telegram_bot_token' not in st.session_state:
    token_def = os.getenv("TELEGRAM_BOT_TOKEN", "")
    try:
        token_def = token_def or st.secrets.get("TELEGRAM_BOT_TOKEN", "")
    except Exception:
        pass
    st.session_state.telegram_bot_token = token_def

if 'telegram_chat_id' not in st.session_state:
    chat_def = os.getenv("TELEGRAM_CHAT_ID", "")
    try:
        chat_def = chat_def or st.secrets.get("TELEGRAM_CHAT_ID", "")
    except Exception:
        pass
    st.session_state.telegram_chat_id = chat_def

if 'notify_signals' not in st.session_state:
    st.session_state.notify_signals = None
if 'trigger_test_alert' not in st.session_state:
    st.session_state.trigger_test_alert = False
if 'saved_refresh_choice' not in st.session_state:
    st.session_state.saved_refresh_choice = "1 min"   # user's preferred in-market interval

# ── Market Hours Helper ─────────────────────────────────────────────────────
def _is_market_hours() -> bool:
    """Return True if current IST time is within NSE market hours: Mon-Fri, 08:45–15:45."""
    now_ist = get_ist_now()
    if now_ist.weekday() >= 5:          # Saturday=5, Sunday=6
        return False
    market_open  = now_ist.replace(hour=8,  minute=45, second=0, microsecond=0)
    market_close = now_ist.replace(hour=15, minute=45, second=0, microsecond=0)
    return market_open <= now_ist <= market_close

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

# Refresh Mode & Notifications
st.sidebar.markdown("---")
st.sidebar.markdown("### 🔄 Auto-Refresh Settings")
auto_refresh = st.sidebar.checkbox("🔄 Enable Auto-Refresh", value=False)

_in_market = _is_market_hours()
refresh_seconds_map = {
    "1 min":  60,
    "2 min":  120,
    "5 min":  300,
    "10 min": 600,
}

if auto_refresh:
    if _in_market:
        # Show the real selectbox during market hours
        refresh_choice = st.sidebar.selectbox(
            "Auto-Refresh Interval",
            options=["1 min", "2 min", "5 min", "10 min"],
            index=list(refresh_seconds_map.keys()).index(
                st.session_state.saved_refresh_choice
                if st.session_state.saved_refresh_choice in refresh_seconds_map
                else "1 min"
            ),
            help="Select auto-refresh interval during market hours."
        )
        # Persist user's choice whenever they change it
        if refresh_choice != st.session_state.saved_refresh_choice:
            st.session_state.saved_refresh_choice = refresh_choice
        refresh_interval = refresh_seconds_map[refresh_choice]
    else:
        # Outside market hours — lock at 1 hour, show saved preference greyed out
        refresh_choice = st.session_state.saved_refresh_choice  # restore label
        refresh_interval = 3600  # 1 hour
        st.sidebar.selectbox(
            "Auto-Refresh Interval",
            options=["1 min", "2 min", "5 min", "10 min"],
            index=list(refresh_seconds_map.keys()).index(
                st.session_state.saved_refresh_choice
                if st.session_state.saved_refresh_choice in refresh_seconds_map
                else "1 min"
            ),
            disabled=True,
            help="Locked outside market hours. Resumes at market open (08:45 IST)."
        )
else:
    # Auto-refresh disabled — still read preference for display
    refresh_choice = st.session_state.saved_refresh_choice
    refresh_interval = refresh_seconds_map.get(refresh_choice, 60)

st.sidebar.markdown("---")
st.sidebar.markdown("### 🔔 Setup Notification Triggers")
enable_sound_alert = st.sidebar.checkbox("🔊 Audio Sound Alert", value=True, help="Play multi-tone chime alert when a setup is found.")
enable_desktop_alert = st.sidebar.checkbox("💻 Desktop Browser Popup", value=True, help="Trigger browser desktop popup notification when a setup is found.")

enable_telegram = st.sidebar.checkbox(
    "📱 Telegram Push Alerts (iOS / Android)",
    value=bool(st.session_state.telegram_bot_token and st.session_state.telegram_chat_id),
    help="Send instant push notifications to your mobile phone via Telegram Bot when setups fire."
)

if enable_telegram:
    with st.sidebar.expander("📱 Telegram Bot Config", expanded=not (st.session_state.telegram_bot_token and st.session_state.telegram_chat_id)):
        st.markdown(
            "**Quick Setup:**\n"
            "1. Search `@BotFather` on Telegram → `/newbot` to get your **Bot Token**.\n"
            "2. Search `@userinfobot` on Telegram to get your numeric **Chat ID**."
        )
        t_token = st.text_input("Bot Token", value=st.session_state.telegram_bot_token, type="password", help="e.g. 123456789:ABCdefGhIJKlmNo")
        t_chat  = st.text_input("Chat ID", value=st.session_state.telegram_chat_id, help="e.g. 987654321")
        if t_token != st.session_state.telegram_bot_token:
            st.session_state.telegram_bot_token = t_token.strip()
        if t_chat != st.session_state.telegram_chat_id:
            st.session_state.telegram_chat_id = t_chat.strip()

if st.sidebar.button("🧪 Test Sound & Notifications", help="Click to test audio sound chime, browser desktop popup, and Telegram alert"):
    st.session_state.trigger_test_alert = True

st.sidebar.markdown("---")

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
    st.session_state.last_scan_time = get_ist_now().strftime("%H:%M:%S IST")
    
    # Save breakout recommendations to database (Auto-purges >7 day old records)
    if found_signals:
        saved_num = save_recommendations_batch(found_signals, validity_days=7)
        if saved_num > 0:
            st.toast(f"💾 Saved {saved_num} new recommendation(s) to Database!", icon="💾")
        # Store signals for notification trigger
        notif_syms = [f"{s['symbol'].replace('.NS', '')} ({s['direction']})" for s in found_signals]
        st.session_state.notify_signals = notif_syms

        # ── Fire real Windows system notification immediately ──────────────────
        title   = f"⚡ {len(notif_syms)} Setup(s) Found! — Options Scanner"
        message = "Setup detected: " + ", ".join(notif_syms[:8])  # cap at 8 to stay within 255 chars
        if len(notif_syms) > 8:
            message += f" (+{len(notif_syms) - 8} more)"
        send_windows_notification(title, message)

        # ── Send Telegram Bot Push Notification (iOS / Android / Cloud) ─────────
        if enable_telegram and st.session_state.telegram_bot_token and st.session_state.telegram_chat_id:
            tg_lines = [f"⚡ <b>INTRADAY BREAKOUT SETUP DETECTED ({len(found_signals)})</b>\n"]
            for sig in found_signals[:10]:
                sym = sig['symbol'].replace('.NS', '')
                d = sig['direction']
                p = sig['current_price']
                sl = sig['stop_loss']
                t = sig.get('target_custom', sig['target_2_0'])
                vr = sig['volume_ratio']
                rvol_str = f"🔥 <b>{vr:.2f}x</b>" if vr >= 2.0 else f"{vr:.2f}x"
                ai_str = f" | AI: {sig['ai_score']['score']}/100" if sig.get('ai_score') else ""
                
                direction_emoji = "🟢" if d == "LONG" else "🔴"
                tg_lines.append(
                    f"{direction_emoji} <b>{sym}</b> ({d})\n"
                    f"• Price: ₹{p:.2f} | SL: ₹{sl:.2f} | Target: ₹{t:.2f}\n"
                    f"• Vol Ratio: {rvol_str}{ai_str}\n"
                )
            
            tg_msg = "\n".join(tg_lines) + f"⏱️ <b>Time:</b> {get_ist_now().strftime('%H:%M:%S IST')}"
            send_telegram_notification(st.session_state.telegram_bot_token, st.session_state.telegram_chat_id, tg_msg)
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
    """Renders an interactive Plotly chart: price + VWAP bands (top) + RVOL panel (bottom)."""
    df = sig_data['df'].copy()
    symbol = sig_data['symbol'].replace('.NS', '')
    live_rvol = sig_data.get('volume_ratio', None)

    # ── Compute RVOL series ─────────────────────────────────────────────────────
    if 'Vol_SMA20' in df.columns and df['Vol_SMA20'].gt(0).any():
        df['RVOL'] = df['Volume'] / df['Vol_SMA20']
    else:
        df['Vol_SMA20_calc'] = df['Volume'].rolling(window=20).mean()
        df['RVOL'] = df['Volume'] / df['Vol_SMA20_calc'].replace(0, float('nan'))
    df['RVOL'] = df['RVOL'].clip(lower=0)

    # Bar colours: gold if RVOL ≥ 2.0, slate-grey otherwise
    rvol_colors = ['#ffc107' if v >= 2.0 else '#546e7a' for v in df['RVOL'].fillna(0)]

    # ── Build subplot figure ────────────────────────────────────────────────────
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.02
    )

    # ── Row 1: Candlesticks ─────────────────────────────────────────────────────
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
        name="Price"
    ), row=1, col=1)

    # VWAP (solid orange)
    fig.add_trace(go.Scatter(
        x=df.index, y=df['VWAP'],
        line=dict(color='#ff9800', width=2),
        name="VWAP",
        text=[f"VWAP: ₹{v:.2f}" for v in df['VWAP']],
        hoverinfo='text'
    ), row=1, col=1)

    # VWAP ±0.1% bands (cyan dashed)
    vwap_upper_01 = df['VWAP'] * 1.001
    vwap_lower_01 = df['VWAP'] * 0.999
    fig.add_trace(go.Scatter(
        x=df.index, y=vwap_upper_01,
        line=dict(color='#00e5ff', width=1.2, dash='dash'),
        opacity=0.75, name="VWAP +0.1%",
        text=[f"VWAP +0.1%: ₹{v:.2f}" for v in vwap_upper_01], hoverinfo='text'
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=vwap_lower_01,
        line=dict(color='#00e5ff', width=1.2, dash='dash'),
        opacity=0.75, name="VWAP -0.1%",
        text=[f"VWAP -0.1%: ₹{v:.2f}" for v in vwap_lower_01], hoverinfo='text'
    ), row=1, col=1)

    # VWAP ±0.3% bands (orange dotted)
    vwap_upper = df['VWAP'] * 1.003
    vwap_lower = df['VWAP'] * 0.997
    fig.add_trace(go.Scatter(
        x=df.index, y=vwap_upper,
        line=dict(color='#ff9800', width=1, dash='dot'),
        opacity=0.55, name="VWAP +0.3%",
        text=[f"VWAP +0.3%: ₹{v:.2f}" for v in vwap_upper], hoverinfo='text'
    ), row=1, col=1)
    fig.add_trace(go.Scatter(
        x=df.index, y=vwap_lower,
        line=dict(color='#ff9800', width=1, dash='dot'),
        opacity=0.55, name="VWAP -0.3%",
        text=[f"VWAP -0.3%: ₹{v:.2f}" for v in vwap_lower], hoverinfo='text'
    ), row=1, col=1)

    # ── Row 2: RVOL bars ────────────────────────────────────────────────────────
    fig.add_trace(go.Bar(
        x=df.index,
        y=df['RVOL'].fillna(0),
        name="RVOL",
        marker_color=rvol_colors,
        opacity=0.85,
        text=[f"RVOL: {v:.2f}x" for v in df['RVOL'].fillna(0)],
        hoverinfo='text'
    ), row=2, col=1)

    # RVOL 2.0 reference line (dashed amber)
    fig.add_hline(
        y=2.0,
        line=dict(color='#ffc107', width=1.5, dash='dash'),
        row=2, col=1,
        annotation_text="RVOL 2.0",
        annotation_position="top left",
        annotation_font=dict(color='#ffc107', size=11)
    )

    # ── Layout ──────────────────────────────────────────────────────────────────
    rangebreak_cfg = [
        dict(bounds=["sat", "mon"]),
        dict(bounds=[15.5, 9.25], pattern="hour")
    ]
    fig.update_layout(
        height=700,
        xaxis_rangeslider_visible=False,
        paper_bgcolor='#11151c', plot_bgcolor='#11151c',
        font_color='#8a99ad',
        margin=dict(t=16, b=40, l=30, r=30),
        legend=dict(orientation="h", yanchor="top", y=1.0, xanchor="left", x=0),
        bargap=0.15,
    )
    fig.update_xaxes(gridcolor='rgba(255,255,255,0.05)', rangebreaks=rangebreak_cfg)
    fig.update_yaxes(gridcolor='rgba(255,255,255,0.05)', row=1, col=1)
    fig.update_yaxes(
        gridcolor='rgba(255,255,255,0.05)',
        title_text="RVOL",
        title_font=dict(size=11, color='#8a99ad'),
        row=2, col=1
    )
    # Hide x-axis labels on top panel (shared axis handles it)
    fig.update_xaxes(showticklabels=False, row=1, col=1)
    fig.update_xaxes(rangebreaks=rangebreak_cfg, row=2, col=1)

    st.plotly_chart(fig, width='stretch')

    display_tf = st.session_state.scan_params['timeframe'] if st.session_state.scan_params else timeframe
    rvol_label = f" · Live RVOL: <b style='color:#ffc107'>{live_rvol:.2f}x</b>" if live_rvol is not None else ""
    chart_title = f"{symbol} — Price + VWAP ±0.1% & ±0.3% · RVOL 2.0 Indicator ({display_tf})"
    st.markdown(
        f'<div style="text-align:center;color:#8a99ad;font-size:0.9rem;margin-top:-0.5rem;margin-bottom:0.8rem;">'
        f'📊 {chart_title}{rvol_label}</div>',
        unsafe_allow_html=True
    )



# ── Notification Trigger Handler ──────────────────────────────────────────────
if st.session_state.get('trigger_test_alert'):
    st.session_state.trigger_test_alert = False
    st.toast("🧪 Test Notification Alert Triggered!", icon="🔔")
    st.info("🔔 **TEST NOTIFICATION TRIGGERED:** Audio chime + Windows tray popup + browser popup sent!")
    render_notification_trigger(["RELIANCE (LONG)", "TATAMOTORS (SHORT)"], sound_enabled=enable_sound_alert, desktop_enabled=enable_desktop_alert)
    send_windows_notification("⚡ Test — Options Scanner", "Test alert: RELIANCE (LONG), TATAMOTORS (SHORT)")
    
    if enable_telegram:
        if st.session_state.telegram_bot_token and st.session_state.telegram_chat_id:
            test_tg_msg = (
                "🧪 <b>TEST ALERT — Intraday Options Scanner</b>\n\n"
                "🟢 <b>RELIANCE</b> (LONG)\n• Price: ₹2,540.50 | SL: ₹2,520.00 | Target: ₹2,581.00\n• Vol Ratio: 🔥 <b>2.45x</b> | AI: 85/100 (Grade A)\n\n"
                "🔴 <b>TATAMOTORS</b> (SHORT)\n• Price: ₹980.20 | SL: ₹995.00 | Target: ₹950.40\n• Vol Ratio: 1.85x | AI: 78/100 (Grade B)\n\n"
                f"⏱️ <b>Time:</b> {get_ist_now().strftime('%H:%M:%S IST')}"
            )
            ok, detail = send_telegram_notification(st.session_state.telegram_bot_token, st.session_state.telegram_chat_id, test_tg_msg)
            if ok:
                st.toast("📱 Telegram test alert sent to your phone!", icon="✅")
                st.success(f"📱 **Telegram Push Alert:** {detail}")
            else:
                st.toast(f"❌ Telegram alert failed: {detail}", icon="⚠️")
                st.error(f"❌ **Telegram Notification Error:** {detail}")
        else:
            st.toast("⚠️ Telegram enabled but Bot Token or Chat ID is missing.", icon="⚠️")
            st.warning("⚠️ **Telegram Config Missing:** Please enter your Bot Token and Chat ID in the sidebar under *Telegram Bot Config*.")

if st.session_state.get('notify_signals'):
    notif_list = st.session_state.notify_signals
    st.session_state.notify_signals = None  # Reset so it doesn't re-fire on non-scan reruns

    st.toast(f"🚨 SETUP FOUND: {', '.join(notif_list)}", icon="⚡")
    st.success(f"⚡ **NOTIFICATION TRIGGERED:** Found {len(notif_list)} setup(s): **{', '.join(notif_list)}**")
    render_notification_trigger(notif_list, sound_enabled=enable_sound_alert, desktop_enabled=enable_desktop_alert)
    # Windows system notification already fired synchronously inside run_scan()

# ── Layout Tabs ────────────────────────────────────────────────────────────────

tab1, tab2, tab3 = st.tabs(["🔥 Active Signals & Charts", "📋 All Scanned Tickers", "💾 Saved Recommendations DB"])

# TAB 1: Active Signals
with tab1:


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

# TAB 3: Saved Recommendations DB
with tab3:
    db_stats = get_db_stats(validity_days=7)
    avail_dates = get_available_dates(validity_days=7)
    active_backend = db_stats.get('backend', 'Local SQLite')

    st.markdown(
        f"### 💾 Saved Breakout Recommendations Database "
        f"<span style='font-size:0.85rem;padding:3px 10px;border-radius:15px;background:rgba(41,182,246,0.15);border:1px solid #29b6f6;color:#29b6f6;font-weight:600;'>"
        f"⚡ Active Backend: {active_backend}</span>",
        unsafe_allow_html=True
    )
    st.markdown(
        "All triggered breakout recommendations are saved to the database in **IST (Asia/Kolkata)**. "
        "Recommendations are automatically valid for **7 days** from their creation date, after which older entries are automatically purged."
    )

    with st.expander("☁️ Streamlit Cloud Persistence Guide (Supabase / Neon / PostgreSQL)", expanded=False):
        st.markdown("""
        **Running on Streamlit Cloud?**
        - **Local Machine**: Automatically uses local SQLite file (`options_recommendations.db`).
        - **Streamlit Cloud**: To make database recommendations persist permanently across app reboots/sleeps, add your free **Supabase** or **Neon PostgreSQL** credentials to your app's **Streamlit Secrets**:
        
        ```toml
        # In Streamlit Cloud -> Settings -> Secrets:
        [postgres]
        host = "your-supabase-or-neon-db.supabase.co"
        port = 5432
        dbname = "postgres"
        user = "postgres"
        password = "your-database-password"
        ```
        *Or simply set `DATABASE_URL = "postgresql://user:password@host:5432/dbname"` in secrets. The app automatically detects it!*
        """)

    # Trigger auto-cleanup for >7 days old entries
    deleted_old = auto_cleanup_old_recommendations(days=7)
    if deleted_old > 0:
        st.info(f"🧹 Auto-cleaned {deleted_old} expired recommendation(s) older than 7 days.")

    # Summary metrics row
    m_col1, m_col2, m_col3, m_col4 = st.columns(4)
    with m_col1:
        st.metric("Total Active Recommendations (≤7d)", f"{db_stats['total']}")
    with m_col2:
        st.metric("Long / Short Signals", f"🟢 {db_stats['long_count']} / 🔴 {db_stats['short_count']}")
    with m_col3:
        st.metric("Avg AI Confidence Score", f"{db_stats['avg_ai_score']}/100" if db_stats['avg_ai_score'] > 0 else "N/A")
    with m_col4:
        st.metric("Recorded Trading Days", f"{db_stats['distinct_dates_count']} day(s)")

    st.markdown("---")
    st.markdown("#### 🔍 Filter Recommendations by Date & Criteria")

    f_col1, f_col2, f_col3, f_col4 = st.columns([2, 2, 2, 2])
    
    with f_col1:
        date_preset = st.selectbox(
            "Date Filter Preset",
            options=["All Valid (Last 7 Days)", "Today", "Last 3 Days", "Specific Date", "Custom Date Range"],
            index=0
        )
        
    start_filter_date = None
    end_filter_date = None
    today_ist = get_ist_now().date()

    if date_preset == "Today":
        start_filter_date = today_ist.strftime("%Y-%m-%d")
        end_filter_date = today_ist.strftime("%Y-%m-%d")
    elif date_preset == "Last 3 Days":
        start_filter_date = (today_ist - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
        end_filter_date = today_ist.strftime("%Y-%m-%d")
    elif date_preset == "All Valid (Last 7 Days)":
        start_filter_date = (today_ist - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        end_filter_date = today_ist.strftime("%Y-%m-%d")
    elif date_preset == "Specific Date":
        if avail_dates:
            selected_single_date = st.selectbox("Select Date (IST)", options=avail_dates)
            start_filter_date = selected_single_date
            end_filter_date = selected_single_date
        else:
            st.info("No recorded dates yet.")
    elif date_preset == "Custom Date Range":
        d_range = st.date_input("Date Range (IST)", value=(today_ist - pd.Timedelta(days=6), today_ist))
        if isinstance(d_range, tuple) and len(d_range) == 2:
            start_filter_date = d_range[0].strftime("%Y-%m-%d")
            end_filter_date = d_range[1].strftime("%Y-%m-%d")
        elif isinstance(d_range, tuple) and len(d_range) == 1:
            start_filter_date = d_range[0].strftime("%Y-%m-%d")
            end_filter_date = d_range[0].strftime("%Y-%m-%d")

    with f_col2:
        symbol_search = st.text_input("Filter Symbol", placeholder="e.g. RELIANCE").strip()

    with f_col3:
        direction_filter = st.selectbox("Direction Filter", options=["ALL", "LONG", "SHORT"])

    with f_col4:
        db_min_ai = st.slider("Min AI Score", min_value=0, max_value=100, value=0, step=5)

    # Fetch recommendations from database
    db_recs = get_recommendations(
        start_date=start_filter_date,
        end_date=end_filter_date,
        symbol=symbol_search,
        direction=direction_filter,
        min_ai_score=db_min_ai,
        validity_days=7
    )

    if not db_recs:
        st.warning("No recommendations match the selected filters within the 7-day validity window.")
    else:
        st.success(f"Found {len(db_recs)} recommendation(s) matching your criteria.")

        # Convert to DataFrame for table display
        df_recs = pd.DataFrame(db_recs)
        
        # Display formatted table columns
        display_cols = [
            "timestamp_ist", "symbol", "direction", "current_price",
            "stop_loss", "target", "risk", "volume_ratio",
            "ai_score", "ai_grade", "candle_type", "days_remaining"
        ]
        
        # Rename for clean UI header display
        rename_map = {
            "timestamp_ist": "Timestamp (IST)",
            "symbol": "Symbol",
            "direction": "Direction",
            "current_price": "Price (₹)",
            "stop_loss": "Stop Loss (₹)",
            "target": "Target (₹)",
            "risk": "Risk (₹)",
            "volume_ratio": "Vol Ratio",
            "ai_score": "AI Score",
            "ai_grade": "AI Grade",
            "candle_type": "Candle Type",
            "days_remaining": "Days Remaining"
        }
        
        # Present table
        view_df = df_recs[display_cols].rename(columns=rename_map)
        st.dataframe(view_df, width='stretch')

        # CSV Export and Clear All Row
        col_exp, col_clr = st.columns([3, 1])
        with col_exp:
            csv_data = df_recs.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Export Filtered Recommendations (CSV)",
                data=csv_data,
                file_name=f"options_recommendations_{get_ist_now().strftime('%Y%m%d_%H%M%S')}.csv",
                mime="text/csv"
            )
        with col_clr:
            if st.button("🗑️ Clear All DB Records"):
                c_num = clear_all_recommendations()
                st.success(f"Cleared {c_num} recommendation(s) from database.")
                st.rerun()

        # Recommendation Details Expander
        st.markdown("#### 🔍 Detailed Recommendation Cards & Actions")
        for rec in db_recs:
            rec_id = rec['id']
            sym = rec['symbol']
            dir_str = rec['direction']
            ts = rec['timestamp_ist']
            ai_sc = rec['ai_score']
            ai_gr = rec['ai_grade']
            badge_color = "#28a745" if dir_str == "LONG" else "#dc3545"
            
            exp_label = f"📌 #{rec_id} · {sym} | {dir_str} Breakout | Price: ₹{rec['current_price']:.2f} | Time: {ts} | AI Score: {ai_sc if ai_sc else 'N/A'}"
            with st.expander(exp_label):
                c1, c2, c3 = st.columns(3)
                with c1:
                    st.markdown(f"**Symbol:** `{sym}`")
                    st.markdown(f"**Direction:** <span style='color:{badge_color};font-weight:bold;'>{dir_str}</span>", unsafe_allow_html=True)
                    st.markdown(f"**Live Price:** ₹{rec['current_price']:.2f}")
                    st.markdown(f"**Candle Type:** {rec['candle_type']}")
                with c2:
                    st.markdown(f"**Stop Loss:** ₹{rec['stop_loss']:.2f} ({rec['sl_source']})")
                    st.markdown(f"**Target:** ₹{rec['target']:.2f}")
                    st.markdown(f"**Risk:** ₹{rec['risk']:.2f}")
                    st.markdown(f"**Volume Ratio:** {rec['volume_ratio']:.2f}x")
                with c3:
                    st.markdown(f"**Created Date (IST):** `{rec['created_date']}`")
                    st.markdown(f"**Timestamp (IST):** `{ts}`")
                    st.markdown(f"**Validity Remaining:** `{rec['days_remaining']} day(s)`")
                    if ai_sc:
                        st.markdown(f"**AI Score:** `{ai_sc}/100` (Grade `{ai_gr}`)")
                    
                    st.markdown("---")
                    if st.button(f"🗑️ Delete Recommendation #{rec_id}", key=f"del_rec_{rec_id}"):
                        if delete_recommendation(rec_id):
                            st.toast(f"Deleted recommendation #{rec_id} ({sym})", icon="🗑️")
                            st.rerun()
                        
                if rec.get('ai_reasoning'):
                    st.markdown("**🤖 AI Reasoning:**")
                    for bullet in rec['ai_reasoning'].split('|'):
                        if bullet.strip():
                            st.markdown(f"• {bullet.strip()}")
                if rec.get('ai_trade_advice'):
                    st.info(f"💡 Trade Advice: {rec['ai_trade_advice']}")
                if rec.get('news_sentiment'):
                    st.markdown(f"**📰 News Sentiment:** {rec['news_sentiment']} — {rec.get('news_summary', '')}")

# ── Autorefresh runner logic ──────────────────────────────────────────────────
if auto_refresh:
    _in_market_now = _is_market_hours()
    _now_ist = get_ist_now()

    if _in_market_now:
        # ── In-market: scan at user's chosen interval ───────────────────────
        st.sidebar.success(
            f"🟢 **Market Open** — scanning every **{refresh_choice}**"
        )
        time.sleep(refresh_interval)
        st.rerun()
    else:
        # ── Outside market hours: sleep 1 hour then re-check ─────────────
        # Calculate next market open (08:45 IST next trading day)
        _market_open_today = _now_ist.replace(hour=8, minute=45, second=0, microsecond=0)
        if _now_ist < _market_open_today and _now_ist.weekday() < 5:
            _next_open = _market_open_today
        else:
            # advance to next weekday
            _days_ahead = 1
            while (_now_ist + __import__('datetime').timedelta(days=_days_ahead)).weekday() >= 5:
                _days_ahead += 1
            _next_day = _now_ist + __import__('datetime').timedelta(days=_days_ahead)
            _next_open = _next_day.replace(hour=8, minute=45, second=0, microsecond=0)

        _mins_to_open = max(0, int((_next_open - _now_ist).total_seconds() // 60))
        _hrs, _mins = divmod(_mins_to_open, 60)
        _eta = f"{_hrs}h {_mins}m" if _hrs else f"{_mins}m"

        st.sidebar.warning(
            f"🔴 **Market Closed** — next check in **1 hour**\n"
            f"🕓 Market opens in **{_eta}** | Saved interval: **{st.session_state.saved_refresh_choice}**"
        )
        time.sleep(3600)   # 1-hour heartbeat during off-hours
        st.rerun()
