@echo off
rem Expansion 2.0: one double-click sets up the Neural Court (the local AI brain) and starts the game.
rem It updates the game from GitHub, installs the Python packages, checks Ollama, downloads the model the
rem first time (about 2 GB), then launches the terminal UI. Safe to run again at any time.
rem Run from a temporary copy: git may replace this very file while it runs, and Windows reads scripts line by line.
if not "%~1"=="--from-temp" (
  copy /y "%~f0" "%TEMP%\ct_setup_neural_court.cmd" >nul
  "%TEMP%\ct_setup_neural_court.cmd" --from-temp "%~dp0"
)
cd /d "%~2"
title Command Terminal - Neural Court setup
echo ============================================================
echo   COMMAND TERMINAL - NEURAL COURT SETUP
echo ============================================================
echo.

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

if not exist "main.py" (
  echo This file is in the wrong folder:
  echo   %CD%
  echo Move it into your game folder - the one with main.py and "Play Graphical.cmd" in it -
  echo then double-click it there.
  pause
  exit /b 1
)
git rev-parse --git-dir >nul 2>nul
if errorlevel 1 (
  echo This game folder was not downloaded with git, so it cannot update itself:
  echo   %CD%
  echo Send Claude a screenshot of this window.
  pause
  exit /b 1
)

echo [1/4] Updating the game from GitHub...
rem A copy of this file downloaded by hand would block the update: git brings its own.
git ls-files --error-unmatch "Setup Neural Court.cmd" >nul 2>nul
if errorlevel 1 del "Setup Neural Court.cmd" >nul 2>nul
git fetch origin
git checkout claude/nice-galileo-guq7zh
if errorlevel 1 (
  echo.
  echo Could not switch to the latest version of the game.
  echo Send Claude a screenshot of this window.
  pause
  exit /b 1
)
git pull
echo.

echo [2/4] Installing Python packages...
"%PY%" -m pip install -r requirements.txt
echo.

echo [3/4] Checking Ollama...
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama is not installed. Opening the download page now.
  echo Install it, then double-click this file again.
  start "" https://ollama.com/download
  pause
  exit /b 1
)
ollama list >nul 2>nul
if errorlevel 1 (
  echo Starting the Ollama service...
  start "" /min ollama serve
  timeout /t 5 /nobreak >nul
)
echo Downloading the AI brain llama3.2 - about 2 GB, only the first time...
ollama pull llama3.2
if errorlevel 1 (
  echo.
  echo The download did not finish. Check the internet connection and run this file again.
  pause
  exit /b 1
)
echo.

echo [4/4] Starting the game. Press 0 for the Royal Court, then T to talk to a courtier.
"%PY%" main.py --skip-boot
if errorlevel 1 pause
