"""Liest trading.db und schreibt BOT_STATUS_REPORT.md - ehrlicher Faktenreport,
keine Prognose. Wird stuendlich per Windows Scheduled Task aufgerufen und einmal
sofort nach dem Bot-Start.

Aendert nichts am Bot; reine Lese-/Berichtsfunktion.
"""
from __future__ import annotations

import sqlite3
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# TRADING_DB (z. B. cloud/paper.db in der CI/Streamlit-Cloud) hat Vorrang.
_env_db = os.environ.get("TRADING_DB")
if _env_db:
    DB = Path(_env_db) if Path(_env_db).is_absolute() else ROOT / _env_db
elif (ROOT / "cloud" / "paper.db").exists() and not (ROOT / "trading.db").exists():
    DB = ROOT / "cloud" / "paper.db"
else:
    DB = ROOT / "trading.db"
OUT = ROOT / "BOT_STATUS_REPORT.md"


def _start_capital() -> float:
    """Startkapital aus config.yaml (Fallback 500)."""
    try:
        import yaml
        raw = yaml.safe_load((ROOT / "config" / "config.yaml").read_text(encoding="utf-8")) or {}
        return float(raw.get("capital", {}).get("initial", 500.0))
    except Exception:  # noqa: BLE001
        return 500.0


START_CAPITAL = _start_capital()


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds/60:.1f} min"
    return f"{seconds/3600:.1f} h"


