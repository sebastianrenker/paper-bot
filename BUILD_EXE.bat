@echo off
REM Baut die echte Windows-.exe des Trading-Bots (dist\TradingBot\TradingBot.exe).
setlocal
cd /d "%~dp0"
set PYEXE=.venv\Scripts\python.exe
if not exist "%PYEXE%" (
    echo Bitte zuerst START.bat einmal ausfuehren ^(richtet die Umgebung ein^).
    pause
    exit /b 1
)
echo Installiere PyInstaller falls noetig ...
"%PYEXE%" -m pip install --quiet pyinstaller
echo Baue TradingBot.exe ^(dauert einige Minuten^) ...
"%PYEXE%" build_exe.py
echo.
echo Fertig. Die .exe liegt in: dist\TradingBot\TradingBot.exe
echo Beispiel:  dist\TradingBot\TradingBot.exe evaluate
pause
