@echo off
REM ============================================================
REM  Supervisor fuer den Paper-Bot: startet `cli.py serve` und
REM  startet ihn nach einem ABSTURZ mit Cooldown neu.
REM
REM  Wichtig: mit dem Prio-2-Fix beendet sich `serve` NICHT mehr,
REM  wenn der Circuit Breaker ausloest (er pausiert nur den Handel).
REM  Dieser Supervisor greift daher nur bei echten Abstuerzen/Exits.
REM  Der Doppelstart-Schutz in `serve` verhindert zwei parallele Bots.
REM
REM  Kein echtes Geld - reines Paper-Trading.
REM ============================================================
setlocal
cd /d "%~dp0"
set PYEXE=.venv\Scripts\python.exe

if not exist "%PYEXE%" (
    echo [supervisor] Umgebung fehlt - bitte zuerst START.bat einmal ausfuehren.
    pause
    exit /b 1
)

:loop
echo [supervisor] %DATE% %TIME% - starte Bot ... >> bot_supervisor.log
"%PYEXE%" cli.py serve --interval 60 --reeval-hours 6
echo [supervisor] %DATE% %TIME% - Bot beendet (Exit %ERRORLEVEL%). Neustart in 30s. >> bot_supervisor.log
timeout /t 30 /nobreak >nul
goto loop
