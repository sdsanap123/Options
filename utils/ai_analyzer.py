import os
import json
import logging
import yfinance as yf
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Groq client (lazy-loaded) ─────────────────────────────────────────────────
_groq_client = None

def _get_client():
    global _groq_client
    if _groq_client is None:
        try:
            from groq import Groq
            api_key = os.getenv("GROQ_API_KEY", "")
            if not api_key:
                raise ValueError("GROQ_API_KEY environment variable not set.")
            _groq_client = Groq(api_key=api_key)
            logger.info("Groq client initialised successfully.")
        except ImportError:
            raise RuntimeError("groq package not installed. Run: pip install groq")
    return _groq_client

_MODEL = "llama-3.3-70b-versatile"

# ─────────────────────────────────────────────────────────────────────────────
# OPTION 2 — Signal Confidence Scoring
# ─────────────────────────────────────────────────────────────────────────────

def score_signal(signal: dict) -> dict:
    """
    Score a detected breakout signal from 0–100 using Groq LLM.
    Returns:
        {
            'score': int,          # 0–100 confidence
            'grade': str,          # A / B / C / D
            'reasoning': str,      # Short bullet-point explanation
            'trade_advice': str,   # One-line trade suggestion
        }
    """
    default = {'score': 50, 'grade': 'C', 'reasoning': 'AI unavailable.', 'trade_advice': 'Proceed with caution.'}
    try:
        client = _get_client()

        direction     = signal.get('direction', 'N/A')
        close         = signal.get('current_price', 0)
        vwap          = signal.get('vwap', 0)
        ema_9         = signal.get('ema_9', 0)
        st_line       = signal.get('supertrend', 0)
        vol_ratio     = signal.get('volume_ratio', 0)
        risk          = signal.get('risk', 0)
        stop_loss     = signal.get('stop_loss', 0)
        target_1_5    = signal.get('target_1_5', 0)
        target_2_0    = signal.get('target_2_0', 0)
        sl_source     = signal.get('sl_source', 'N/A')
        candle_type   = signal.get('candle_type', 'N/A')
        symbol        = signal.get('symbol', 'N/A').replace('.NS', '')

        # Derived metrics
        vwap_dist_pct  = ((close - vwap) / vwap * 100) if vwap else 0
        ema_dist_pct   = ((close - ema_9) / ema_9 * 100) if ema_9 else 0
        st_dist_pct    = ((close - st_line) / st_line * 100) if st_line else 0
        rr_ratio       = abs(target_2_0 - close) / risk if risk > 0 else 0
        hour_now       = datetime.now().hour
        minute_now     = datetime.now().minute
        time_str       = f"{hour_now:02d}:{minute_now:02d} IST"
        # Premium trading window: 09:20–10:30 AM IST
        in_prime_window = (9, 20) <= (hour_now, minute_now) <= (10, 30)

        prompt = f"""You are an expert Indian intraday options trader. Analyze this breakout signal and give a confidence score.

SIGNAL DETAILS:
- Stock: {symbol} | Direction: {direction}
- Current Price: ₹{close:.2f}
- VWAP: ₹{vwap:.2f} (Price is {vwap_dist_pct:+.2f}% {'above' if vwap_dist_pct > 0 else 'below'} VWAP)
- 9 EMA: ₹{ema_9:.2f} (Price is {ema_dist_pct:+.2f}% from 9 EMA)
- Supertrend Line: ₹{st_line:.2f} (Price is {st_dist_pct:+.2f}% from ST)
- Volume Ratio: {vol_ratio:.2f}x average (breakout threshold: 2.0x)
- Stop-Loss: ₹{stop_loss:.2f} (Source: {sl_source}) | Risk: ₹{risk:.2f}
- Target 1.5R: ₹{target_1_5:.2f} | Target 2.0R: ₹{target_2_0:.2f}
- Risk-to-Reward: 1:{rr_ratio:.1f}
- Candle Type: {candle_type}
- Signal Time: {time_str} | Prime Window (09:20–10:30): {'YES ✅' if in_prime_window else 'NO ⚠️'}

SCORING CRITERIA (weight each factor):
1. Volume strength (25%): Vol ratio > 3x = very strong. Between 2-3x = moderate.
2. VWAP distance (20%): Ideal: 0.3%–1.5% above/below VWAP. Too far = chasing.
3. EMA alignment (20%): Price close to but above/below 9 EMA = cleaner entry.
4. Supertrend confidence (20%): Tighter ST distance = more reliable support/resistance.
5. Time of day (15%): 09:20–10:30 AM IST is the prime breakout window.

Respond ONLY with a valid JSON object in this exact format (no markdown, no extra text):
{{
  "score": <integer 0-100>,
  "grade": "<A|B|C|D>",
  "reasoning": "<3-4 concise bullet points separated by |>",
  "trade_advice": "<one actionable sentence>"
}}

Grade scale: A=80-100 (high conviction), B=60-79 (decent setup), C=40-59 (borderline), D=0-39 (weak/skip)."""

        response = client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=300,
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)

        # Validate and clamp score
        result['score'] = max(0, min(100, int(result.get('score', 50))))
        result['grade'] = result.get('grade', 'C')
        result['reasoning'] = result.get('reasoning', 'No reasoning provided.')
        result['trade_advice'] = result.get('trade_advice', 'Monitor closely.')
        return result

    except Exception as e:
        logger.error(f"AI scoring failed for {signal.get('symbol', '?')}: {e}")
        return default


