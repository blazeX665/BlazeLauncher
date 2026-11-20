@echo off
cd /d "%~dp0"
where pyw >nul 2>nul
if %errorlevel%==0 (start "" pyw launcher.py & exit /b)
where pythonw >nul 2>nul
if %errorlevel%==0 (start "" pythonw launcher.py & exit /b)
echo Python 3.8+ not found. Install it from https://www.python.org/downloads/ (tick "Add to PATH"),
echo or download the ready BlazeLauncher.exe from the Releases page.
pause
