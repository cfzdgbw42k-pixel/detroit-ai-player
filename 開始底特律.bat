@echo off
cd /d "%~dp0"

echo Starting Detroit Blind Host...
echo.

where py >nul 2>nul
if not errorlevel 1 (
    py -3 web_host\server.py
    goto end
)

where python >nul 2>nul
if not errorlevel 1 (
    python web_host\server.py
    goto end
)

echo.
echo Python was not found.
echo Please install Python 3.10 or newer.
echo.

:end
if errorlevel 1 (
    echo.
    echo The host could not start.
)

echo.
pause
