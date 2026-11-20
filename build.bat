@echo off
cd /d "%~dp0"
pip install pyinstaller==5.13.2
pyinstaller --onefile --windowed --name BlazeLauncher launcher.py
echo Done: dist\BlazeLauncher.exe
pause
