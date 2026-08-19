"""Baut die echte Windows-.exe des Trading-Bots mit PyInstaller.

Ergebnis: dist/TradingBot/TradingBot.exe  (onedir - robuster als onefile bei
grossen wissenschaftlichen Paketen wie pandas/numpy/ccxt).

Die .exe ist der HEADLESS-Bot: evaluate, optimize, stress, paper, serve, vault,
enable-live. Das visuelle Dashboard (Streamlit) laeuft weiter ueber START.bat -
Streamlit laesst sich nicht zuverlaessig in eine einzelne .exe einfrieren, das ist
eine bekannte Einschraenkung und kein Fehler dieses Projekts.

Aufruf:  .venv\Scripts\python.exe build_exe.py
"""
from __future__ import annotations

import PyInstaller.__main__

PyInstaller.__main__.run([
    "cli.py",
    "--name", "TradingBot",
    "--onedir",
    "--noconfirm",
    "--clean",
    "--console",
    # dynamisch/verzoegert importierte Pakete vollstaendig einsammeln
    "--collect-all", "ccxt",
    "--collect-all", "yfinance",
    "--collect-submodules", "cryptography",
    "--collect-submodules", "pandas",
    "--collect-submodules", "numpy",
    # Projektmodule, die per String importiert werden koennten, sicher mitnehmen
    "--collect-submodules", "strategies",
    # Konfiguration mit ins Bundle legen
    "--add-data", "config/config.yaml;config",
])
