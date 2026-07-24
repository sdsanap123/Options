import sqlite3
import os
import json
import logging
from datetime import datetime, timedelta
import pytz
from typing import List, Dict, Optional, Tuple, Any

logger = logging.getLogger(__name__)

# Timezone constant
IST = pytz.timezone('Asia/Kolkata')

# ─────────────────────────────────────────────────────────────────────────────
# DATABASE CONNECTION & DIALECT ABSTRACTION LAYER
# ─────────────────────────────────────────────────────────────────────────────

def get_db_path() -> str:
    """Get absolute path to local SQLite database file."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, "options_recommendations.db")

def get_ist_now() -> datetime:
    """Return current datetime in IST timezone."""
    return datetime.now(IST)

def get_ist_date_str() -> str:
    """Return current date string in IST (YYYY-MM-DD)."""
    return get_ist_now().strftime("%Y-%m-%d")

def _get_cloud_config() -> Optional[Dict[str, Any]]:
    """
    Check if cloud database credentials exist in Streamlit secrets or environment variables.
    Supports PostgreSQL (Supabase, Neon, CockroachDB, RDS) or custom connection strings.
    """
    try:
        import streamlit as st
        # 1. Check st.secrets["postgres"]
        if hasattr(st, "secrets") and "postgres" in st.secrets:
            return dict(st.secrets["postgres"])
        # 2. Check st.secrets["database_url"] or st.secrets["DATABASE_URL"]
        if hasattr(st, "secrets") and "database_url" in st.secrets:
            return {"url": st.secrets["database_url"]}
        if hasattr(st, "secrets") and "DATABASE_URL" in st.secrets:
            return {"url": st.secrets["DATABASE_URL"]}
    except Exception:
        pass

    # 3. Check environment variable DATABASE_URL
    env_url = os.getenv("DATABASE_URL", "")
    if env_url:
        return {"url": env_url}

    return None

def _get_connection():
    """
    Returns (connection_object, backend_type)
    backend_type is 'postgres' or 'sqlite'
    """
    cloud_cfg = _get_cloud_config()
    if cloud_cfg:
        try:
            import psycopg2
            from psycopg2.extras import RealDictCursor
            if "url" in cloud_cfg:
                conn = psycopg2.connect(cloud_cfg["url"], cursor_factory=RealDictCursor)
            else:
                conn = psycopg2.connect(
                    host=cloud_cfg.get("host", "localhost"),
                    port=cloud_cfg.get("port", 5432),
                    dbname=cloud_cfg.get("dbname", cloud_cfg.get("database", "postgres")),
                    user=cloud_cfg.get("user", cloud_cfg.get("username", "postgres")),
                    password=cloud_cfg.get("password", ""),
                    cursor_factory=RealDictCursor
                )
            return conn, 'postgres'
        except Exception as e:
            logger.warning(f"Could not connect to Cloud PostgreSQL ({e}). Falling back to local SQLite.")

    # Default: Local SQLite connection
    conn = sqlite3.connect(get_db_path())
    conn.row_factory = sqlite3.Row
    return conn, 'sqlite'

def _convert_query_params(query: str, params: Tuple, backend: str) -> Tuple[str, Tuple]:
    """Adapt placeholder parameter markers: '?' for SQLite, '%s' for PostgreSQL."""
    if backend == 'postgres':
        formatted_query = query.replace('?', '%s')
        return formatted_query, params
    return query, params

def _execute(query: str, params: Tuple = ()) -> Tuple[int, int]:
    """Execute write query (INSERT/UPDATE/DELETE). Returns (affected_rowcount, last_inserted_id)."""
    conn, backend = _get_connection()
    rowcount = 0
    lastrowid = 0
    try:
        cursor = conn.cursor()
        q_fmt, p_fmt = _convert_query_params(query, params, backend)
        cursor.execute(q_fmt, p_fmt)
        rowcount = cursor.rowcount
        if backend == 'sqlite' and hasattr(cursor, 'lastrowid'):
            lastrowid = cursor.lastrowid
        conn.commit()
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Execution error on {backend}: {e}", exc_info=True)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        raise e
    return rowcount, lastrowid

def _query_all(query: str, params: Tuple = ()) -> List[Dict[str, Any]]:
    """Execute read query and return list of dictionaries."""
    conn, backend = _get_connection()
    results = []
    try:
        cursor = conn.cursor()
        q_fmt, p_fmt = _convert_query_params(query, params, backend)
        cursor.execute(q_fmt, p_fmt)
        rows = cursor.fetchall()
        for r in rows:
            if isinstance(r, dict):
                results.append(r)
            else:
                results.append(dict(r))
        cursor.close()
        conn.close()
    except Exception as e:
        logger.error(f"Query error on {backend}: {e}", exc_info=True)
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    return results

# ─────────────────────────────────────────────────────────────────────────────
# CORE DATABASE OPERATIONS
# ─────────────────────────────────────────────────────────────────────────────

def init_db() -> None:
    """Initialize database tables, indexes, and run 7-day auto-cleanup."""
    conn, backend = _get_connection()
    try:
        cursor = conn.cursor()
        if backend == 'postgres':
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recommendations (
                    id SERIAL PRIMARY KEY,
                    symbol VARCHAR(50) NOT NULL,
                    direction VARCHAR(10) NOT NULL,
                    current_price DOUBLE PRECISION NOT NULL,
                    vwap DOUBLE PRECISION,
                    ema_9 DOUBLE PRECISION,
                    supertrend DOUBLE PRECISION,
                    volume_ratio DOUBLE PRECISION,
                    stop_loss DOUBLE PRECISION,
                    target DOUBLE PRECISION,
                    risk DOUBLE PRECISION,
                    sl_source VARCHAR(100),
                    candle_type VARCHAR(50),
                    ai_score INTEGER,
                    ai_grade VARCHAR(10),
                    ai_reasoning TEXT,
                    ai_trade_advice TEXT,
                    news_sentiment VARCHAR(50),
                    news_summary TEXT,
                    timestamp_ist VARCHAR(100) NOT NULL,
                    created_date VARCHAR(20) NOT NULL,
                    created_at VARCHAR(100) NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_rec_date ON recommendations(created_date);
                CREATE INDEX IF NOT EXISTS idx_rec_symbol ON recommendations(symbol);
            """)
        else:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recommendations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    current_price REAL NOT NULL,
                    vwap REAL,
                    ema_9 REAL,
                    supertrend REAL,
                    volume_ratio REAL,
                    stop_loss REAL,
                    target REAL,
                    risk REAL,
                    sl_source TEXT,
                    candle_type TEXT,
                    ai_score INTEGER,
                    ai_grade TEXT,
                    ai_reasoning TEXT,
                    ai_trade_advice TEXT,
                    news_sentiment TEXT,
                    news_summary TEXT,
                    timestamp_ist TEXT NOT NULL,
                    created_date TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rec_date ON recommendations(created_date);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_rec_symbol ON recommendations(symbol);")
            
        conn.commit()
        cursor.close()
        conn.close()
        logger.info(f"Database initialized successfully using backend: {backend.upper()}")
        
        # Auto-cleanup entries older than 7 days on startup
        auto_cleanup_old_recommendations(days=7)
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}", exc_info=True)

