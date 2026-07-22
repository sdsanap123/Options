import yfinance as yf
import pandas as pd
import numpy as np
import logging
import requests
from typing import Dict, Optional, List, Tuple
from utils.indicators import calculate_ema, calculate_vwap, calculate_supertrend

logger = logging.getLogger(__name__)

def fetch_intraday_data(symbol: str, interval: str = "5m", period: str = "5d") -> Optional[pd.DataFrame]:
    """
    Fetch historical intraday data for a given symbol.
    period='5d' or '2d' ensures enough data for indicators like 20-period SMA/EMA.
    """
    ticker_symbol = symbol
    if not ticker_symbol.endswith('.NS') and '.' not in ticker_symbol:
        ticker_symbol += '.NS'

    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period=period, interval=interval)

        if df.empty:
            logger.error(f"Empty dataframe returned for {ticker_symbol}. It may be delisted, have no data for the requested period, or yfinance is rate-limited.")
            return None
            
        if len(df) < 30:
            logger.warning(f"Insufficient data returned for {ticker_symbol} (Rows: {len(df)}). Needed at least 30 for indicators.")
            return None

        # Clean columns if multi-level index is returned
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        # Convert index to Asia/Kolkata timezone
        if df.index.tz is None:
            df.index = df.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
        else:
            df.index = df.index.tz_convert('Asia/Kolkata')

        # Filter out Saturday and Sunday
        df = df[df.index.dayofweek < 5]

        # Filter for market hours (09:15 to 15:30)
        start_time = pd.Timestamp("09:15").time()
        end_time = pd.Timestamp("15:30").time()
        df = df[(df.index.time >= start_time) & (df.index.time <= end_time)]

        if len(df) < 30:
            logger.warning(f"Insufficient data returned for {ticker_symbol} (Rows: {len(df)}). Needed at least 30 for indicators.")
            return None

        return df

    except Exception as e:
        logger.error(f"Exception encountered while fetching data for {ticker_symbol}: {e}", exc_info=True)
        return None


def fetch_batch_intraday_data(tickers: List[str], interval: str = "5m", period: str = "5d") -> Tuple[Dict[str, pd.DataFrame], Optional[str]]:
    """
    Fetch historical intraday data for a batch of tickers using yf.download.
    Returns a tuple of (dictionary mapping ticker symbol to DataFrame, error_message).
    """
    if not tickers:
        return {}, "No tickers specified."

    # Standardize tickers to have .NS suffix if needed
    standardized_tickers = []
    ticker_map = {}  # Maps standardized back to original input symbol
    for t in tickers:
        std_t = t
        if not std_t.endswith('.NS') and '.' not in std_t:
            std_t += '.NS'
        standardized_tickers.append(std_t)
        ticker_map[std_t] = t

    try:
        logger.info(f"Downloading batch intraday data for {len(standardized_tickers)} tickers...")
        # Use yf.download to get all tickers in one request
        combined_df = yf.download(
            tickers=standardized_tickers,
            period=period,
            interval=interval,
            group_by='ticker',
            threads=True,
            progress=False
        )

        result_dfs = {}

        if combined_df.empty:
            logger.error("Empty batch dataframe returned from yf.download. Checking connectivity...")
            # Try to hit a basic Yahoo Finance endpoint to diagnose the exact issue
            try:
                test_url = "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?period1=1&period2=2&interval=1d"
                headers = {"User-Agent": "Mozilla/5.0"}
                r = requests.get(test_url, headers=headers, timeout=5)
                if r.status_code == 429:
                    return {}, "Rate Limited (HTTP 429) by Yahoo Finance. Please wait before scanning again."
                elif r.status_code == 403:
                    return {}, "IP Blocked/Forbidden (HTTP 403) by Yahoo Finance."
                elif r.status_code != 200:
                    return {}, f"Yahoo Finance API returned HTTP error: {r.status_code}."
            except Exception as net_err:
                return {}, f"Network Connectivity Issue: Could not reach Yahoo Finance ({str(net_err)})"
            return {}, "Yahoo Finance returned no data (Possible tickers error or closed market)."

        is_multi = isinstance(combined_df.columns, pd.MultiIndex)

        # Process each ticker's data
        for std_t in standardized_tickers:
            if is_multi:
                if std_t not in combined_df.columns.levels[0]:
                    logger.warning(f"No data returned for ticker {std_t} in batch download.")
                    continue
                df = combined_df[std_t].dropna(how='all')
            else:
                # Fallback if single ticker download returned flat columns
                df = combined_df.dropna(how='all')

            if df.empty:
                continue

            # Convert index to Asia/Kolkata timezone
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
            else:
                df.index = df.index.tz_convert('Asia/Kolkata')

            # Filter out Saturday and Sunday
            df = df[df.index.dayofweek < 5]

            # Filter for market hours (09:15 to 15:30)
            start_time = pd.Timestamp("09:15").time()
            end_time = pd.Timestamp("15:30").time()
            df = df[(df.index.time >= start_time) & (df.index.time <= end_time)]

            if len(df) < 30:
                logger.warning(f"Insufficient data returned for {std_t} in batch (Rows: {len(df)}). Needed at least 30.")
                continue

            orig_t = ticker_map[std_t]
            result_dfs[orig_t] = df

        logger.info(f"Successfully processed {len(result_dfs)} out of {len(tickers)} tickers in batch download.")

        if not result_dfs:
            return {}, "No tickers had sufficient data (Need at least 30 candles within market hours)."

        return result_dfs, None

    except Exception as e:
        error_str = str(e)
        logger.error(f"Exception during batch fetch: {error_str}", exc_info=True)

        # Check for common network error signatures
        if any(term in error_str for term in ["Connection", "Max retries", "NameResolutionError", "getaddrinfo"]):
            return {}, "Network Connectivity Issue: Could not connect to Yahoo Finance. Check your internet connection."
        elif "429" in error_str:
            return {}, "Rate Limited (HTTP 429) by Yahoo Finance. Please wait before scanning again."
        elif "403" in error_str:
            return {}, "IP Blocked/Forbidden (HTTP 403) by Yahoo Finance."
        else:
            return {}, f"Download Failed: {error_str}"


