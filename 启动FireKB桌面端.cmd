@echo off
setlocal
cd /d "%~dp0firekb-desktop"
set "FIREKB_ROOT=%~dp0"
set "PYTHON_PATH=%~dp0.venv\Scripts\python.exe"
if not exist "dist\main\main.js" (
  echo Building FireKB Desktop...
  call npm run build:app
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
call npm start
endlocal