# ─────────────────────────────────────────────────────────────────────────────
# OPTION 3 — News Sentiment Filter
# ─────────────────────────────────────────────────────────────────────────────

def get_news_sentiment(symbol: str) -> dict:
    """
    Fetch recent news headlines via yfinance and classify sentiment with Groq.
    Returns:
        {
            'sentiment': str,       # 'Positive' | 'Neutral' | 'Negative'
            'emoji': str,           # 🟢 | 🟡 | 🔴
            'summary': str,         # 1-2 sentence summary
            'should_trade': bool,   # False if strongly negative news
            'headlines': list[str], # Top headlines fetched
        }
    """
    default = {
        'sentiment': 'Neutral', 'emoji': '🟡',
        'summary': 'No news data available.',
        'should_trade': True, 'headlines': []
    }
    try:
        client = _get_client()

        ticker_sym = symbol if symbol.endswith('.NS') else symbol + '.NS'
        ticker = yf.Ticker(ticker_sym)
        news_data = ticker.news  # list of dicts with 'title', 'publisher', etc.

        if not news_data:
            logger.info(f"No news found for {symbol}.")
            return default

        # Extract up to 8 most recent headlines
        headlines = []
        for item in news_data[:8]:
            content = item.get('content', {})
            title = (
                content.get('title')
                or item.get('title')
                or ''
            )
            if title:
                headlines.append(title)

        if not headlines:
            return default

        headlines_str = "\n".join(f"- {h}" for h in headlines)
        clean_symbol  = symbol.replace('.NS', '')

        prompt = f"""You are a financial news analyst specializing in Indian stock markets.

Stock: {clean_symbol}
Recent News Headlines:
{headlines_str}

Analyze the above headlines and determine their OVERALL impact on intraday trading for this stock TODAY.

Respond ONLY with a valid JSON object (no markdown, no extra text):
{{
  "sentiment": "<Positive|Neutral|Negative>",
  "summary": "<1-2 sentences: what the news means for intraday traders>",
  "should_trade": <true if safe to trade despite news, false if strongly negative catalysts like fraud/halt/crash>,
  "risk_flag": "<None|Earnings Risk|Regulatory Risk|Macro Risk|Sector Weakness>"
}}"""

        response = client.chat.completions.create(
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = json.loads(raw)

        sentiment = result.get('sentiment', 'Neutral')
        emoji_map = {'Positive': '🟢', 'Neutral': '🟡', 'Negative': '🔴'}

        return {
            'sentiment': sentiment,
            'emoji': emoji_map.get(sentiment, '🟡'),
            'summary': result.get('summary', ''),
            'should_trade': bool(result.get('should_trade', True)),
            'risk_flag': result.get('risk_flag', 'None'),
            'headlines': headlines,
        }

    except Exception as e:
        logger.error(f"News sentiment failed for {symbol}: {e}")
        return default
