@echo off
rem Starts the PC side of the Streamdeck. Double-click it, or run it from a terminal.
rem First run: creates a Python environment in companion\.venv and installs the requirements.
rem Extra arguments go to the companion, e.g.:  Streamdeck.bat --dry-run   (log actions, do not run them)
rem   --deck FILE   --port COM3   --no-media   --no-stats   --check   -v
setlocal
cd /d "%~dp0companion"

if not exist ".venv\Scripts\python.exe" (
  echo First run: creating the Python environment...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    python -m venv .venv
  )
  if errorlevel 1 (
    echo.
    echo Python 3 was not found. Install it from https://www.python.org/downloads/ and run this again.
    pause
    exit /b 1
  )
)

rem Install the requirements on the first run and whenever requirements.txt changes.
fc /b requirements.txt .venv\requirements.installed >nul 2>nul
if errorlevel 1 (
  echo Installing requirements, this can take a minute...
  ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo.
    echo Installing the requirements failed. Check your internet connection and run this again.
    pause
    exit /b 1
  )
  copy /y requirements.txt .venv\requirements.installed >nul
)

echo Streamdeck companion running. Close this window or press Ctrl+C to stop it.
echo (Stop it before flashing the board: it holds the USB port.)
echo.
".venv\Scripts\python.exe" -m streamdeck_companion %*
if errorlevel 1 (
  echo.
  echo The companion stopped with an error.
  pause
)
