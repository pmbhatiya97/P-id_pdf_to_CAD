@echo off
setlocal
cd /d "%~dp0"
if exist "LineaForgeWeb.exe" (
  "LineaForgeWeb.exe" --open %*
) else (
  if exist "dist\web-current.txt" (
    set /p LINEAFORGE_LAUNCH_DIR=<"dist\web-current.txt"
    call :launch_current %*
  ) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_web.ps1" %*
  )
)
if errorlevel 1 pause
exit /b %errorlevel%
:launch_current
if exist "%LINEAFORGE_LAUNCH_DIR%\LineaForgeWeb.exe" (
  "%LINEAFORGE_LAUNCH_DIR%\LineaForgeWeb.exe" --open %*
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_web.ps1" %*
)
exit /b %errorlevel%
