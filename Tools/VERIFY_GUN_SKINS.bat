@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0.." || exit /b 1
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
if not exist "Engine\launch.py" (
    echo Azimuth is incomplete. Extract the full ZIP and keep Tools inside Azimuth.
    pause
    exit /b 1
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 "Engine\launch.py" check-skins
    goto finished
)
python "Engine\launch.py" check-skins
:finished
set "AZIMUTH_CHECK_EXIT=%ERRORLEVEL%"
echo.
pause
exit /b %AZIMUTH_CHECK_EXIT%
