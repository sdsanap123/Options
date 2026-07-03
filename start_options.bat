@echo off
title Options Intraday Breakout Scanner
echo Starting Options Intraday Breakout Scanner (Streamlit)...
echo.

REM Check if Python is installed
py --version >nul 2>&1
if errorlevel 1 (
    echo Error: Python is not installed or not in PATH
    echo Please install Python 3.8 or higher from https://python.org
    pause
    exit /b 1
)

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    py -m venv venv
    if errorlevel 1 (
        echo Error: Failed to create virtual environment
        pause
        exit /b 1
    )
)

REM Activate virtual environment
echo Activating virtual environment...
call venv\Scripts\activate.bat

REM Install dependencies
echo Installing/Verifying dependencies...
echo Upgrading pip first...
python.exe -m pip install --upgrade pip
echo Installing setuptools...
pip install setuptools>=65.0.0
echo Installing requirements...
pip install -r requirements.txt
if errorlevel 1 (
    echo Error: Failed to install dependencies
    pause
    exit /b 1
)

REM Start the Streamlit application
echo.
echo Starting the Streamlit application...
echo The app will open automatically in your browser.
echo Press Ctrl+C to stop the server
echo.
streamlit run streamlit_app.py

pause
