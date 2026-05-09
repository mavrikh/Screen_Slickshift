@echo off
setlocal

set HOST=0.0.0.0
set PORT=8765

:: Allow overrides: run.bat [host] [port]
if not "%~1"=="" set HOST=%~1
if not "%~2"=="" set PORT=%~2

:: Create virtual environment if it doesn't exist
if not exist ".venv\" (
    echo Creating Python virtual environment...
    where py >nul 2>&1 && (
        py -3 -m venv .venv
    ) || (
        python -m venv .venv
    )
    if errorlevel 1 (
        echo ERROR: Could not create virtual environment.
        echo Make sure Python 3 is installed and on your PATH.
        pause
        exit /b 1
    )
)

echo Activating virtual environment...
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Could not activate virtual environment.
    pause
    exit /b 1
)

echo Installing/updating dependencies...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERROR: Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Starting Screen Slickshift on http://%HOST%:%PORT%
echo  Press Ctrl+C to stop the server.
echo ============================================================
echo.

python -m uvicorn app.main:app --host %HOST% --port %PORT% --no-access-log

pause