def _iso_age(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    try:
        return (now - datetime.fromisoformat(ts)).total_seconds()
    except Exception:  # noqa: BLE001
        return None


def build_report() -> str:
    now = datetime.now(timezone.utc)
    lines: list[str] = []
    add = lines.append

    if not DB.exists():
        return f"# Bot-Status\n\nKeine Datenbank `{DB.name}` gefunden. Bot wurde nie gestartet.\n"

    conn = sqlite3.connect(DB, timeout=30.0)
    conn.row_factory = sqlite3.Row

    def q1(sql, params=()):
        r = conn.execute(sql, params).fetchone()
        return r

    def qall(sql, params=()):
        return conn.execute(sql, params).fetchall()

    # --- Heartbeat / Laufzeit ------------------------------------------------
    hb = q1("SELECT * FROM heartbeat WHERE source='paper'")
    hb_age = _iso_age(hb["ts"], now) if hb else None
    first_eq = q1("SELECT MIN(ts) AS ts FROM equity_points WHERE source='paper'")
    start_age = _iso_age(first_eq["ts"] if first_eq else None, now)
    alive = hb_age is not None and hb_age < 120

    add("# Bot-Status-Report (Paper-Trading, echte Marktdaten)\n")
    add(f"*Erstellt: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC — reine Fakten, keine Prognose.*\n")
    add("## Betrieb\n")
    add(f"- **Status:** {'LÄUFT (LIVE)' if alive else 'gestoppt / kein aktuelles Lebenszeichen'}")
    add(f"- **Letztes Lebenszeichen:** vor {_fmt_age(hb_age)}")
    add(f"- **Läuft seit:** {first_eq['ts'] if first_eq and first_eq['ts'] else '— (noch kein Tick)'}"
        f" ({_fmt_age(start_age)})")

    # --- Kapital -------------------------------------------------------------
    equity = hb["equity"] if hb and hb["equity"] is not None else START_CAPITAL
    pnl = equity - START_CAPITAL
    pct = (equity / START_CAPITAL - 1) * 100 if START_CAPITAL else 0.0
    add("\n## Kapital\n")
    add(f"- **Start:** {START_CAPITAL:.2f} € (Paper, simuliert)")
    add(f"- **Aktuell:** {equity:.2f} €")
    add(f"- **Veränderung:** {pnl:+.2f} € ({pct:+.3f} %)")
    add(f"- **Offene Positionen:** {hb['open_positions'] if hb else 0}")
    add(f"- **Tagesverlust (heute):** {(hb['daily_pnl_pct'] or 0)*100:+.2f} %" if hb else
        "- **Tagesverlust (heute):** —")

    # --- Offene Positionen ---------------------------------------------------
    add("\n## Offene Positionen\n")
    pos = qall("SELECT * FROM positions_snapshot WHERE source='paper' ORDER BY symbol")
    if not pos:
        add("Keine offenen Positionen.")
    else:
        add("| Symbol | Strategie | Richtung | Einstieg | Kurs | Unrealisiert |")
        add("|---|---|---|---|---|---|")
        for p in pos:
            direction = "LONG" if (p["qty"] or 0) > 0 else "SHORT"
            add(f"| {p['symbol']} | {p['strategy'] or '—'} | {direction} | "
                f"{p['avg_price']:.4f} | {p['mark_price']:.4f} | {p['unrealized']:+.2f} |")

    # --- Abgeschlossene Paper-Trades ----------------------------------------
    add("\n## Abgeschlossene Paper-Trades\n")
    tot = q1("SELECT COUNT(*) AS n, COALESCE(SUM(pnl),0) AS pnl FROM trades WHERE source='paper'")
    n_trades = tot["n"] if tot else 0
    add(f"- **Anzahl:** {n_trades}")
    add(f"- **Summe PnL:** {(tot['pnl'] if tot else 0):+.2f} €")
    if n_trades:
        add("\n| Strategie | Trades | Summe PnL | Ø PnL |")
        add("|---|---|---|---|")
        for r in qall("SELECT strategy, COUNT(*) n, SUM(pnl) pnl, AVG(pnl) avg "
                      "FROM trades WHERE source='paper' GROUP BY strategy ORDER BY pnl DESC"):
            add(f"| {r['strategy']} | {r['n']} | {r['pnl']:+.2f} | {r['avg']:+.3f} |")
    else:
        add("\n*Noch keine abgeschlossenen Trades. Auf 4h-Timeframes kann das Stunden dauern.*")

    # --- Circuit Breaker -----------------------------------------------------
    add("\n## Circuit Breaker / Risiko-Ereignisse\n")
    cb = qall("SELECT ts, event, details FROM audit_log "
              "WHERE event IN ('circuit_breaker','kill_switch','forced_flat') ORDER BY id DESC LIMIT 10")
    if not cb:
        add("Nie ausgelöst. Handel lief innerhalb der Risikolimits.")
    else:
        add(f"**{len(cb)} Ereignis(se)** (neueste zuerst):\n")
        add("| Zeit | Ereignis | Details |")
        add("|---|---|---|")
        for r in cb:
            add(f"| {r['ts']} | {r['event']} | {(r['details'] or '')[:80]} |")

    # --- Letzte Selbst-Anpassung --------------------------------------------
    add("\n## Selbst-Anpassung\n")
    la = q1("SELECT value FROM control WHERE key='last_adaptation'")
    ac = q1("SELECT value FROM control WHERE key='adapted_combos'")
    add(f"- **Letzte Anpassung:** {la['value'] if la and la['value'] else 'noch keine'}")
    add(f"- **Aktiv gehandelte (validierte) Kombinationen:** {ac['value'] if ac and ac['value'] else '0'}")

    # --- Datenquelle-Kontrolle ----------------------------------------------
    add("\n## Datenintegrität\n")
    src = qall("SELECT data_source, COUNT(*) n FROM evaluations GROUP BY data_source")
    parts = ", ".join(f"{r['data_source']}: {r['n']}" for r in src)
    synth = sum(r["n"] for r in src if r["data_source"] == "synthetic")
    add(f"- Datenquellen der Auswertungen: {parts}")
    add(f"- **Synthetische Daten:** {synth} "
        f"{'(OK — 0 bei require_real:true)' if synth == 0 else '⚠️ FEHLER: sollte 0 sein!'}")

    conn.close()
    add("\n---\n*Analysewerkzeug, keine Finanzberatung. Paper-Trading mit simuliertem Geld. "
        "Ergebnisse über wenige Stunden sind Marktrauschen, kein Beleg für einen Vorteil.*\n")
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.write_text(build_report(), encoding="utf-8")
    print(f"Report geschrieben: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
