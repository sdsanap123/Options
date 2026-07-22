import numpy as np
import pandas as pd

def calculate_ema(df: pd.DataFrame, period: int = 9, column: str = 'Close') -> pd.Series:
    """Calculate Exponential Moving Average."""
    return df[column].ewm(span=period, adjust=False).mean()

def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """
    Calculate intraday Volume Weighted Average Price (VWAP).
    VWAP reset is applied daily.
    """
    # Make a copy to avoid modifications
    df = df.copy()
    
    # Calculate Typical Price
    typical_price = (df['High'] + df['Low'] + df['Close']) / 3.0
    typical_price_volume = typical_price * df['Volume']
    
    # Check if df has a datetime index to group by date
    if isinstance(df.index, pd.DatetimeIndex):
        dates = df.index.date
    else:
        # Fallback if index is not datetime
        return (typical_price * df['Volume']).cumsum() / df['Volume'].cumsum()
        
    # Group by date and calculate cumulative sum
    df['TP_Vol'] = typical_price_volume
    df['Vol_cum'] = df.groupby(dates)['Volume'].cumsum()
    df['TP_Vol_cum'] = df.groupby(dates)['TP_Vol'].cumsum()
    
    # Avoid division by zero
    vwap = df['TP_Vol_cum'] / df['Vol_cum'].replace(0, np.nan)
    # Fill any NaN with close price
    vwap = vwap.ffill().bfill()
    return vwap

def calculate_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 1.2) -> pd.DataFrame:
    """
    Calculate Supertrend (period, multiplier).
    Returns a DataFrame with columns:
      - 'ST_Line': The Supertrend line value
      - 'ST_Direction': 1 for green/bullish, -1 for red/bearish
      - 'ST_Buy_Trigger': True if flipped to bullish on this candle
      - 'ST_Sell_Trigger': True if flipped to bearish on this candle
    """
    df = df.copy()
    
    # Calculate ATR (Average True Range)
    high = df['High']
    low = df['Low']
    close = df['Close']
    
    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/period, adjust=False).mean() # standard ATR calculation (Wilder's EMA)
    
    # Basic Bands
    hl2 = (high + low) / 2
    basic_upper = hl2 + (multiplier * atr)
    basic_lower = hl2 - (multiplier * atr)
    
    # Final Bands
    final_upper = pd.Series(0.0, index=df.index)
    final_lower = pd.Series(0.0, index=df.index)
    st_line = pd.Series(0.0, index=df.index)
    direction = pd.Series(1, index=df.index) # 1: Bullish, -1: Bearish
    
    # Initialize first values
    if len(df) > 0:
        final_upper.iloc[0] = basic_upper.iloc[0]
        final_lower.iloc[0] = basic_lower.iloc[0]
        st_line.iloc[0] = basic_lower.iloc[0]
        direction.iloc[0] = 1
        
    for i in range(1, len(df)):
        # Upper band calculation
        if basic_upper.iloc[i] < final_upper.iloc[i-1] or close.iloc[i-1] > final_upper.iloc[i-1]:
            final_upper.iloc[i] = basic_upper.iloc[i]
        else:
            final_upper.iloc[i] = final_upper.iloc[i-1]
            
        # Lower band calculation
        if basic_lower.iloc[i] > final_lower.iloc[i-1] or close.iloc[i-1] < final_lower.iloc[i-1]:
            final_lower.iloc[i] = basic_lower.iloc[i]
        else:
            final_lower.iloc[i] = final_lower.iloc[i-1]
            
        # Direction calculation
        if close.iloc[i] > final_upper.iloc[i]:
            direction.iloc[i] = 1
        elif close.iloc[i] < final_lower.iloc[i]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i-1]
            
        # Supertrend line
        if direction.iloc[i] == 1:
            st_line.iloc[i] = final_lower.iloc[i]
        else:
            st_line.iloc[i] = final_upper.iloc[i]
            
    # Trigger conditions (flip)
    st_buy_trigger = (direction == 1) & (direction.shift(1) == -1)
    st_sell_trigger = (direction == -1) & (direction.shift(1) == 1)
    
    result = pd.DataFrame({
        'ST_Line': st_line,
        'ST_Direction': direction,
        'ST_Buy_Trigger': st_buy_trigger,
        'ST_Sell_Trigger': st_sell_trigger
    }, index=df.index)
    
    return result
