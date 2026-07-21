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


def analyze_ticker(symbol: str, interval: str = "5m", period: str = "5d", st_lookback: int = 5, ignore_volume: bool = False, pre_fetched_df: Optional[pd.DataFrame] = None) -> Optional[Dict]:
    """
    Analyze a ticker for intraday breakout setup:
    1. Volume breakout: Vol > 2x the 20-period Average Volume (unless ignore_volume is True).
    2. Close relation to VWAP.
    3. Supertrend (7, 3) crossover (flipped green for long, red for short) within the st_lookback window.
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
        
        st_df = calculate_supertrend(df, period=7, multiplier=3.0)
        df = pd.concat([df, st_df], axis=1)
        
        # Volume SMA 20
        df['Vol_SMA20'] = df['Volume'].rolling(window=20).mean()
        
        # Check the last two candles (current live and previous closed)
        for offset in [-1, -2]:
            if abs(offset) > len(df):
                continue
                
            row = df.iloc[offset]
            
            close = float(row['Close'])
            volume = float(row['Volume'])
            vol_sma = float(row['Vol_SMA20'])
            vwap = float(row['VWAP'])
            ema_9 = float(row['EMA_9'])
            st_line = float(row['ST_Line'])
            st_dir = int(row['ST_Direction'])
            
            # 1. Volume filter: Current volume > 2.5 * Volume_SMA20 (or custom multiplier)
            # Default to True if ignore_volume is checked
            vol_condition = ignore_volume or (volume > (2.5 * vol_sma) if vol_sma > 0 else False)
            
            # 2. VWAP filter: Close > VWAP for Long, Close < VWAP for Short (with 0.2% margin of safety)
            above_vwap = close > (vwap * 1.002)
            below_vwap = close < (vwap * 0.998)
            
            # 3. Supertrend filter: Check if flipped green (long) or red (short)
            # If st_lookback == 0, we just check if it's currently in that direction (active trend)
            if st_lookback == 0:
                flipped_green = st_dir == 1
                flipped_red = st_dir == -1
            else:
                # We check if there was a trigger in the lookback window
                # [offset - st_lookback, offset]
                start_idx = len(df) + offset - st_lookback
                end_idx = len(df) + offset + 1 # inclusive of current offset
                if start_idx < 0:
                    start_idx = 0
                
                trigger_window = df.iloc[start_idx:end_idx]
                flipped_green = trigger_window['ST_Buy_Trigger'].any()
                flipped_red = trigger_window['ST_Sell_Trigger'].any()
                
            # Long Setup
            is_long_setup = vol_condition and above_vwap and flipped_green
            # Short Setup
            is_short_setup = vol_condition and below_vwap and flipped_red
            
            if is_long_setup or is_short_setup:
                direction = "LONG" if is_long_setup else "SHORT"
                
                # Entry range: between 9 EMA and VWAP
                # Calculate stop loss: 9 EMA or Supertrend line (whichever is closer to current price)
                st_dist = abs(close - st_line)
                ema_dist = abs(close - ema_9)
                
                if ema_dist < st_dist:
                    stop_loss = ema_9
                    sl_source = "9 EMA"
                else:
                    stop_loss = st_line
                    sl_source = "Supertrend"
                    
                # Risk calculation
                risk = abs(close - stop_loss)
                min_risk = close * 0.0005
                if risk < min_risk:
                    risk = min_risk
                    stop_loss = close - min_risk if direction == "LONG" else close + min_risk
                
                # Targets: 1:2 to 1:3 Risk-to-Reward ratio (Bigger margin of win)
                if direction == "LONG":
                    target_1_5 = close + (2.0 * risk)
                    target_2_0 = close + (3.0 * risk)
                else: # SHORT
                    target_1_5 = close - (2.0 * risk)
                    target_2_0 = close - (3.0 * risk)
                    
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
                    'volume_ratio': volume / vol_sma if vol_sma > 0 else 0,
                    'stop_loss': stop_loss,
                    'sl_source': sl_source,
                    'target_1_5': target_1_5,
                    'target_2_0': target_2_0,
                    'risk': risk,
                    'df': df.tail(50), # Keep tail for plotting
                    'candle_type': 'Live/Incomplete' if offset == -1 else 'Closed',
                    'setup_triggered': True
                }
                
        # If no setup triggered, return default metrics from the latest candle (offset -1)
        row = df.iloc[-1]
        close = float(row['Close'])
        volume = float(row['Volume'])
        vol_sma = float(row['Vol_SMA20']) if 'Vol_SMA20' in row else 0
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
