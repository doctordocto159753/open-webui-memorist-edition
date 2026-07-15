@echo off
REM =====================================================================
REM  Memorist - double-click launcher for Windows.
REM  Invokes the PowerShell installer safely (bypasses only this process
REM  execution policy; nothing is changed system-wide).
REM =====================================================================
setlocal
set "HERE=%~dp0"

where pwsh >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PS=pwsh"
) else (
    set "PS=powershell"
)

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HERE%Install-Memorist.ps1" %*
set "RC=%ERRORLEVEL%"

echo.
echo Press any key to close this window...
pause >nul
endlocal & exit /b %RC%
