"""Streamlit-Dashboard.

Start:  python cli.py dashboard      (oder: streamlit run dashboard/app.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from config.settings import DEFAULT_CONFIG_PATH, load_settings  # noqa: E402
from core.store import Store  # noqa: E402
from risk.manager import Mode  # noqa: E402
from stats.metrics import MIN_TRADES_FOR_SIGNIFICANCE, drawdown_series  # noqa: E402

st.set_page_config(page_title="Trading-Strategie-Dashboard", layout="wide", page_icon="")

LIGHT_COLORS = {"green": "#16a34a", "yellow": "#ca8a04", "red": "#dc2626"}


@st.cache_resource
def get_store(path: str) -> Store:
    return Store(path)


def mode_banner(mode: Mode, tripped: bool, trip_reason: str) -> None:
    if mode == Mode.LIVE:
        color, text = "#dc2626", "LIVE MODE - ECHTES GELD IM EINSATZ"
    elif mode == Mode.PAPER:
        color, text = "#16a34a", "PAPER MODE - kein echtes Geld"
    else:
        color, text = "#2563eb", "BACKTEST MODE - historische Auswertung"
    st.markdown(
        f"<div style='background:{color};color:#fff;padding:14px 18px;border-radius:8px;"
        f"font-size:22px;font-weight:700;text-align:center'>{text}</div>",
        unsafe_allow_html=True,
    )
    if tripped:
        st.error(f"CIRCUIT BREAKER AKTIV - Handel gestoppt. Grund: {trip_reason}")


def main() -> None:
    settings = load_settings(DEFAULT_CONFIG_PATH)
    store = get_store(str(settings.db_path))

    st.title("📊 Trading-Bot — Live-Übersicht")
    st.caption(
        "Analysewerkzeug mit **Spielgeld**, keine Finanzberatung. Kein echtes Geld im Spiel. "
        "Vergangene Ergebnisse sind keine Garantie für die Zukunft."
    )

    if "killed" not in st.session_state:
        st.session_state.killed = False

    mode = Mode.PAPER if st.session_state.killed else settings.effective_mode
    mode_banner(mode, st.session_state.killed, "Kill-Switch im UI ausgeloest")

    import os as _os
    _readonly = bool(_os.environ.get("CLOUD_READONLY"))
    with st.expander("👋 Was ist das hier? (in 20 Sekunden erklärt)", expanded=_readonly):
        st.markdown(
            "Dieser Bot testet **Handels-Strategien mit Spielgeld** auf echten Börsendaten — "
            "damit man sieht, was funktioniert, **ohne echtes Geld zu riskieren**.\n\n"
            "**So liest du die Seite — die 3 wichtigsten Tabs oben:**\n"
            "1. **Live Paper-Trader** — was der Bot *gerade jetzt* macht: Kapital, offene "
            "Positionen, ein Kurs-Chart mit den Kauf-/Verkauf-Punkten.\n"
            "2. **Ranking** — welche Strategie auf welchem Coin am besten abschneidet. "
            "🟢 grün = vielversprechend, 🟡 gelb = mittel, 🔴 rot = eher nicht.\n"
            "3. **Portfolio** — die besten, sich ergänzenden Strategien zusammen (streut das Risiko).\n\n"
            "**Wichtig & ehrlich:** Kein Programm garantiert Gewinn. Nur Zeilen mit **vielen "
            "Trades** sind aussagekräftig; wenige Trades = Zufall. Bei „**Robust bei doppelten "
            "Kosten? ✅**\" hält der Vorteil auch mit höheren Gebühren."
        )

    # ---- Sidebar: Status und Kill-Switch ---------------------------------
    with st.sidebar:
        st.header("Status")
        st.write(f"**Konfiguriert:** `{settings.configured_mode.value}`")
        st.write(f"**Effektiv:** `{mode.value}`")
        limits = settings.risk_limits()
        st.write(f"**Risiko/Trade:** {limits.risk_per_trade:.1%}")
        st.write(f"**Max. Tagesverlust:** {limits.max_daily_loss:.1%}")
        st.write(f"**Max. offene Positionen:** {limits.max_open_positions}")

        paper_eq = store.equity_curve("paper")
        equity = float(paper_eq["equity"].iloc[-1]) if len(paper_eq) else settings.initial_capital
        start = settings.initial_capital
        st.metric("Kapital (Paper)", f"{equity:,.2f}", f"{(equity/start-1)*100:+.2f} %")

        daily_loss_used = max(0.0, -(equity / start - 1)) / limits.max_daily_loss
        st.progress(min(daily_loss_used, 1.0), text=f"Tagesverlust-Limit {min(daily_loss_used,1.0):.0%} ausgeschoepft")

        st.divider()
        if st.button("KILL-SWITCH: sofort auf Paper", type="primary", width="stretch"):
            st.session_state.killed = True
            store.log("kill_switch", mode=mode.value, source="dashboard")
            st.rerun()
        if st.session_state.killed and st.button("Kill-Switch zuruecksetzen", width="stretch"):
            st.session_state.killed = False
            st.rerun()

    df = store.latest_evaluations()
    has_evals = not df.empty
    if has_evals and (df["data_source"] == "synthetic").any():
        st.warning(
            "Teile der Auswertung beruhen auf SYNTHETISCHEN Daten (keine Datenquelle "
            "verfuegbar). Diese Zeilen haben keinerlei Aussagekraft fuer echte Maerkte."
        )

    tab_live, tab_rank, tab_detail, tab_pf, tab_heat, tab_audit = st.tabs(
        ["Live Paper-Trader", "Ranking", "Detail je Strategie", "Portfolio",
         "Markt-Heatmap", "Audit-Log"]
    )

    with tab_live:
        render_live_paper(store, settings)
    if not has_evals:
        for t in (tab_rank, tab_detail, tab_pf, tab_heat):
            with t:
                st.warning("Noch keine Auswertungen. Zuerst `python cli.py evaluate` ausfuehren.")
        with tab_audit:
            st.dataframe(store.audit(300), width="stretch", hide_index=True)
        return

    with tab_rank:
        render_ranking(df)
    with tab_detail:
        render_detail(store, df)
    with tab_pf:
        render_portfolio(settings, store, df)
    with tab_heat:
        render_heatmap(df)
    with tab_audit:
        st.dataframe(store.audit(300), width="stretch", hide_index=True)


def render_ranking(df: pd.DataFrame) -> None:
    st.subheader("Welche Strategie funktioniert auf welchem Markt gerade?")
    st.info(
        "**So liest du diese Tabelle:** Jede Zeile = eine Strategie auf einem Coin. "
        "**Score 0–100** (höher = besser), **Ampel** 🟢🟡🔴. Achte auf die Spalte **Trades** — "
        "nur bei vielen Trades ist die Zahl aussagekräftig, wenige = Zufall.",
        icon="💡",
    )
    with st.expander("Wie wird der Score genau berechnet? (für Interessierte)"):
        st.caption(
            "Score = 0.35 × Edge (Out-of-Sample-Erwartung) + 0.30 × Robustheit (Monte-Carlo) + "
            "0.20 × Regime-Passung + 0.15 × Recency, mal Konfidenz (Trade-Anzahl), mal "
            "Trial-Abschlag (Overfitting) mal Kosten-Robustheit. Keine Black Box — Formel in stats/score.py."
        )

    c1, c2, c3 = st.columns(3)
    markets = c1.multiselect("Markt", sorted(df["market"].unique()), default=list(df["market"].unique()))
    tfs = c2.multiselect("Timeframe", sorted(df["timeframe"].unique()), default=list(df["timeframe"].unique()))
    only_sig = c3.checkbox(f"Nur statistisch belastbar (>= {MIN_TRADES_FOR_SIGNIFICANCE} Trades)", value=False)

    view = df[df["market"].isin(markets) & df["timeframe"].isin(tfs)]
    if only_sig:
        view = view[view["n_trades"] >= MIN_TRADES_FOR_SIGNIFICANCE]
    if view.empty:
        st.info("Keine Zeilen fuer diese Filter.")
        return

    view = view.copy()
    view["Ampel"] = view["traffic_light"].map({"green": "GRUEN", "yellow": "GELB", "red": "ROT"})
    view["MC-Return 90%-KI"] = view.apply(
        lambda r: f"{r['mc_return_ci_low']:+.1%} ... {r['mc_return_ci_high']:+.1%}", axis=1
    )
    view["Belastbar"] = view["n_trades"].ge(MIN_TRADES_FOR_SIGNIFICANCE).map({True: "ja", False: "NEIN"})

    cols = {
        "strategy": "Strategie", "market": "Markt", "symbol": "Symbol", "timeframe": "TF",
        "score": "Score", "Ampel": "Ampel", "n_trades": "Trades", "Belastbar": "Belastbar",
        "win_rate": "Win-Rate", "profit_factor": "Profit-Faktor", "expectancy_r": "Erw. R",
        "sharpe": "Sharpe", "max_drawdown": "Max DD", "mc_prob_profitable": "MC P(profitabel)",
        "MC-Return 90%-KI": "MC-Return 90%-KI", "mc_drawdown_ci_high": "MC DD 95%",
        "wf_efficiency": "WF-Effizienz", "regime": "Regime", "data_source": "Datenquelle",
    }
    table = view[list(cols)].rename(columns=cols).sort_values("Score", ascending=False)
    st.dataframe(
        table.style.format({
            "Score": "{:.1f}", "Win-Rate": "{:.1%}", "Profit-Faktor": "{:.2f}",
            "Erw. R": "{:+.3f}", "Sharpe": "{:.2f}", "Max DD": "{:.1%}",
            "MC P(profitabel)": "{:.0%}", "MC DD 95%": "{:.1%}", "WF-Effizienz": "{:.2f}",
        }).map(lambda v: f"color:{LIGHT_COLORS.get({'GRUEN':'green','GELB':'yellow','ROT':'red'}.get(v,''),'')}",
               subset=["Ampel"]),
        width="stretch", hide_index=True, height=480,
    )
    st.caption(
        "Win-Rate allein ist irrefuehrend: eine Strategie mit 90 % Trefferquote und "
        "einem katastrophalen Verlierer ist unprofitabel. Deshalb immer Profit-Faktor, "
        "Erwartungswert (R) und das Monte-Carlo-Intervall mitlesen."
    )


def render_detail(store: Store, df: pd.DataFrame) -> None:
    labels = df.apply(lambda r: f"{r['strategy']} | {r['symbol']} | {r['timeframe']}", axis=1).tolist()
    choice = st.selectbox("Kombination", labels)
    strategy, symbol, timeframe = [s.strip() for s in choice.split("|")]
    row = df[(df.strategy == strategy) & (df.symbol == symbol) & (df.timeframe == timeframe)].iloc[0]

    import json
    payload = json.loads(row["payload"]) if row["payload"] else {}
    score = payload.get("score", {})

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Score", f"{row['score']:.1f}", row["traffic_light"])
    c2.metric("Trades", int(row["n_trades"]))
    c3.metric("Profit-Faktor", f"{row['profit_factor']:.2f}")
    c4.metric("Max Drawdown", f"{row['max_drawdown']:.1%}")

    # Ehrlichkeits-Signale (aus dem Score): Kosten-Robustheit + Trial-Abschlag
    cr = score.get("cost_robust")
    tf_factor = score.get("trial_factor")
    d1, d2 = st.columns(2)
    if cr is not None:
        d1.metric("Robust bei doppelten Kosten?", "JA ✅" if cr else "NEIN ⚠️",
                  help="Macht der Vorteil auch mit Gebühr/Slippage ×2 noch Geld?")
    if tf_factor is not None:
        d2.metric("Trial-Abschlag (Overfitting)", f"×{tf_factor:.2f}",
                  help="Score-Abschlag für die Zahl getesteter Varianten (Deflated-Sharpe-Idee).")

    for w in payload.get("warnings", []):
        st.warning(w)

    st.markdown("**Score-Zerlegung** (so kommt die Zahl zustande)")
    comps = {k[2:]: v for k, v in score.items() if k.startswith("c_")}
    if comps:
        st.bar_chart(pd.Series(comps, name="Komponente (0..1)"))
        st.code(score.get("explanation", ""), language="text")

    trades = store.trades(source="backtest", strategy=strategy)
    trades = trades[(trades.symbol == symbol) & (trades.timeframe == timeframe)]
    if not trades.empty:
        t = trades.sort_values("entry_time").copy()
        t["entry_time"] = pd.to_datetime(t["entry_time"])
        t["kumuliertes_R"] = t["r_multiple"].cumsum()
        g1, g2 = st.columns(2)
        with g1:
            st.markdown("**Kumulierte R-Kurve (Backtest)**")
            st.line_chart(t.set_index("entry_time")["kumuliertes_R"])
        with g2:
            st.markdown("**Drawdown der R-Kurve**")
            eq = (1 + t.set_index("entry_time")["r_multiple"] * 0.01).cumprod()
            st.area_chart(drawdown_series(eq))

        g3, g4 = st.columns(2)
        with g3:
            st.markdown("**Verteilung der Trade-Ergebnisse (R)**")
            hist = pd.cut(t["r_multiple"].clip(-3, 3), bins=24).value_counts().sort_index()
            hist.index = [f"{i.mid:.1f}" for i in hist.index]
            st.bar_chart(hist)
        with g4:
            st.markdown("**Gewinner vs. Verlierer**")
            wins = int((t["r_multiple"] > 0).sum())
            losses = int((t["r_multiple"] <= 0).sum())
            st.bar_chart(pd.Series({"Gewinner": wins, "Verlierer": losses}))

        # Monte-Carlo-Fächer: viele simulierte Kapitalpfade als Graph
        st.markdown("**Monte-Carlo-Simulation: mögliche Kapitalpfade** "
                    "(zeigt die Streuung, keine Prognose)")
        st.line_chart(_mc_fan(t["r_multiple"].to_numpy()))

        st.markdown("**Trade-Historie**")
        st.dataframe(t.tail(200), width="stretch", hide_index=True)
    else:
        st.info("Keine gespeicherten Trades fuer diese Kombination.")

    paper_eq = store.equity_curve("paper")
    if len(paper_eq):
        st.markdown("**Paper-Trading-Equity (alle Strategien gemeinsam)**")
        paper_eq["ts"] = pd.to_datetime(paper_eq["ts"])
        st.line_chart(paper_eq.set_index("ts")["equity"])


def _mc_fan(r_multiples, n_paths: int = 40, risk: float = 0.01, seed: int = 3) -> pd.DataFrame:
    """Einige simulierte Kapitalpfade fuer die Fächer-Grafik (Perzentil-Baender)."""
    import numpy as np

    r = np.asarray([x for x in r_multiples if np.isfinite(x)], dtype=float)
    if r.size == 0:
        return pd.DataFrame()
    rng = np.random.default_rng(seed)
    draws = rng.choice(r, size=(n_paths, r.size), replace=True)
    equity = np.cumprod(np.clip(1 + draws * risk, 1e-6, None), axis=1)
    pct = np.percentile(equity, [5, 50, 95], axis=0)
    return pd.DataFrame({"5% (schlecht)": pct[0], "Median": pct[1], "95% (gut)": pct[2]})


def score_background(value) -> str:
    """Rot-Gelb-Gruen-Verlauf fuer einen Score 0..100.

    Bewusst selbst gerechnet statt via Styler.background_gradient - das wuerde
    matplotlib als Abhaengigkeit erzwingen, die das Projekt sonst nicht braucht.
    """
    if value is None or pd.isna(value):
        return ""
    t = max(0.0, min(float(value), 100.0)) / 100.0
    if t < 0.5:                      # rot -> gelb
        r, g = 220, int(38 + (163 - 38) * (t / 0.5))
    else:                            # gelb -> gruen
        r, g = int(220 - (220 - 22) * ((t - 0.5) / 0.5)), 163
    return f"background-color: rgba({r},{g},60,0.55)"


ORDER_EVENTS = ("order_placed", "order_denied", "position_closed", "forced_flat",
                "loop_stopped", "circuit_breaker", "kill_switch")


def _start_bot_process(settings) -> str:
    """Startet den Paper-Trader (serve) als eigenen, unabhaengigen Prozess.
    Laeuft weiter, auch wenn das Dashboard geschlossen wird. Idiotensicher: ein Klick."""
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent
    log_file = open(root / "bot.log", "a", encoding="utf-8")
    cmd = [_sys.executable, str(root / "cli.py"), "serve", "--interval", "60", "--reeval-hours", "6"]
    kwargs = {"stdout": log_file, "stderr": subprocess.STDOUT, "cwd": str(root)}
    if _sys.platform == "win32":
        # eigener Prozess, unabhaengig vom Dashboard-Fenster
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)  # noqa: S603
    return "Bot gestartet — er handelt jetzt aktiv (Paper) und erscheint gleich als LIVE."


@st.fragment(run_every=5)
def render_live_paper(store: Store, settings) -> None:
    """Live-Panel: zeigt in Echtzeit, was der Paper-Loop tut. Aktualisiert sich
    alle 5 Sekunden selbst (st.fragment run_every), liest dabei frisch aus der DB."""
    from datetime import datetime, timezone

    st.subheader("Live Paper-Trader — was der Bot gerade macht")
    st.caption("Aktiver Handel gegen ECHTE Börsendaten, aber mit Spielgeld (kein echtes Geld). "
               "Der Bot passt sich selbst an. Dieses Panel frischt sich alle 5s auf.")

    hb = store.heartbeat("paper")
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb["ts"])).total_seconds() if hb else 9e9
    except Exception:  # noqa: BLE001
        age = 9e9
    running = age < 120 and store.get_control("desired_state", "stopped") == "running"

    # ---- Steuerung (alles per Knopf, kein CLI noetig) --------------------
    # In der Cloud (CLOUD_READONLY gesetzt, z. B. Streamlit Cloud) laeuft der Bot ueber
    # GitHub Actions - hier gibt es keinen lokalen Prozess zum Starten/Stoppen. Deshalb
    # nur-lesen: Steuerknoepfe ausblenden statt tote Buttons zu zeigen.
    import os as _os
    readonly = bool(_os.environ.get("CLOUD_READONLY"))
    if readonly:
        st.info("**Nur-Lesen-Ansicht (Cloud).** Der Bot läuft über GitHub Actions und "
                "aktualisiert diese Seite regelmäßig. Start/Stopp erfolgt über das Repo "
                "(Actions-Tab / active_combos.yaml), nicht hier.")
    else:
        st.markdown("**Steuerung**")
        b1, b2, b3, b4 = st.columns(4)
        if b1.button("BOT STARTEN", type="primary", disabled=running, width="stretch"):
            msg = _start_bot_process(settings)
            store.set_control("desired_state", "running")
            st.success(msg)
        if b2.button("BOT STOPPEN", disabled=not running, width="stretch"):
            store.set_control("desired_state", "stopped")
            st.warning("Stopp-Befehl gesendet — der Bot stellt Positionen glatt und hält an.")
        if b3.button("JETZT ANPASSEN", width="stretch",
                     help="Neu auswerten + optimieren; der Bot übernimmt die validierten Parameter."):
            store.set_control("command", "adapt")
            st.info("Anpassung angestoßen — läuft beim nächsten Tick des Bots.")
        if b4.button("NOT-AUS (Kill)", width="stretch"):
            store.set_control("desired_state", "stopped")
            store.log("kill_switch", mode="paper", source="dashboard_live")
            st.error("NOT-AUS: Bot wird gestoppt und Positionen glattgestellt.")

    last_adapt = store.get_control("last_adaptation", "noch nie")
    adapted_n = store.get_control("adapted_combos", "0")
    st.caption(f"Letzte Selbst-Anpassung: **{last_adapt}** · aktuell **{adapted_n}** validierte "
               f"Kombinationen aktiv. Der Bot re-optimiert automatisch, weil sich Märkte ändern.")
    st.divider()

    if not hb:
        st.info("Der Bot lief noch nicht. Klicke oben auf **BOT STARTEN** — dann erscheinen hier "
                "live Positionen, Orders und die Kapitalkurve.")
        return

    # Live/gestoppt anhand des letzten Lebenszeichens
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb["ts"])).total_seconds()
    except Exception:  # noqa: BLE001
        age = 9e9
    live = age < 120
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Status", "LIVE" if live else "gestoppt",
              f"aktiv (vor {age:.0f}s)" if live else "kein Lebenszeichen",
              delta_color="normal" if live else "off")
    if not live:
        st.warning("Der Bot laeuft gerade **nicht** (kein aktuelles Lebenszeichen). "
                   "Klicke oben **BOT STARTEN**. Offene Positionen unten sind der letzte Stand.")
    start = settings.initial_capital
    eq = hb.get("equity") or start
    c2.metric("Kapital (Paper)", f"{eq:,.2f}", f"{(eq/start-1)*100:+.2f} %", delta_color="off")
    c3.metric("Offene Positionen", int(hb.get("open_positions") or 0))
    c4.metric("Tagesverlust", f"{(hb.get('daily_pnl_pct') or 0)*100:+.2f} %")
    if hb.get("tripped"):
        st.error(f"CIRCUIT BREAKER aktiv — Handel gestoppt. Grund: {hb.get('note','')}")

    st.markdown("**Offene Positionen**")
    pos = store.positions_snapshot("paper")
    if pos.empty:
        st.write("Aktuell keine offenen Positionen.")
    else:
        pos = pos.copy()
        pos["Richtung"] = pos["qty"].apply(lambda q: "LONG" if q > 0 else "SHORT")
        # "strategy": zeigt, WELCHE Strategie eine Position haelt - wichtig, seit
        # Positionen pro (Strategie, Symbol) statt nur pro Symbol getrackt werden.
        # Vorher liess sich hier nicht erkennen, dass mehrere Strategien gleichzeitig
        # (und potenziell gegeneinander) auf demselben Symbol aktiv waren.
        view = pos[["symbol", "strategy", "Richtung", "qty", "avg_price", "mark_price", "stop_loss",
                    "take_profit", "unrealized"]].rename(columns={
            "symbol": "Symbol", "strategy": "Strategie", "qty": "Menge", "avg_price": "Einstieg",
            "mark_price": "Kurs", "stop_loss": "Stop", "take_profit": "Ziel", "unrealized": "Unrealisiert"})
        st.dataframe(view.style.format({
            "Menge": "{:.4f}", "Einstieg": "{:.4f}", "Kurs": "{:.4f}", "Stop": "{:.4f}",
            "Ziel": "{:.4f}", "Unrealisiert": "{:+.2f}"}), width="stretch", hide_index=True)

    # ---- Chart: wo der Bot handelt (Kerzen + Kauf/Verkauf-Marker) ---------
    st.markdown("**Chart — wo der Bot handelt** (Kurs mit Ein-/Ausstiegen)")
    audit_all = store.audit(500)
    traded_syms = sorted(set(pos["symbol"].tolist()) |
                         set(audit_all[audit_all["event"] == "order_placed"]["symbol"].dropna().tolist())) \
        if not audit_all.empty else sorted(pos["symbol"].tolist())
    if traded_syms:
        chosen = st.selectbox("Symbol", traded_syms, key="live_chart_symbol")
        _render_trade_chart(store, settings, audit_all, chosen)
    else:
        st.write("Sobald der Bot handelt, erscheint hier der Kurs mit seinen Ein- und Ausstiegen.")

    g1, g2 = st.columns([3, 2])
    with g1:
        st.markdown("**Live-Kapitalkurve (Paper)** — das Auf und Ab deines Kapitals")
        curve = store.equity_curve("paper")
        if len(curve) > 1:
            curve["ts"] = pd.to_datetime(curve["ts"])
            st.line_chart(curve.set_index("ts")["equity"])
        else:
            st.write("Noch keine Kapitalpunkte — der erste Tick fuellt die Kurve.")
    with g2:
        st.markdown("**Aktivitaet (was der Bot tut)**")
        audit = store.audit(200)
        if not audit.empty:
            acts = audit[audit["event"].isin(ORDER_EVENTS)].head(15)
            if acts.empty:
                st.write("Noch keine Orders.")
            else:
                st.dataframe(acts[["ts", "event", "symbol", "details"]].rename(columns={
                    "ts": "Zeit", "event": "Ereignis", "symbol": "Symbol", "details": "Info"}),
                    width="stretch", hide_index=True, height=360)

    # Echte, abgeschlossene Paper-Trades je Strategie (Bugfix: frueher landete hier
    # NICHTS - Paper-Trades wurden nie strukturiert in der `trades`-Tabelle
    # gespeichert, nur als unstrukturierter Audit-Log-Text. Genau diese fehlende
    # Sicht liess die Positions-Kollision zwischen Strategien lange unentdeckt.
    st.markdown("**Echte Paper-Trades (abgeschlossen, je Strategie)**")
    paper_trades = store.trades(source="paper")
    if paper_trades.empty:
        st.write("Noch keine abgeschlossenen Paper-Trades.")
    else:
        pt = paper_trades.head(50).copy()
        st.dataframe(pt[["entry_time", "strategy", "symbol", "direction", "entry_price",
                         "exit_price", "qty", "pnl", "r_multiple", "exit_reason"]].rename(columns={
            "entry_time": "Einstieg (Zeit)", "strategy": "Strategie", "symbol": "Symbol",
            "direction": "Richtung", "entry_price": "Einstiegspreis", "exit_price": "Ausstiegspreis",
            "qty": "Menge", "pnl": "PnL", "r_multiple": "R", "exit_reason": "Ausstiegsgrund"}),
            width="stretch", hide_index=True, height=280)
        by_strategy = pt.groupby("strategy")["pnl"].agg(["count", "sum"]).rename(
            columns={"count": "Trades", "sum": "PnL gesamt"}).sort_values("PnL gesamt")
        st.caption("PnL je Strategie (reale Paper-Trades, nicht Backtest):")
        st.dataframe(by_strategy.style.format({"PnL gesamt": "{:+.2f}"}), width="stretch")


def _render_trade_chart(store: Store, settings, audit: pd.DataFrame, symbol: str) -> None:
    """Kerzenchart des Symbols mit Markern, WO der Bot ein-/ausgestiegen ist."""
    import json

    import altair as alt

    from data.loader import DataLoader, MarketSpec

    market = "crypto" if "/" in symbol else ("forex" if symbol.endswith("=X") else "stocks")
    tf = "4h" if market == "crypto" else "1d"
    try:
        ohlcv = DataLoader(allow_synthetic=True).load(MarketSpec(market, symbol, tf), bars=200)
    except Exception as exc:  # noqa: BLE001
        st.info(f"Kurs fuer {symbol} gerade nicht ladbar ({exc}).")
        return

    o = ohlcv.tail(160).reset_index()
    o.columns = ["time"] + list(o.columns[1:])
    o["steigt"] = o["close"] >= o["open"]

    base = alt.Chart(o).encode(
        x=alt.X("time:T", title=None),
        color=alt.Color("steigt:N",
                        scale=alt.Scale(domain=[True, False], range=["#16a34a", "#dc2626"]),
                        legend=None),
    )
    wick = base.mark_rule().encode(y=alt.Y("low:Q", title="Kurs", scale=alt.Scale(zero=False)), y2="high:Q")
    body = base.mark_bar(size=4).encode(y="open:Q", y2="close:Q")
    layers = [wick, body]

    # Marker aus dem Audit-Log: order_placed (Ein-/Ausstieg) fuer dieses Symbol
    marks = []
    if not audit.empty:
        for _, row in audit[(audit["event"] == "order_placed") & (audit["symbol"] == symbol)].iterrows():
            try:
                d = json.loads(row["details"]) if row["details"] else {}
                marks.append({"time": pd.to_datetime(row["ts"]), "price": d.get("price"),
                              "Aktion": "Kauf" if d.get("side") == "buy" else "Verkauf"})
            except Exception:  # noqa: BLE001
                continue
    if marks:
        mdf = pd.DataFrame(marks).dropna()
        if not mdf.empty:
            markers = alt.Chart(mdf).mark_point(size=140, filled=True, opacity=0.9).encode(
                x="time:T", y="price:Q",
                shape=alt.Shape("Aktion:N", scale=alt.Scale(domain=["Kauf", "Verkauf"],
                                                            range=["triangle-up", "triangle-down"])),
                color=alt.Color("Aktion:N", scale=alt.Scale(domain=["Kauf", "Verkauf"],
                                                            range=["#16a34a", "#dc2626"]), legend=None),
                tooltip=["time:T", "Aktion:N", "price:Q"],
            )
            layers.append(markers)

    st.altair_chart(alt.layer(*layers).properties(height=340).interactive(), use_container_width=True)
    st.caption("Grüne Dreiecke = Kauf-Einstieg, rote = Verkauf. Kerzen zeigen das Auf und Ab des echten Kurses.")


@st.cache_data(show_spinner="Portfolio wird berechnet (Backtests laufen) ...")
def _build_portfolio_cached(db_path: str, run_at_key: str, max_positions: int, max_corr: float,
                            target_vol: float = 0.0):
    """Baut das Portfolio aus den gespeicherten Auswertungen. Gecacht ueber den
    juengsten Auswertungszeitpunkt, damit nicht bei jedem Klick neu gerechnet wird."""
    from cli import _backtest_returns
    from stats.portfolio import QualityGates, build_portfolio, candidate_labels

    settings = load_settings(DEFAULT_CONFIG_PATH)
    store = Store(db_path)
    evals = store.latest_evaluations()
    rows = {f"{r['strategy']} | {r['symbol']} | {r['timeframe']}": r.to_dict()
            for _, r in evals.iterrows()}
    gates = QualityGates()
    candidates, _ = candidate_labels(rows, gates)
    returns, r_mult = _backtest_returns(rows, candidates, settings)  # nur Kandidaten -> schnell
    tf = evals.iloc[0]["timeframe"] if len(evals) else "1h"
    return build_portfolio(rows, returns, r_mult, gates=gates,
                           max_positions=max_positions, max_correlation=max_corr,
                           initial_capital=settings.initial_capital, timeframe=tf,
                           target_vol=(target_vol or None))


def render_portfolio(settings, store: Store, df: pd.DataFrame) -> None:
    st.subheader("Diversifiziertes Portfolio aus validierten Kombinationen")
    st.caption(
        "Ein Portfolio buendelt mehrere geprüfte, wenig korrelierte Kombinationen. "
        "Diversifikation senkt RISIKO und Schwankung - sie macht aus Verlierern aber "
        "keine Gewinner. Nur Kombinationen mit out-of-sample validiertem Vorteil "
        "(positive Erwartung, Effizienz >= 0.5, genug Trades, echte Daten) sind handelbar."
    )
    c1, c2, c3 = st.columns(3)
    max_pos = c1.slider("Max. Positionen", 2, 10, 6)
    max_corr = c2.slider("Max. Korrelation", 0.1, 0.9, 0.6, 0.05)
    target_vol = c3.slider("Vol-Ziel (0 = aus)", 0.0, 0.5, 0.0, 0.05,
                           help="Steuert die Gesamt-Schwankung auf ein Ziel (annualisiert). "
                                "0 = keine Vol-Steuerung.")
    if st.button("Portfolio berechnen", type="primary"):
        st.session_state.pf_requested = True
    if not st.session_state.get("pf_requested"):
        st.info("Klicke auf **Portfolio berechnen** - die Backtests dafuer laufen dann einmalig.")
        return

    run_key = str(df["run_at"].max()) if "run_at" in df else "0"
    pf = _build_portfolio_cached(str(settings.db_path), run_key, max_pos, max_corr, target_vol)

    if pf.validated:
        st.success(pf.note)
    else:
        st.warning(pf.note)
    if not pf.members:
        return

    members = pd.DataFrame([{
        "Kombination": m.label, "Gewicht": m.weight, "Erw. R": m.expectancy_r,
        "Trades": m.n_trades, "Max DD": m.max_drawdown,
    } for m in pf.members])
    st.dataframe(members.style.format({"Gewicht": "{:.1%}", "Erw. R": "{:+.3f}",
                                       "Max DD": "{:.1%}"}), width="stretch", hide_index=True)

    g1, g2 = st.columns([2, 1])
    with g1:
        st.markdown("**Kombinierte Kapitalkurve (Portfolio)**")
        st.line_chart(pf.equity_curve)
    with g2:
        st.markdown("**Risiko-Gewichtung**")
        st.bar_chart(members.set_index("Kombination")["Gewicht"])

    m = pf.metrics
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Sharpe", f"{m.sharpe:.2f}")
    k2.metric("Max Drawdown", f"{m.max_drawdown:.1%}")
    k3.metric("Erwartung/Trade", f"{m.expectancy_r:+.3f} R")
    k4.metric("Gesamtrendite", f"{m.total_return:+.1%}")
    if pf.target_vol:
        v1, v2, v3 = st.columns(3)
        v1.metric("Vol-Ziel", f"{pf.target_vol:.0%}")
        v2.metric("Gemessene Vola", f"{pf.realized_vol:.0%}")
        v3.metric("Hebel", f"×{pf.leverage:.2f}",
                  help="<1 = Exposure gesenkt, >1 = erhöht, um das Vol-Ziel zu treffen.")
    st.caption(pf.diversification_note)

    if len(pf.correlation) > 1:
        st.markdown("**Korrelationsmatrix der Mitglieder** (niedrig = gut diversifiziert)")
        st.dataframe(pf.correlation.style.format("{:.2f}").map(_corr_color), width="stretch")

    if not pf.validated:
        st.error("Kein Mitglied hat den Out-of-Sample-Test bestanden - dieses Portfolio ist "
                 "ILLUSTRATIV und NICHT handelbar. Es zeigt nur die Glaettung durch Diversifikation.")


def _corr_color(v) -> str:
    if v is None or pd.isna(v):
        return ""
    a = min(abs(float(v)), 1.0)
    return f"background-color: rgba(220,{int(163*(1-a))},60,0.5)"


def render_heatmap(df: pd.DataFrame) -> None:
    st.subheader("Wo sind die Signale gerade am staerksten?")
    pivot = df.pivot_table(index="strategy", columns=["market", "timeframe"],
                           values="score", aggfunc="max")
    st.dataframe(
        pivot.style.map(score_background).format("{:.0f}", na_rep="-"),
        width="stretch",
    )
    st.caption("Zellwert = bester Score dieser Strategie im jeweiligen Markt/Timeframe.")


if __name__ == "__main__":
    main()

