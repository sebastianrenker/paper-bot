@echo off
REM ============================================================
REM  Live Paper-Trader: startet den Paper-Loop UND das Dashboard
REM  zusammen. Ein Klick -> du siehst live, was der Bot macht.
REM  Kein echtes Geld (Paper-Modus).
REM ============================================================
setlocal
cd /d "%~dp0"
set PYEXE=.venv\Scripts\python.exe

if not exist "%PYEXE%" (
    echo Bitte zuerst START.bat einmal ausfuehren ^(richtet die Umgebung ein^).
    pause
    exit /b 1
)

REM Falls noch keine Auswertung existiert, einmal rechnen (fuer die anderen Tabs)
if not exist "trading.db" (
    echo [Setup] Erste Auswertung wird gerechnet ^(einmalig^) ...
    "%PYEXE%" cli.py evaluate
)

echo Starte Paper-Loop in eigenem Fenster ...
start "Paper-Loop" "%PYEXE%" cli.py serve --interval 300 --reeval-hours 6

echo Starte Dashboard ... Browser: http://localhost:8501
echo Oeffne den Tab "Live Paper-Trader" - er frischt sich alle 5s selbst auf.
"%PYEXE%" -m streamlit run dashboard\app.py

echo.
echo Dashboard beendet. Das Paper-Loop-Fenster ggf. separat schliessen.
pause
