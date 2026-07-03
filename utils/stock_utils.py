import pandas as pd
import os
import requests
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)
equity_df = None

def download_nse_equity_list(file_path: str) -> bool:
    """Download the EQUITY_L.csv file from NSE archives."""
    url = "https://archives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Accept": "*/*"
    }
    try:
        logger.info(f"Downloading latest NSE equity list from {url}...")
        response = requests.get(url, headers=headers, timeout=15)
        if response.status_code == 200:
            # Ensure target directory exists
            os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
            with open(file_path, 'wb') as f:
                f.write(response.content)
            logger.info(f"Successfully downloaded and saved NSE equity list to {file_path}")
            return True
        else:
            logger.error(f"Failed to download NSE equity list. HTTP Status: {response.status_code}")
    except Exception as e:
        logger.error(f"Error downloading NSE equity list: {str(e)}")
    return False

def check_and_update_nse_file(file_path: str) -> bool:
    """
    Check if the local EQUITY_L.csv needs an update.
    Triggers download if:
    1. The file does not exist.
    2. Today is the 1st of the month and the file was not modified today.
    3. The file's modification month/year is older than the current month/year.
    """
    try:
        if not os.path.exists(file_path):
            logger.info(f"{file_path} not found. Triggering initial download.")
            return download_nse_equity_list(file_path)
            
        # Get last modification time of the file
        mtime = os.path.getmtime(file_path)
        last_modified = datetime.fromtimestamp(mtime)
        now = datetime.now()
        
        # Check if last modified month is different, or if it is the 1st of the month and last modified is not today
        needs_update = False
        if last_modified.year < now.year or last_modified.month < now.month:
            logger.info(f"Local equity file is from a previous month ({last_modified.strftime('%B %Y')}). Updating.")
            needs_update = True
        elif now.day == 1 and last_modified.date() < now.date():
            logger.info("Today is the 1st of the month. Triggering scheduled monthly update of equity list.")
            needs_update = True
            
        if needs_update:
            return download_nse_equity_list(file_path)
            
    except Exception as e:
        logger.error(f"Error checking/updating NSE equity list: {str(e)}")
    return False

def load_equity_data() -> pd.DataFrame:
    """
    Load the EQUITY_L.csv file into a pandas DataFrame with caching.
    Automatically checks and updates the file from NSE archives every month.
    """
    global equity_df
    
    if equity_df is not None and not equity_df.empty:
        return equity_df
    
    try:
        # Save CSV in the parent directory (F:\Android\Options\EQUITY_L.csv)
        file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'EQUITY_L.csv')
            
        # Perform automatic monthly update check
        check_and_update_nse_file(file_path)
        
        if not os.path.exists(file_path):
            logger.error("EQUITY_L.csv not found and could not be downloaded")
            return pd.DataFrame(columns=['SYMBOL', 'NAME OF COMPANY'])
        
        # Read the CSV with appropriate encoding
        try:
            equity_df = pd.read_csv(file_path, encoding='utf-8')
        except UnicodeDecodeError:
            equity_df = pd.read_csv(file_path, encoding='latin1')
        
        # Standardize column names (strip and uppercase)
        equity_df.columns = equity_df.columns.str.strip().str.upper()
        
        # Ensure required columns exist
        required_columns = ['SYMBOL', 'NAME OF COMPANY']
        for col in required_columns:
            if col not in equity_df.columns:
                logger.error(f"Required column '{col}' not found in EQUITY_L.csv")
                return pd.DataFrame(columns=required_columns)
        
        # Clean the data
        equity_df = equity_df.dropna(subset=['SYMBOL', 'NAME OF COMPANY'], how='all')
        equity_df['SYMBOL'] = equity_df['SYMBOL'].astype(str).str.strip()
        equity_df['NAME OF COMPANY'] = equity_df['NAME OF COMPANY'].astype(str).str.strip()
        
        # Remove duplicates, keeping the first occurrence
        equity_df = equity_df.drop_duplicates(subset=['SYMBOL'], keep='first')
        
        logger.info(f"Successfully loaded {len(equity_df)} equity records from {file_path}")
        
    except Exception as e:
        logger.error(f"Error loading EQUITY_L.csv: {str(e)}")
        equity_df = pd.DataFrame(columns=['SYMBOL', 'NAME OF COMPANY'])
    
    return equity_df
