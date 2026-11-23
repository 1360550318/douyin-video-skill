@echo off
REM ==========================================================
REM  douyin-video-skill one-click installer (Windows)
REM  Kept ASCII-only on purpose: cmd parses the batch before chcp.
REM
REM  Usage:
REM    install.cmd              install deps + self-check
REM    install.cmd --link       also install into the skills directory
REM    install.cmd --check      self-check only
REM ==========================================================
setlocal
cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py
"%PY%" -c "import sys" >nul 2>nul || (
  echo ERROR: python not found in PATH
  pause
  exit /b 1
)

"%PY%" install.py %*
echo.
pause
