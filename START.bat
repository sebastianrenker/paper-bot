@echo off
REM ============================================================
REM  Trading-Strategie-Dashboard - Ein-Klick-Starter (Windows)
REM  Legt beim ersten Start automatisch ein venv an, installiert
REM  alle Abhaengigkeiten und bietet danach ein Menue.
REM ============================================================
setlocal
cd /d "%~dp0"

set PYEXE=.venv\Scripts\python.exe

if not exist "%PYEXE%" (
    echo [Setup] Erstelle virtuelle Umgebung .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo.
        echo FEHLER: Python wurde nicht gefunden. Bitte Python 3.11+ installieren
        echo und beim Setup "Add python.exe to PATH" ankreuzen.
        pause
        exit /b 1
    )
    echo [Setup] Installiere Abhaengigkeiten ^(einmalig, dauert 1-2 Minuten^) ...
    "%PYEXE%" -m pip install --upgrade pip >nul
    "%PYEXE%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo FEHLER bei der Installation der Abhaengigkeiten.
        pause
        exit /b 1
    )
    echo [Setup] Fertig.
    echo.
)

:menu
echo.
echo ============================================================
echo   TRADING-STRATEGIE-DASHBOARD
echo   ACHTUNG: Analysewerkzeug, keine Finanzberatung.
echo   Handel kann zum Totalverlust fuehren. Standard: PAPER.
echo ============================================================
echo.
echo   [0] SELBSTTEST                 (pruefen ob alles bereit ist - hier anfangen!)
echo   [1] Auswertung aktualisieren   (Backtest + Walk-Forward + Monte-Carlo)
echo   [2] Dashboard oeffnen          (Browser: http://localhost:8501)
echo   [3] Dauerbetrieb starten       (Paper-Loop + Auto-Auswertung)
echo   [4] LIVE PAPER-TRADER          (Loop + Dashboard zusammen, du siehst alles live)
echo   [5] Selbst-Optimierung         (mit Overfitting-Waechter)
echo   [6] Paper-Trading (ein Durchlauf)
echo   [7] Tests ausfuehren
echo   [8] Beenden
echo.
set /p choice="Auswahl (0-8): "

if "%choice%"=="0" (
    "%PYEXE%" cli.py doctor
    pause
    goto menu
)

if "%choice%"=="1" (
    "%PYEXE%" cli.py evaluate
    pause
    goto menu
)
if "%choice%"=="2" (
    echo Starte Dashboard ... Browser oeffnet sich. Fenster schliessen = Dashboard stoppen.
    "%PYEXE%" -m streamlit run dashboard\app.py
    goto menu
)
if "%choice%"=="3" (
    echo Dauerbetrieb - Strg+C zum Beenden. Dashboard separat ueber [2] oeffnen.
    "%PYEXE%" cli.py serve
    goto menu
)
if "%choice%"=="4" (
    call PAPERTRADER.bat
    goto menu
)
if "%choice%"=="5" (
    "%PYEXE%" cli.py optimize
    pause
    goto menu
)
if "%choice%"=="6" (
    "%PYEXE%" cli.py paper --once
    pause
    goto menu
)
if "%choice%"=="7" (
    "%PYEXE%" -m pytest -q
    pause
    goto menu
)
if "%choice%"=="8" exit /b 0

echo Ungueltige Auswahl.
goto menu
