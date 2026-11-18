@echo off
rem Stops a running Streamdeck companion (also the hidden one), e.g. before flashing the board.
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*streamdeck_companion*' -and $_.Name -match 'python' }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Write-Host 'Streamdeck companion stopped.' } else { Write-Host 'The Streamdeck companion is not running.' }"
ping -n 3 127.0.0.1 >nul