def auto_cleanup_old_recommendations(days: int = 7) -> int:
    """
    Automatically delete recommendations older than specified days (default 7 days) based on IST date.
    Returns the count of deleted records.
    """
    deleted_count = 0
    try:
        cutoff_date = (get_ist_now().date() - timedelta(days=days)).strftime("%Y-%m-%d")
        deleted_count, _ = _execute("DELETE FROM recommendations WHERE created_date < ?", (cutoff_date,))
        if deleted_count > 0:
            logger.info(f"Auto-cleanup: Purged {deleted_count} recommendations older than {days} days (before {cutoff_date}).")
    except Exception as e:
        logger.error(f"Error during auto-cleanup of recommendations: {e}", exc_info=True)
    return deleted_count

def save_recommendation(signal: dict, validity_days: int = 7) -> bool:
    """
    Save a single breakout recommendation signal to database.
    Prevents duplicate entries on the same day within close price proximity.
    Automatically purges entries older than validity_days (7 days).
    """
    auto_cleanup_old_recommendations(days=validity_days)
    
    try:
        symbol = signal.get('symbol', '').replace('.NS', '').strip().upper()
        direction = signal.get('direction', 'LONG').strip().upper()
        current_price = float(signal.get('current_price', 0.0))
        vwap = float(signal.get('vwap', 0.0))
        ema_9 = float(signal.get('ema_9', 0.0))
        supertrend = float(signal.get('supertrend', 0.0))
        volume_ratio = float(signal.get('volume_ratio', 0.0))
        stop_loss = float(signal.get('stop_loss', 0.0))
        target = float(signal.get('target_custom', signal.get('target_2_0', 0.0)))
        risk = float(signal.get('risk', 0.0))
        sl_source = str(signal.get('sl_source', 'N/A'))
        candle_type = str(signal.get('candle_type', 'N/A'))
        
        # AI analysis extraction
        ai_score_data = signal.get('ai_score') or {}
        ai_score = int(ai_score_data.get('score')) if ai_score_data.get('score') is not None else None
        ai_grade = str(ai_score_data.get('grade', '')) if ai_score_data.get('grade') else None
        ai_reasoning = str(ai_score_data.get('reasoning', '')) if ai_score_data.get('reasoning') else None
        ai_trade_advice = str(ai_score_data.get('trade_advice', '')) if ai_score_data.get('trade_advice') else None
        
        # News extraction
        news_data = signal.get('ai_news') or {}
        news_sentiment = str(news_data.get('sentiment', '')) if news_data.get('sentiment') else None
        news_summary = str(news_data.get('summary', '')) if news_data.get('summary') else None
        
        ist_now = get_ist_now()
        timestamp_ist = ist_now.strftime("%Y-%m-%d %H:%M:%S IST")
        created_date = ist_now.strftime("%Y-%m-%d")
        created_at = ist_now.isoformat()
        
        price_margin = 0.0005 * (current_price if current_price > 0 else 1.0)
        
        # Deduplication check
        existing = _query_all("""
            SELECT id FROM recommendations
            WHERE symbol = ? AND direction = ? AND created_date = ? 
            AND ABS(current_price - ?) < ?
            LIMIT 1
        """, (symbol, direction, created_date, current_price, price_margin))
        
        if existing:
            logger.debug(f"Recommendation for {symbol} ({direction}) on {created_date} already exists. Skipping duplicate.")
            return False
            
        _execute("""
            INSERT INTO recommendations (
                symbol, direction, current_price, vwap, ema_9, supertrend,
                volume_ratio, stop_loss, target, risk, sl_source, candle_type,
                ai_score, ai_grade, ai_reasoning, ai_trade_advice,
                news_sentiment, news_summary, timestamp_ist, created_date, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            symbol, direction, current_price, vwap, ema_9, supertrend,
            volume_ratio, stop_loss, target, risk, sl_source, candle_type,
            ai_score, ai_grade, ai_reasoning, ai_trade_advice,
            news_sentiment, news_summary, timestamp_ist, created_date, created_at
        ))
        
        logger.info(f"Saved recommendation for {symbol} ({direction}) to database.")
        return True
    except Exception as e:
        logger.error(f"Error saving recommendation for {signal.get('symbol')}: {e}", exc_info=True)
        return False

def save_recommendations_batch(signals: List[dict], validity_days: int = 7) -> int:
    """Save a list of recommendation signals to DB. Returns count of saved records."""
    count = 0
    for sig in signals:
        if sig and sig.get('setup_triggered'):
            if save_recommendation(sig, validity_days=validity_days):
                count += 1
    return count

def get_recommendations(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    symbol: Optional[str] = None,
    direction: Optional[str] = None,
    min_ai_score: int = 0,
    validity_days: int = 7
) -> List[Dict]:
    """
    Fetch stored recommendations from database filtered as per date and criteria.
    Automatically purges recommendations older than validity_days before fetching.
    """
    auto_cleanup_old_recommendations(days=validity_days)
    
    results = []
    try:
        query = "SELECT * FROM recommendations WHERE 1=1"
        params = []
        
        if start_date:
            query += " AND created_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND created_date <= ?"
            params.append(end_date)
        if symbol:
            query += " AND symbol LIKE ?"
            params.append(f"%{symbol.strip().upper()}%")
        if direction and direction != "ALL":
            query += " AND direction = ?"
            params.append(direction.strip().upper())
        if min_ai_score > 0:
            query += " AND (ai_score IS NULL OR ai_score >= ?)"
            params.append(min_ai_score)
            
        query += " ORDER BY id DESC"
        
        rows = _query_all(query, tuple(params))
        today_date = get_ist_now().date()
        
        for rec in rows:
            try:
                c_date = datetime.strptime(rec['created_date'], "%Y-%m-%d").date()
                days_old = (today_date - c_date).days
                rec['days_old'] = days_old
                rec['days_remaining'] = max(0, validity_days - days_old)
            except Exception:
                rec['days_old'] = 0
                rec['days_remaining'] = validity_days
            results.append(rec)
            
    except Exception as e:
        logger.error(f"Error fetching recommendations from database: {e}", exc_info=True)
    return results

def get_available_dates(validity_days: int = 7) -> List[str]:
    """Return distinct created dates present in recommendations database."""
    auto_cleanup_old_recommendations(days=validity_days)
    dates = []
    try:
        rows = _query_all("SELECT DISTINCT created_date FROM recommendations ORDER BY created_date DESC")
        dates = [r['created_date'] for r in rows if r.get('created_date')]
    except Exception as e:
        logger.error(f"Error fetching available dates: {e}", exc_info=True)
    return dates

def get_db_stats(validity_days: int = 7) -> Dict:
    """Return summary statistics of recommendations stored in DB and active database backend type."""
    auto_cleanup_old_recommendations(days=validity_days)
    _, backend = _get_connection()
    stats = {
        'total': 0,
        'long_count': 0,
        'short_count': 0,
        'avg_ai_score': 0.0,
        'distinct_dates_count': 0,
        'backend': 'Cloud PostgreSQL' if backend == 'postgres' else 'Local SQLite'
    }
    try:
        rows = _query_all("SELECT COUNT(*) as cnt, SUM(CASE WHEN direction='LONG' THEN 1 ELSE 0 END) as l_cnt, SUM(CASE WHEN direction='SHORT' THEN 1 ELSE 0 END) as s_cnt, AVG(ai_score) as avg_score FROM recommendations")
        if rows:
            r = rows[0]
            stats['total'] = int(r.get('cnt') or 0)
            stats['long_count'] = int(r.get('l_cnt') or 0)
            stats['short_count'] = int(r.get('s_cnt') or 0)
            avg_val = r.get('avg_score')
            stats['avg_ai_score'] = round(float(avg_val), 1) if avg_val is not None else 0.0
            
        d_rows = _query_all("SELECT COUNT(DISTINCT created_date) as d_cnt FROM recommendations")
        if d_rows:
            stats['distinct_dates_count'] = int(d_rows[0].get('d_cnt') or 0)
            
    except Exception as e:
        logger.error(f"Error fetching database stats: {e}", exc_info=True)
    return stats

def delete_recommendation(rec_id: int) -> bool:
    """Delete a specific recommendation by ID from the database."""
    try:
        deleted_count, _ = _execute("DELETE FROM recommendations WHERE id = ?", (rec_id,))
        return deleted_count > 0
    except Exception as e:
        logger.error(f"Error deleting recommendation ID {rec_id}: {e}", exc_info=True)
        return False

def clear_all_recommendations() -> int:
    """Delete all recommendations from the database."""
    try:
        deleted_count, _ = _execute("DELETE FROM recommendations")
        return deleted_count
    except Exception as e:
        logger.error(f"Error clearing all recommendations: {e}", exc_info=True)
        return 0
