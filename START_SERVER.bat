@echo off
setlocal DisableDelayedExpansion
title Azimuth DayZ Server Manager
cd /d "%~dp0"
set "PYTHON_MANAGER_AUTOMATIC_INSTALL=false"
set "PYLAUNCHER_ALLOW_INSTALL="
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
if not exist "%~dp0Engine\launch.py" goto missing_files
where py >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "import sys,struct,tkinter; sys.exit(0 if sys.version_info >= (3,10) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 goto try_python
py -3 -c "from pathlib import Path; import subprocess,sys; app=Path(sys.executable).with_name('pythonw.exe'); subprocess.Popen([str(app),sys.argv[1]],creationflags=subprocess.DETACHED_PROCESS) if app.is_file() else subprocess.run([sys.executable,sys.argv[1]],check=True)" "%~dp0Engine\launch.py"
goto finished
:try_python
where python >nul 2>nul
if errorlevel 1 goto no_python
python -c "import sys,struct,tkinter; sys.exit(0 if sys.version_info >= (3,10) and struct.calcsize('P') == 8 else 1)" >nul 2>nul
if errorlevel 1 goto no_python
python -c "from pathlib import Path; import subprocess,sys; app=Path(sys.executable).with_name('pythonw.exe'); subprocess.Popen([str(app),sys.argv[1]],creationflags=subprocess.DETACHED_PROCESS) if app.is_file() else subprocess.run([sys.executable,sys.argv[1]],check=True)" "%~dp0Engine\launch.py"
goto finished
:missing_files
echo Extract the entire ZIP, then run START_SERVER.bat in the extracted folder.
pause
exit /b 2
:no_python
echo Azimuth needs 64-bit Python 3.10 or newer with Tcl/Tk.
echo https://www.python.org/downloads/windows/
echo In the Python installer, keep the default Tcl/Tk feature selected.
pause
exit /b 2
:finished
set "FD_EXIT=%ERRORLEVEL%"
if not "%FD_EXIT%"=="0" pause
exit /b %FD_EXIT%