def analyze_ticker(
    symbol: str, 
    interval: str = "5m", 
    period: str = "5d", 
    st_lookback: int = 0, 
    ignore_volume: bool = False, 
    pre_fetched_df: Optional[pd.DataFrame] = None,
    st_period: int = 10,
    st_multiplier: float = 1.2,
    min_vol_ratio: float = 1.5
) -> Optional[Dict]:
    """
    Anticipation + Early Trigger Evaluation Engine:
    1. Background Trend State: Supertrend (10, 1.2) direction (1 for Long, -1 for Short).
    2. Proximity to Value: Price above VWAP (Long) or below VWAP (Short) and within 0.1% distance of VWAP or 9 EMA.
    3. Micro-Breakout: Live Close > prev candle High (Long) or Live Close < prev candle Low (Short).
    4. Time-Weighted Projected Volume on Live Candle: Projected Vol > 1.5x Volume SMA 20.
    """
    if pre_fetched_df is not None:
        df = pre_fetched_df.copy()
    else:
        df = fetch_intraday_data(symbol, interval=interval, period=period)

    if df is None or len(df) < 25:
        return None
        
    try:
        # Calculate Indicators
        df['EMA_9'] = calculate_ema(df, period=9)
        df['VWAP'] = calculate_vwap(df)
        
        st_df = calculate_supertrend(df, period=st_period, multiplier=st_multiplier)
        df = pd.concat([df, st_df], axis=1)
        
        # Volume SMA 20
        df['Vol_SMA20'] = df['Volume'].rolling(window=20).mean()
        
        # Determine timeframe interval in seconds for time-weighted live volume projection
        tf_minutes = int(interval.replace('m', '')) if 'm' in interval else 5
        tf_seconds = tf_minutes * 60.0

        # Check candles (live candle offset=-1 evaluated first, then closed offset=-2)
        for offset in [-1, -2]:
            if abs(offset) > len(df):
                continue
                
            row = df.iloc[offset]
            
            close = float(row['Close'])
            volume = float(row['Volume'])
            vol_sma = float(row['Vol_SMA20']) if 'Vol_SMA20' in row and row['Vol_SMA20'] > 0 else 0
            vwap = float(row['VWAP'])
            ema_9 = float(row['EMA_9'])
            st_line = float(row['ST_Line'])
            st_dir = int(row['ST_Direction'])
            
            # Time-Weighted Projected Volume Calculation on Live Candle
            if offset == -1 and vol_sma > 0:
                try:
                    candle_ts = df.index[-1]
                    now_ts = pd.Timestamp.now(tz=candle_ts.tz) if candle_ts.tz is not None else pd.Timestamp.now()
                    elapsed_sec = (now_ts - candle_ts).total_seconds()
                    # Bound elapsed seconds between 10s and tf_seconds
                    elapsed_sec = max(10.0, min(tf_seconds, elapsed_sec))
                    projected_volume = (volume / elapsed_sec) * tf_seconds
                    effective_vol_ratio = projected_volume / vol_sma
                except Exception:
                    effective_vol_ratio = volume / vol_sma if vol_sma > 0 else 0
            else:
                effective_vol_ratio = volume / vol_sma if vol_sma > 0 else 0

            # 1. Volume Velocity Condition
            vol_condition = ignore_volume or (effective_vol_ratio > min_vol_ratio)
            
            # 2. Background Trend State Filter (Supertrend 10, 1.2)
            state_long = st_dir == 1
            state_short = st_dir == -1
            
            if st_lookback > 0:
                start_idx = max(0, len(df) + offset - st_lookback)
                end_idx = len(df) + offset + 1
                trigger_window = df.iloc[start_idx:end_idx]
                state_long = state_long and trigger_window['ST_Buy_Trigger'].any()
                state_short = state_short and trigger_window['ST_Sell_Trigger'].any()

            # 3. Proximity to Value Filter (within 0.1% distance of VWAP or 9 EMA)
            # Long: Close > VWAP and within 0.1% of VWAP or 9 EMA
            vwap_dist_long = (close - vwap) / vwap
            ema_dist = abs(close - ema_9) / ema_9
            prox_long = (close > vwap) and (vwap_dist_long <= 0.001 or ema_dist <= 0.001)

            # Short: Close < VWAP and within 0.1% of VWAP or 9 EMA
            vwap_dist_short = (vwap - close) / vwap
            prox_short = (close < vwap) and (vwap_dist_short <= 0.001 or ema_dist <= 0.001)

            # 4. Micro-Breakout Filter (vs previous closed candle offset -2)
            prev_row = df.iloc[offset - 1] if len(df) >= abs(offset - 1) else row
            prev_high = float(prev_row['High'])
            prev_low = float(prev_row['Low'])

            micro_breakout_long = close > prev_high
            micro_breakout_short = close < prev_low

            is_long_setup = vol_condition and state_long and prox_long and micro_breakout_long
            is_short_setup = vol_condition and state_short and prox_short and micro_breakout_short
            
            if is_long_setup or is_short_setup:
                direction = "LONG" if is_long_setup else "SHORT"
                
                # Stop Loss Placement & Risk Floor (0.05%)
                if direction == "LONG":
                    sl_raw = min(ema_9, vwap)
                else:
                    sl_raw = max(ema_9, vwap)
                    
                risk = max(abs(close - sl_raw), close * 0.0005)
                
                if direction == "LONG":
                    stop_loss = close - risk
                    target_1_5 = close + (1.5 * risk)
                    target_2_0 = close + (2.0 * risk)
                    sl_source = "min(9 EMA, VWAP)"
                else:
                    stop_loss = close + risk
                    target_1_5 = close - (1.5 * risk)
                    target_2_0 = close - (2.0 * risk)
                    sl_source = "max(9 EMA, VWAP)"
                    
                return {
                    'symbol': symbol,
                    'timestamp': df.index[offset].strftime('%H:%M:%S') if hasattr(df.index[offset], 'strftime') else str(df.index[offset]),
                    'direction': direction,
                    'current_price': close,
                    'vwap': vwap,
                    'ema_9': ema_9,
                    'supertrend': st_line,
                    'volume': volume,
                    'vol_sma20': vol_sma,
                    'volume_ratio': effective_vol_ratio,
                    'stop_loss': stop_loss,
                    'sl_source': sl_source,
                    'target_1_5': target_1_5,
                    'target_2_0': target_2_0,
                    'risk': risk,
                    'df': df.tail(50),
                    'candle_type': 'Live Candle (-1)' if offset == -1 else 'Closed Candle (-2)',
                    'setup_triggered': True
                }
                
        # If no setup triggered, return default metrics from the latest candle (offset -1)
        row = df.iloc[-1]
        close = float(row['Close'])
        volume = float(row['Volume'])
        vol_sma = float(row['Vol_SMA20']) if 'Vol_SMA20' in row and row['Vol_SMA20'] > 0 else 0
        vwap = float(row['VWAP']) if 'VWAP' in row else close
        ema_9 = float(row['EMA_9']) if 'EMA_9' in row else close
        st_line = float(row['ST_Line']) if 'ST_Line' in row else close
        
        return {
            'symbol': symbol,
            'timestamp': df.index[-1].strftime('%H:%M:%S') if hasattr(df.index[-1], 'strftime') else str(df.index[-1]),
            'direction': 'N/A',
            'current_price': close,
            'vwap': vwap,
            'ema_9': ema_9,
            'supertrend': st_line,
            'volume': volume,
            'vol_sma20': vol_sma,
            'volume_ratio': volume / vol_sma if vol_sma > 0 else 0,
            'stop_loss': close,
            'sl_source': 'N/A',
            'target_1_5': close,
            'target_2_0': close,
            'risk': 0.0,
            'df': df.tail(50),
            'candle_type': 'Live/Incomplete',
            'setup_triggered': False
        }
        
    except Exception as e:
        logger.error(f"Error analyzing {symbol}: {str(e)}")
        return None
