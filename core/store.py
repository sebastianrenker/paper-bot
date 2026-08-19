"""SQLite-Persistenz fuer Evaluationen, Trades und das Audit-Log."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at TEXT NOT NULL,
    strategy TEXT NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    data_source TEXT NOT NULL,
    score REAL,
    traffic_light TEXT,
    n_trades INTEGER,
    win_rate REAL,
    profit_factor REAL,
    expectancy_r REAL,
    sharpe REAL,
    sortino REAL,
    max_drawdown REAL,
    mc_prob_profitable REAL,
    mc_return_ci_low REAL,
    mc_return_ci_high REAL,
    mc_drawdown_ci_high REAL,
    wf_efficiency REAL,
    regime TEXT,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_eval_lookup ON evaluations(strategy, market, symbol, timeframe, run_at);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,            -- backtest | paper | live
    strategy TEXT NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_time TEXT, entry_price REAL,
    exit_time TEXT, exit_price REAL,
    qty REAL, pnl REAL, r_multiple REAL,
    exit_reason TEXT, reason TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    mode TEXT,
    strategy TEXT,
    symbol TEXT,
    details TEXT
);

CREATE TABLE IF NOT EXISTS equity_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    equity REAL NOT NULL
);

-- Momentaufnahme der offenen Positionen (wird je Tick ersetzt), damit das
-- Dashboard als eigener Prozess live sieht, was der Paper-Loop gerade haelt.
CREATE TABLE IF NOT EXISTS positions_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    source TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL DEFAULT '',
    qty REAL NOT NULL,
    avg_price REAL NOT NULL,
    stop_loss REAL,
    take_profit REAL,
    mark_price REAL,
    unrealized REAL,
    entry_time TEXT
);

CREATE TABLE IF NOT EXISTS heartbeat (
    source TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    equity REAL,
    open_positions INTEGER,
    daily_pnl_pct REAL,
    tripped INTEGER,
    note TEXT
);

-- Steuerkanal: das Dashboard schreibt Befehle/Wunschzustand, der Bot-Prozess liest sie.
CREATE TABLE IF NOT EXISTS control (
    key TEXT PRIMARY KEY,
    value TEXT,
    ts TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | str = "trading.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        # WAL erlaubt gleichzeitiges Lesen/Schreiben aus mehreren Verbindungen
        # (Bot-Prozess + Dashboard + Anpassungs-Thread) ohne "database is locked".
        # busy_timeout laesst Schreibvorgaenge warten statt sofort zu scheitern.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        """Leichte Schema-Migration fuer Datenbanken, die vor neueren Feldern in
        positions_snapshot angelegt wurden. `CREATE TABLE IF NOT EXISTS` legt bei bereits
        vorhandener Tabelle keine neuen Spalten an - das holt diese Methode nach."""
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(positions_snapshot)")}
        if "strategy" not in cols:
            # Bugfix Positions-Kollision: Positionen werden jetzt pro Strategie statt nur
            # pro Symbol getrackt, siehe execution/paper.py.
            self.conn.execute("ALTER TABLE positions_snapshot ADD COLUMN strategy TEXT NOT NULL DEFAULT ''")
            self.conn.commit()
        if "entry_time" not in cols:
            # Bugfix Neustart-Datenverlust: ohne entry_time laesst sich eine offene Position
            # beim Wiederherstellen nach einem Neustart (siehe cli.py::restore_broker_state)
            # nur mit einem geschaetzten Einstiegszeitpunkt rekonstruieren statt dem echten.
            self.conn.execute("ALTER TABLE positions_snapshot ADD COLUMN entry_time TEXT")
            self.conn.commit()

    # ---- Schreiben --------------------------------------------------------
    def save_evaluation(self, row: dict[str, Any]) -> int:
        row = dict(row)
        row.setdefault("run_at", datetime.now(timezone.utc).isoformat())
        if isinstance(row.get("payload"), (dict, list)):
            row["payload"] = json.dumps(row["payload"], default=str)
        cols = ", ".join(row)
        placeholders = ", ".join(f":{k}" for k in row)
        cur = self.conn.execute(f"INSERT INTO evaluations ({cols}) VALUES ({placeholders})", row)
        self.conn.commit()
        return int(cur.lastrowid)

    def save_trades(self, trades: Iterable[dict], source: str) -> None:
        rows = [{**t, "source": source} for t in trades]
        if not rows:
            return
        keys = ["source", "strategy", "market", "symbol", "timeframe", "direction",
                "entry_time", "entry_price", "exit_time", "exit_price", "qty",
                "pnl", "r_multiple", "exit_reason", "reason"]
        self.conn.executemany(
            f"INSERT INTO trades ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
            [tuple(_iso(r.get(k)) for k in keys) for r in rows],
        )
        self.conn.commit()

    def log(self, event: str, *, mode: str = "", strategy: str = "",
            symbol: str = "", **details) -> None:
        self.conn.execute(
            "INSERT INTO audit_log (ts, event, mode, strategy, symbol, details) VALUES (?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), event, mode, strategy, symbol,
             json.dumps(details, default=str)),
        )
        self.conn.commit()

    def save_equity_point(self, equity: float, source: str) -> None:
        self.conn.execute(
            "INSERT INTO equity_points (ts, source, equity) VALUES (?,?,?)",
            (datetime.now(timezone.utc).isoformat(), source, float(equity)),
        )
        self.conn.commit()

    def save_positions_snapshot(self, positions: list[dict], source: str) -> None:
        """Ersetzt die Momentaufnahme der offenen Positionen fuer diese Quelle.

        `entry_time` wird mitgespeichert (nicht nur fuers Dashboard, sondern damit ein
        neu gestarteter Bot-Prozess offene Positionen mit ihrem ECHTEN Einstiegszeitpunkt
        wiederherstellen kann, siehe cli.py::restore_broker_state)."""
        ts = datetime.now(timezone.utc).isoformat()
        self.conn.execute("DELETE FROM positions_snapshot WHERE source=?", (source,))
        if positions:
            self.conn.executemany(
                "INSERT INTO positions_snapshot "
                "(ts, source, symbol, strategy, qty, avg_price, stop_loss, take_profit, mark_price, "
                "unrealized, entry_time) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [(ts, source, p["symbol"], p.get("strategy", ""), p["qty"], p["avg_price"], p.get("stop_loss"),
                  p.get("take_profit"), p.get("mark_price"), p.get("unrealized"), _iso(p.get("entry_time")))
                 for p in positions],
            )
        self.conn.commit()

    def save_heartbeat(self, source: str, **fields) -> None:
        """Letztes Lebenszeichen des Loops - das Dashboard zeigt 'live' vs. 'gestoppt'."""
        self.conn.execute(
            "INSERT INTO heartbeat (source, ts, equity, open_positions, daily_pnl_pct, tripped, note) "
            "VALUES (?,?,?,?,?,?,?) ON CONFLICT(source) DO UPDATE SET "
            "ts=excluded.ts, equity=excluded.equity, open_positions=excluded.open_positions, "
            "daily_pnl_pct=excluded.daily_pnl_pct, tripped=excluded.tripped, note=excluded.note",
            (source, datetime.now(timezone.utc).isoformat(), fields.get("equity"),
             fields.get("open_positions"), fields.get("daily_pnl_pct"),
             int(fields.get("tripped", 0)), fields.get("note", "")),
        )
        self.conn.commit()

    def positions_snapshot(self, source: str = "paper") -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM positions_snapshot WHERE source=? ORDER BY symbol",
            self.conn, params=(source,),
        )

    def heartbeat(self, source: str = "paper") -> dict:
        cur = self.conn.execute("SELECT * FROM heartbeat WHERE source=?", (source,))
        row = cur.fetchone()
        return dict(row) if row else {}

    # ---- Steuerkanal Dashboard <-> Bot -----------------------------------
    def set_control(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO control (key, value, ts) VALUES (?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
            (key, str(value), datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def get_control(self, key: str, default: str = "") -> str:
        cur = self.conn.execute("SELECT value FROM control WHERE key=?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default

    def pop_command(self) -> str:
        """Liest den anstehenden Befehl und loescht ihn (einmalige Ausfuehrung)."""
        cmd = self.get_control("command", "")
        if cmd:
            self.set_control("command", "")
        return cmd

    # ---- Lesen ------------------------------------------------------------
    def latest_evaluations(self) -> pd.DataFrame:
        """Je Strategie/Markt/Symbol/Timeframe die jeweils juengste Auswertung."""
        return pd.read_sql_query(
            """
            SELECT e.* FROM evaluations e
            JOIN (
                SELECT strategy, market, symbol, timeframe, MAX(run_at) AS run_at
                FROM evaluations GROUP BY strategy, market, symbol, timeframe
            ) m USING (strategy, market, symbol, timeframe, run_at)
            ORDER BY e.score DESC
            """,
            self.conn,
        )

    def evaluation_history(self, strategy: str, symbol: str, timeframe: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM evaluations WHERE strategy=? AND symbol=? AND timeframe=? ORDER BY run_at",
            self.conn, params=(strategy, symbol, timeframe),
        )

    def trades(self, source: str | None = None, strategy: str | None = None) -> pd.DataFrame:
        sql, params = "SELECT * FROM trades WHERE 1=1", []
        if source:
            sql += " AND source=?"
            params.append(source)
        if strategy:
            sql += " AND strategy=?"
            params.append(strategy)
        return pd.read_sql_query(sql + " ORDER BY entry_time DESC", self.conn, params=params)

    def audit(self, limit: int = 200) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", self.conn, params=(limit,)
        )

    def equity_curve(self, source: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT ts, equity FROM equity_points WHERE source=? ORDER BY ts",
            self.conn, params=(source,),
        )

    def close(self) -> None:
        self.conn.close()


def _iso(value):
    return value.isoformat() if isinstance(value, (datetime, pd.Timestamp)) else value
