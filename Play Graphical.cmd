@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" play_graphical.py %*
) else (
  python play_graphical.py %*
)
if errorlevel 1 pause
