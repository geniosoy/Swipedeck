@echo off
rem Opens the deck editor in your browser. If the companion is not running yet, it is started in the background
rem first (the editor is served by the companion). Add --no-browser to only make sure the editor is up.
setlocal
set "URL=http://127.0.0.1:8765/"

call :up
if errorlevel 1 (
  echo Starting the Streamdeck companion in the background...
  wscript "%~dp0Streamdeck-background.vbs"
  for /l %%i in (1,1,40) do (
    call :up
    if not errorlevel 1 goto ready
    ping -n 2 127.0.0.1 >nul
  )
  echo.
  echo The editor did not start. Look at companion\companion.log, or run Streamdeck.bat to see the error.
  pause
  exit /b 1
)

:ready
if /i "%~1"=="--no-browser" (
  echo The editor is running at %URL%
  exit /b 0
)
start "" "%URL%"
exit /b 0

:up
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing -Uri '%URL%api/status' -TimeoutSec 2 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>nul
exit /b %errorlevel%
