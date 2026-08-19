"""CLI: Evaluation, Paper-Loop und der manuelle Live-Bestaetigungsflow.

    python cli.py evaluate            # Backtest + Walk-Forward + Monte-Carlo, schreibt in die DB
    python cli.py paper               # Paper-Trading-Loop starten (kein echtes Geld)
    python cli.py dashboard           # Streamlit-Dashboard starten
    python cli.py enable-live         # manueller Bestaetigungsflow (Standard: bricht ab)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config.settings import load_settings  # noqa: E402
from core.store import Store  # noqa: E402
from risk.manager import Mode, RiskManager  # noqa: E402

RISK_BANNER = """
================================================================================
  RISIKOHINWEIS - BITTE LESEN
  Dieses Programm ist ein Analysewerkzeug, KEINE Finanzberatung.
  Backtest-Ergebnisse sind keine Zusage kuenftiger Gewinne. Handel mit
  Hebel- und Finanzprodukten kann zum TOTALVERLUST des eingesetzten
  Kapitals fuehren. Nutzung auf eigenes Risiko.
================================================================================
"""


def cmd_evaluate(args) -> int:
    from backtest.evaluate import evaluate_universe

    settings = load_settings(args.config)
    store = Store(settings.db_path)

    def progress(i, total, label):
        print(f"[{i}/{total}] {label}", flush=True)

    results = evaluate_universe(settings, store=store, progress=progress)
    print(f"\n{len(results)} Kombinationen ausgewertet. Top 10:\n")
    print(f"{'Strategie':<24}{'Symbol':<12}{'TF':<5}{'Score':>7}{'Trades':>8}{'PF':>7}  Ampel")
    for ev in results[:10]:
        s = ev.score
        print(f"{ev.strategy:<24}{ev.symbol:<12}{ev.timeframe:<5}"
              f"{s.total:>7.1f}{ev.metrics.n_trades:>8}{ev.metrics.profit_factor:>7.2f}  {s.traffic_light}")
        for w in s.warnings:
            print(f"    ! {w}")
    return 0


def _make_broker(settings, store=None):
    """Baut den Paper-Broker MIT den in config.yaml hinterlegten Gebuehren-/Slippage-
    Annahmen. Bugfix: vorher wurde `PaperBroker(starting_balance=...)` ohne fee_rate/
    slippage_rate aufgerufen - die Klassen-Defaults trafen zufaellig die damaligen
    Config-Werte, aber eine Aenderung von `backtest.fee_rate`/`backtest.slippage_rate`
    in config.yaml hatte NIE einen Effekt auf den Paper-/Live-Handel, nur auf Backtests.

    Bugfix Neustart-Datenverlust: der Broker begann bisher bei JEDEM Prozessstart
    (Absturz, Server-Reboot, Watchdog-Neustart bei autonomem Dauerbetrieb) wieder bei
    `capital.initial` mit leerem Positionsbestand - ein tatsaechlich offener Bestand aus
    dem vorherigen Lauf wurde stillschweigend verworfen, ohne dass das irgendwo sichtbar
    wurde. Fuer echten unbeaufsichtigten Dauerbetrieb (`serve` mit Auto-Neustart) ist das
    inakzeptabel. Wird ein `store` uebergeben, wird der letzte bekannte Stand (Heartbeat +
    Positions-Snapshot) vor der Rueckgabe wiederhergestellt - siehe restore_broker_state()."""
    from execution.paper import PaperBroker

    cfg = settings.backtest_config()
    broker = PaperBroker(starting_balance=settings.initial_capital,
                         fee_rate=cfg.fee_rate, slippage_rate=cfg.slippage_rate)
    if store is not None:
        restore_broker_state(broker, store)
    return broker


def restore_broker_state(broker, store) -> bool:
    """Stellt Kontostand und offene Positionen aus der DB wieder her (siehe _make_broker).

    Gibt True zurueck, wenn ein vorheriger Lauf gefunden und wiederhergestellt wurde, sonst
    False (dann bleibt der Broker unveraendert bei `capital.initial` mit leerem Bestand -
    z. B. bei einer wirklich frischen Installation ohne vorherigen Lauf)."""
    from datetime import datetime, timezone

    from core.types import Position

    hb = store.heartbeat(broker.mode)
    if not hb or hb.get("equity") is None:
        return False
    snapshot = store.positions_snapshot(broker.mode)
    unrealized_total = float(snapshot["unrealized"].fillna(0.0).sum()) if len(snapshot) else 0.0
    broker.cash = float(hb["equity"]) - unrealized_total
    for _, p in snapshot.iterrows():
        key = (p.get("strategy") or "", p["symbol"])
        raw_entry_time = p.get("entry_time")
        try:
            entry_time = datetime.fromisoformat(raw_entry_time) if raw_entry_time else None
        except (TypeError, ValueError):
            entry_time = None
        broker._positions[key] = Position(
            p["symbol"], float(p["qty"]), float(p["avg_price"]), p.get("stop_loss"),
            p.get("take_profit"), strategy=p.get("strategy") or "",
            entry_time=entry_time or datetime.now(timezone.utc),
        )
        mark = p.get("mark_price")
        broker.update_price(p["symbol"], float(mark) if mark is not None else float(p["avg_price"]))
    print(f"[bot] Zustand aus vorherigem Lauf wiederhergestellt: {len(snapshot)} offene "
          f"Position(en), Kapital {float(hb['equity']):.2f} (statt Neustart bei "
          f"{broker.starting_balance:.2f}).")
    return True


def _make_loop_config(settings, poll_seconds: int):
    """Baut die PaperLoopConfig MIT den Werten aus config.yaml (backtest-Sektion).

    Bugfix: `PaperLoopConfig(poll_seconds=...)` uebernahm nur das Poll-Intervall;
    stop_atr_mult, take_profit_r und atr_period blieben auf den Klassen-Defaults, sodass
    eine Aenderung dieser Werte in config.yaml KEINEN Effekt auf den Paper-Handel hatte -
    der Bot handelte dann mit anderen Stops als die validierte Strategie."""
    from execution.paper_loop import PaperLoopConfig

    bt = settings.backtest_config()
    return PaperLoopConfig(
        poll_seconds=poll_seconds,
        stop_atr_mult=bt.stop_atr_mult,
        take_profit_r=bt.take_profit_r if bt.take_profit_r else 2.0,
        atr_period=bt.atr_period,
    )


def _make_risk_manager(settings, store, mode: Mode = Mode.PAPER, source: str = "paper") -> RiskManager:
    """Erzeugt den RiskManager mit persistiertem Circuit-Breaker-Status.

    Bugfix: der Trip-Status ('tripped') lebte vorher nur im Prozessspeicher. Ein
    Neustart des Bot-Prozesses (z. B. per Klick auf 'BOT STARTEN' im Dashboard, oder
    nach einem Absturz) erzeugte einen komplett frischen RiskManager mit tripped=False -
    eine am selben Tag ausgeloeste Tagesverlust-Sperre war damit faktisch wirkungslos,
    sobald der Prozess neu startete. Jetzt wird der Trip-Status in der DB gespiegelt
    (Tabelle `control`, Schluessel `risk_trip_<source>`) und beim Start wiederhergestellt,
    solange er noch vom selben UTC-Kalendertag stammt und nicht bewusst per
    reset(confirm=True) aufgehoben wurde."""
    import json
    from datetime import datetime, timezone

    key = f"risk_trip_{source}"

    def _on_trip(reason: str) -> None:
        store.set_control(key, json.dumps(
            {"date": datetime.now(timezone.utc).date().isoformat(), "reason": reason}))

    def _on_reset() -> None:
        store.set_control(key, "")

    risk = RiskManager(settings.risk_limits(), settings.initial_capital, mode=mode,
                       on_trip=_on_trip, on_reset=_on_reset)

    raw = store.get_control(key, "")
    if raw:
        try:
            saved = json.loads(raw)
        except ValueError:
            saved = {}
        if saved.get("date") == datetime.now(timezone.utc).date().isoformat():
            risk.trip(f"{saved.get('reason', 'unbekannt')} (aus vorherigem Lauf wiederhergestellt - "
                      f"heute noch nicht bewusst zurueckgesetzt)")
            print("[risk] Circuit Breaker war heute bereits ausgeloest und wird NICHT automatisch "
                  "aufgehoben - ueber den Dashboard-Knopf oder den Befehl 'reset_breaker' bestaetigen.")
    return risk


def cmd_paper(args) -> int:
    from data.loader import DataLoader
    from execution.paper_loop import PaperLoopConfig, PaperTradingLoop
    from strategies import REGISTRY

    print(RISK_BANNER)
    settings = load_settings(args.config)
    if settings.effective_mode == Mode.LIVE:
        print("Live-Modus ist freigeschaltet - dieser Befehl startet trotzdem PAPER. "
              "Live-Ausfuehrung ist in dieser Version nicht implementiert.")

    store = Store(settings.db_path)
    broker = _make_broker(settings, store)
    risk = _make_risk_manager(settings, store, mode=Mode.PAPER)
    combos = [
        (s, m, sym, tf)
        for (m, sym, tf) in settings.universe()
        for s in settings.strategy_names()
        if REGISTRY[s]().supports(m, tf)
    ]
    loop = PaperTradingLoop(
        broker, risk, store, combos,
        _make_loop_config(settings, args.interval),
        settings.data_loader(), {s: settings.params_for(s) for s in settings.strategy_names()},
    )
    print(f"PAPER MODE - kein echtes Geld. {len(combos)} Kombinationen, "
          f"Intervall {args.interval}s. Strg+C zum Beenden.")
    try:
        loop.run_forever() if not args.once else loop.tick()
    except KeyboardInterrupt:
        loop.stop("Strg+C")
    print(f"Endkapital (Paper): {broker.get_account_balance():.2f}")
    return 0


def cmd_dashboard(args) -> int:
    import subprocess

    app = Path(__file__).resolve().parent / "dashboard" / "app.py"
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app)])


def cmd_doctor(args) -> int:
    """Selbsttest in Klartext: prueft, ob alles bereit ist. Fuer Nicht-Techniker.
    Sagt bei jedem Punkt OK / WARNUNG / FEHLER und was zu tun ist."""
    ok, warn, fail = "  [OK]   ", "  [!]    ", "  [FEHLER] "
    problems = 0

    print("\n=== SELBSTTEST (doctor) ===\n")

    # 1. Python-Version
    v = sys.version_info
    if v >= (3, 11):
        print(f"{ok}Python {v.major}.{v.minor}")
    else:
        print(f"{fail}Python {v.major}.{v.minor} - benoetigt 3.11+. Bitte neuere Version installieren.")
        problems += 1

    # 2. Pakete - nur pandas/numpy/yaml sind Pflicht; der Rest schaltet nur ein
    #    einzelnes Feature frei und ist daher hoechstens eine Warnung.
    import importlib
    frozen = getattr(sys, "frozen", False)
    needed = {"pandas": ("Pflicht", True), "numpy": ("Pflicht", True), "yaml": ("Pflicht", True),
              "streamlit": ("Dashboard", False), "ccxt": ("Krypto-Daten", False),
              "yfinance": ("Aktien/Forex", False), "cryptography": ("Schluessel-Tresor", False)}
    for pkg, (zweck, pflicht) in needed.items():
        try:
            importlib.import_module(pkg)
            print(f"{ok}Paket {pkg} ({zweck})")
        except ImportError:
            if pflicht:
                print(f"{fail}Paket {pkg} fehlt ({zweck}). Loesung: pip install -r requirements.txt")
                problems += 1
            elif pkg == "streamlit" and frozen:
                print(f"{ok}Dashboard laeuft ueber START.bat, nicht ueber die .exe (so gewollt)")
            else:
                print(f"{warn}Paket {pkg} fehlt - nur '{zweck}' ist dann nicht verfuegbar. "
                      f"Optional: pip install -r requirements.txt")

    # 3. Konfiguration
    try:
        settings = load_settings(args.config)
        conf_problems = settings.validate()
        if conf_problems:
            for p in conf_problems:
                print(f"{fail}Konfiguration: {p}")
            problems += len(conf_problems)
        else:
            print(f"{ok}Konfiguration gueltig ({len(settings.universe())} Markt-Kombinationen, "
                  f"{len(settings.strategy_names())} Strategien)")
    except Exception as exc:  # noqa: BLE001
        print(f"{fail}config.yaml kann nicht gelesen werden: {exc}")
        return 1

    # 4. Datenbank beschreibbar
    try:
        store = Store(settings.db_path)
        store.log("doctor_check", mode="test")
        n = len(store.latest_evaluations())
        print(f"{ok}Datenbank beschreibbar ({n} gespeicherte Auswertungen)")
    except Exception as exc:  # noqa: BLE001
        print(f"{fail}Datenbank-Problem: {exc}")
        problems += 1

    # 5. Echte Boersendaten erreichbar
    print("  ...pruefe Verbindung zur Boerse (echte Daten)...")
    try:
        from data.loader import DataLoader, MarketSpec
        dl = DataLoader(allow_synthetic=False, max_retries=2, retry_backoff=1.0)
        df = dl.load(MarketSpec("crypto", "BTC/USDT", "1h"), bars=50, refresh=True)
        last = df["close"].iloc[-1]
        print(f"{ok}Echte Krypto-Daten erreichbar (BTC/USDT zuletzt {last:,.2f})")
    except Exception as exc:  # noqa: BLE001
        print(f"{warn}Krypto-Daten aktuell nicht erreichbar ({exc}). "
              f"Internet pruefen; spaeter erneut versuchen.")

    # 6. Geld-Sicherheit
    from execution.gate import is_unlocked
    from execution.live import LIVE_TRADING_IMPLEMENTED
    live_safe = (not is_unlocked()) and (not LIVE_TRADING_IMPLEMENTED)
    print(f"{ok if live_safe else warn}Geld-Sicherheit: Live-Handel ist "
          f"{'GESPERRT (kein echtes Geld moeglich)' if live_safe else 'freigeschaltet - Vorsicht!'}")
    print(f"{ok}Standardmodus: PAPER (kein echtes Geld)")

    print("\n=== ERGEBNIS ===")
    if problems == 0:
        print("Alles bereit. Du kannst das Dashboard oder den Live Paper-Trader starten.\n")
        return 0
    print(f"{problems} Problem(e) gefunden - siehe oben. Meist hilft: pip install -r requirements.txt\n")
    return 1


def cmd_serve(args) -> int:
    """Dauerbetrieb: aktiver Paper-Trader gegen ECHTE Boersendaten, vom Dashboard
    steuerbar. Passt sich selbst an: optimiert periodisch auf frischen Daten und
    handelt mit den validierten Parametern weiter.

    Bleibt IMMER im Paper-Modus - dieser Befehl kennt keinen Live-Pfad.
    """
    import threading
    import time

    from backtest.evaluate import evaluate_universe
    from execution.paper_loop import PaperLoopConfig, PaperTradingLoop
    from strategies import REGISTRY

    print(RISK_BANNER)
    settings = load_settings(args.config)
    store = Store(settings.db_path)

    # Schutz gegen zwei konkurrierende Bot-Prozesse auf DERSELBEN DB. Ohne diese
    # Pruefung konnte ein zweiter Start (z. B. zweiter Klick auf 'BOT STARTEN' bei kurz
    # veraltetem Heartbeat) einen zweiten serve-Prozess starten; beide schrieben dann
    # Heartbeat/Positionen/Equity in dieselbe DB und verfaelschten die Kapitalkurve.
    if not getattr(args, "force", False):
        from datetime import datetime, timezone
        hb = store.heartbeat("paper")
        if hb and store.get_control("desired_state", "stopped") == "running":
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb["ts"])).total_seconds()
            except Exception:  # noqa: BLE001
                age = 9e9
            if age < max(args.interval * 2, 120):
                print(f"[serve] Es laeuft bereits ein Bot (Lebenszeichen vor {age:.0f}s). "
                      f"Starte KEINEN zweiten Prozess - das wuerde die Kapitalkurve verfaelschen. "
                      f"Zum Erzwingen: --force.")
                return 1

    stop_event = threading.Event()
    trigger = threading.Event()   # sofortige Auffrischung/Anpassung auf Befehl

    broker = _make_broker(settings, store)
    risk = _make_risk_manager(settings, store, mode=Mode.PAPER)
    combos = [
        (s, m, sym, tf)
        for (m, sym, tf) in settings.universe()
        for s in settings.strategy_names()
        if REGISTRY[s]().supports(m, tf)
    ]
    loop = PaperTradingLoop(
        broker, risk, store, [],   # aktive Kombinationen werden aus validierten Vorteilen gesetzt
        _make_loop_config(settings, args.interval),
        settings.data_loader(), {s: settings.params_for(s) for s in settings.strategy_names()},
    )

    def _apply_active(portfolio_members, optimized: dict) -> None:
        """Der Bot handelt aktiv NUR die validierten Portfolio-Kombinationen (volle
        Qualitaetshuerde, alle Strategien). Optimierte Parameter werden - wo vorhanden -
        darueber gelegt. Das ist die ehrliche Basis fuer profitables Trading und haelt
        die Ticks schnell (nur wenige Echtdaten-Abrufe je Tick).

        Bugfix: `combo_params` wurde vorher per `clear()` gefolgt von `update()`
        aktualisiert - zwei getrennte Schritte, waehrend der Haupt-Thread parallel
        (in `loop.tick()`) daraus liest. Im kurzen Fenster dazwischen war das Dict leer,
        ein Tick in genau diesem Moment handelte dann versehentlich mit den
        Strategie-Defaults statt den validierten Parametern. Jetzt wird ein neues Dict
        aufgebaut und in einem einzigen (fuer CPython atomaren) Attribut-Zuweisungsschritt
        eingesetzt."""
        loop.combo_params = dict(optimized)
        loop.combos = [(m.strategy, _market_of(m.symbol), m.symbol, m.timeframe)
                       for m in portfolio_members]

    # Beim Start: bereits gelernte Parameter laden; aktive Menge kommt aus dem Portfolio
    optimized_start = _load_learned_params(settings)
    try:
        pf0 = _refresh_portfolio(settings, store)
        _apply_active(pf0.members if pf0 else [], optimized_start)
    except Exception:  # noqa: BLE001
        _apply_active([], optimized_start)

    def adapt_worker() -> None:
        """Der Selbst-Anpassungs-Thread: bewertet neu, optimiert, baut das Portfolio
        neu und schiebt die validierte, aktiv gehandelte Menge LIVE in den Loop.
        Nutzt eine EIGENE DB-Verbindung (Thread-sicher, kein 'database is locked')."""
        worker_store = Store(settings.db_path)
        while not stop_event.is_set():
            try:
                print(f"[serve] Anpassung ({time.strftime('%H:%M:%S')}): Auswertung + Optimierung ...", flush=True)
                evaluate_universe(settings, store=worker_store, loader=settings.data_loader())
                optimized = run_optimization(settings)
                pf = _refresh_portfolio(settings, worker_store)
                _apply_active(pf.members if pf else [], optimized)
                n = len(loop.combos)
                worker_store.set_control("last_adaptation", time.strftime("%Y-%m-%d %H:%M:%S"))
                worker_store.set_control("adapted_combos", str(n))
                print(f"[serve] Angepasst: {n} validierte Kombinationen werden aktiv gehandelt.", flush=True)
            except Exception as exc:  # noqa: BLE001 - Hintergrund darf den Prozess nie killen
                print(f"[serve] Anpassung fehlgeschlagen: {exc}", flush=True)
            trigger.wait(args.reeval_hours * 3600)
            trigger.clear()

    threading.Thread(target=adapt_worker, daemon=True).start()

    store.set_control("desired_state", "running")
    store.set_control("command", "")
    print(f"PAPER-DAUERBETRIEB - kein echtes Geld. Aktiver Handel gegen ECHTE Daten, "
          f"Tick alle {args.interval}s, Anpassung alle {args.reeval_hours}h. "
          f"Steuerbar ueber das Dashboard. Strg+C beendet.")

    loop._running = True
    try:
        while loop._running:
            # Ein einzelner Iterationsfehler (z.B. kurzer DB-/Netz-Hickup) darf den
            # Bot NIEMALS beenden - alles in try/except, der Loop laeuft weiter.
            try:
                if store.get_control("desired_state", "running") == "stopped":
                    print("[serve] Stopp-Befehl empfangen - stelle Positionen glatt und beende.", flush=True)
                    for pos in list(broker.get_positions()):
                        # strategy= mitgeben - Positionen werden pro (Strategie, Symbol)
                        # getrackt, ohne das faende close_position() die Position nicht.
                        broker.close_position(pos.symbol, strategy=getattr(pos, "strategy", ""))
                    loop._persist_state(broker.get_account_balance())
                    loop.stop("vom Dashboard gestoppt")
                    break
                cmd = store.pop_command()
                if cmd in ("reeval", "optimize", "adapt"):
                    print(f"[serve] Befehl '{cmd}' -> sofortige Anpassung angestossen.", flush=True)
                    trigger.set()
                elif cmd == "reset_breaker":
                    risk.reset(confirm=True)
                    print("[serve] Circuit Breaker zurueckgesetzt.", flush=True)

                loop.tick()
            except Exception as exc:  # noqa: BLE001 - Loop ueberlebt jeden Iterationsfehler
                print(f"[serve] Iterationsfehler (Loop laeuft weiter): {exc}", flush=True)
            stop_event.wait(args.interval)
            if stop_event.is_set():
                break
    except KeyboardInterrupt:
        loop.stop("Strg+C")
    finally:
        stop_event.set()
        store.set_control("desired_state", "stopped")
    print(f"Endkapital (Paper): {broker.get_account_balance():.2f}")
    return 0


def _load_learned_params(settings) -> dict:
    """Liest learned_params.yaml (falls vorhanden) in ein {(strategy,symbol,tf): params}-Dict."""
    import yaml

    path = settings.path.resolve().parent / "learned_params.yaml"
    if not path.exists():
        return {}
    try:
        nested = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for strategy, by_symbol in nested.items():
        for symbol, by_tf in (by_symbol or {}).items():
            for tf, params in (by_tf or {}).items():
                out[(strategy, symbol, tf)] = params
    return out


def run_optimization(settings, progress=None) -> dict:
    """Selbst-Anpassung: optimiert alle Kombinationen per Walk-Forward und liefert
    NUR die out-of-sample validierten Parameter zurueck, als Dict
    {(strategy, symbol, timeframe): params}. Schreibt zusaetzlich learned_params.yaml.

    Das ist der Kern von 'der Bot passt sich staendig an': wird periodisch auf
    frischen Daten aufgerufen; nur was den Overfitting-Waechter besteht, wird uebernommen.
    """
    import yaml

    from backtest.optimize import optimize_strategy
    from data.loader import MarketSpec
    from strategies import REGISTRY

    loader = settings.data_loader()
    cfg = settings.backtest_config()
    learned_nested: dict[str, dict] = {}
    combo_params: dict[tuple, dict] = {}

    combos = [(s, m, sym, tf) for (m, sym, tf) in settings.universe()
              for s in settings.strategy_names() if REGISTRY[s]().supports(m, tf)]
    for i, (strategy, market, symbol, tf) in enumerate(combos, 1):
        grid = settings.grid_for(strategy)
        if not grid:
            continue
        try:
            ohlcv = loader.load(MarketSpec(market, symbol, tf), bars=settings.evaluation.get("bars", 1500))
            res = optimize_strategy(REGISTRY[strategy], ohlcv, grid,
                                    base_params=settings.params_for(strategy), config=cfg,
                                    market=market, symbol=symbol, timeframe=tf)
        except Exception as exc:  # noqa: BLE001
            if progress:
                progress(i, len(combos), f"{strategy}/{symbol}/{tf}: FEHLER {exc}")
            continue
        if progress:
            progress(i, len(combos), f"{'OK ' if res.accepted else '-- '}{strategy}/{symbol}/{tf}: {res.verdict}")
        if res.accepted:
            learned_nested.setdefault(strategy, {}).setdefault(symbol, {})[tf] = res.chosen_params
            combo_params[(strategy, symbol, tf)] = res.chosen_params

    out_path = settings.path.resolve().parent / "learned_params.yaml"
    out_path.write_text(yaml.safe_dump(learned_nested, allow_unicode=True), encoding="utf-8")
    return combo_params


def cmd_optimize(args) -> int:
    settings = load_settings(args.config)

    def progress(i, total, msg):
        print(f"[{i}/{total}] {msg}", flush=True)

    combo_params = run_optimization(settings, progress=progress)
    print(f"\n{len(combo_params)} Kombination(en) bestanden den Out-of-Sample-Test und werden uebernommen.")
    print(f"Validierte Parameter gespeichert: {settings.path.resolve().parent / 'learned_params.yaml'}")
    print("Der Rest bleibt bewusst bei den Defaults - Ablehnung ist hier der Normalfall.")
    return 0


def cmd_stress(args) -> int:
    """Millionen-Trade-Stresstest fuer eine Strategie/Symbol/Timeframe-Kombination."""
    from backtest.engine import run_backtest
    from backtest.stress import stress_test
    from data.loader import DataLoader, MarketSpec
    from strategies import REGISTRY

    settings = load_settings(args.config)
    market = _market_of(args.symbol)
    ohlcv = settings.data_loader().load(MarketSpec(market, args.symbol, args.timeframe),
                              bars=settings.evaluation.get("bars", 1500))
    bt = run_backtest(REGISTRY[args.strategy](**settings.params_for(args.strategy)), ohlcv,
                      market=market, symbol=args.symbol, timeframe=args.timeframe,
                      config=settings.backtest_config())

    print(f"Basis: {len(bt.r_multiples)} reale Trades aus dem Backtest. "
          f"Simuliere {args.paths:,} Pfade ...")

    def progress(done, total):
        print(f"  {done:,}/{total:,} Pfade", end="\r", flush=True)

    res = stress_test(bt.r_multiples, risk_per_trade=settings.risk_limits().risk_per_trade,
                      total_paths=args.paths, progress=progress)
    print(f"\n\n=== Stresstest {args.strategy} / {args.symbol} / {args.timeframe} ===")
    print(f"Simulierte Trades gesamt : {res.simulated_trades:,}")
    print(f"P(profitabel)            : {res.prob_profitable:.1%}")
    print(f"Return Median            : {res.median_return:+.1%}")
    print(f"Return 5%..95%           : {res.return_p05:+.1%} .. {res.return_p95:+.1%}")
    print(f"Schlechtester Pfad       : {res.worst_return:+.1%}")
    print(f"Max Drawdown (Median)    : {res.median_max_drawdown:.1%}")
    print(f"Max Drawdown (95%)       : {res.drawdown_p95:.1%}")
    print(f"Ruin-Wahrscheinlichkeit  : {res.prob_ruin:.2%}  (Schwelle {res.ruin_threshold:.0%})")
    if not res.reliable:
        print(f"\n! {res.note}")
    print("\nHINWEIS: Das quantifiziert die Risiko-Streuung, es BEWEIST keinen kuenftigen Gewinn.")
    return 0


def cmd_portfolio(args) -> int:
    """Baut aus den gespeicherten Auswertungen ein diversifiziertes Portfolio aus
    validierten, wenig korrelierten Kombinationen."""
    from stats.portfolio import QualityGates, build_portfolio, candidate_labels

    settings = load_settings(args.config)
    store = Store(settings.db_path)
    evals = store.latest_evaluations()
    if evals.empty:
        print("Keine Auswertungen in der DB. Zuerst 'evaluate' ausfuehren.")
        return 1

    rows = {f"{r['strategy']} | {r['symbol']} | {r['timeframe']}": r.to_dict()
            for _, r in evals.iterrows()}
    gates = QualityGates()
    candidates, _ = candidate_labels(rows, gates)
    returns, r_mult = _backtest_returns(rows, candidates, settings)  # nur fuer Kandidaten
    pf = build_portfolio(rows, returns, r_mult, gates=gates,
                         max_positions=args.max_positions, max_correlation=args.max_correlation,
                         initial_capital=settings.initial_capital,
                         timeframe=evals.iloc[0]["timeframe"])

    print("\n" + "=" * 66)
    print("PORTFOLIO" + ("  (VALIDIERT / handelbar)" if pf.validated else "  (ILLUSTRATIV / NICHT handelbar)"))
    print("=" * 66)
    print(pf.note + "\n")
    if not pf.members:
        return 0
    print(f"{'Kombination':<44}{'Gewicht':>8}{'Erw.R':>8}{'Trades':>8}")
    for m in pf.members:
        print(f"{m.label:<44}{m.weight:>7.1%}{m.expectancy_r:>+8.3f}{m.n_trades:>8}")
    mm = pf.metrics
    print(f"\nPortfolio: Sharpe {mm.sharpe:.2f} | Max Drawdown {mm.max_drawdown:.1%} | "
          f"Erwartung {mm.expectancy_r:+.3f}R | Gesamtrendite {mm.total_return:+.1%}")
    print(pf.diversification_note)
    if not pf.validated:
        print("\nHINWEIS: Kein Mitglied hat den Out-of-Sample-Test bestanden. Diversifikation "
              "senkt hier nur die Schwankung - sie macht das Portfolio NICHT profitabel.")
    else:
        store.save_equity_point(float(pf.equity_curve.iloc[-1]), source="portfolio")
    return 0


def cmd_vault(args) -> int:
    """Verschluesselten Schluessel-Tresor anlegen (Geldschutz)."""
    from execution.gate import REQUIRED_ENV
    from risk.vault import SecretsVault, VaultError

    print(RISK_BANNER)
    if not sys.stdin.isatty():
        print("Abbruch: Vault-Erstellung erfordert eine interaktive Sitzung.")
        return 1

    import getpass

    names = REQUIRED_ENV.get(args.market, [])
    print(f"Trage die Keys fuer '{args.market}' ein (leer lassen = ueberspringen).")
    print("WICHTIG: Erstelle den Exchange-Key OHNE Auszahlungsrecht und mit IP-Whitelist!\n")
    secrets = {}
    for name in names:
        val = getpass.getpass(f"  {name}: ")
        if val:
            secrets[name] = val
    if not secrets:
        print("Nichts eingegeben - abgebrochen.")
        return 1
    pw1 = getpass.getpass("\nMaster-Passwort (min. 8 Zeichen): ")
    pw2 = getpass.getpass("Master-Passwort wiederholen    : ")
    if pw1 != pw2:
        print("Passwoerter stimmen nicht ueberein.")
        return 1
    try:
        SecretsVault().save(secrets, pw1)
    except VaultError as exc:
        print(f"Fehler: {exc}")
        return 1
    print("\nVault verschluesselt gespeichert (config/secrets.vault). "
          "Das Master-Passwort wird nirgends gespeichert - merke es dir gut.")
    return 0


def _refresh_portfolio(settings, store) -> None:
    """Baut das Portfolio aus den aktuellen Auswertungen neu und persistiert den
    Equity-Punkt. Wird von serve nach jeder Auffrischung aufgerufen."""
    from stats.portfolio import QualityGates, build_portfolio, candidate_labels

    evals = store.latest_evaluations()
    if evals.empty:
        return
    rows = {f"{r['strategy']} | {r['symbol']} | {r['timeframe']}": r.to_dict()
            for _, r in evals.iterrows()}
    gates = QualityGates()
    candidates, validated = candidate_labels(rows, gates)
    returns, r_mult = _backtest_returns(rows, candidates, settings)
    pf = build_portfolio(rows, returns, r_mult, gates=gates,
                         initial_capital=settings.initial_capital)
    tag = "validiert" if pf.validated else "illustrativ"
    print(f"[serve] Portfolio: {pf.n_members} Mitglied(er) ({tag}), "
          f"MaxDD {pf.metrics.max_drawdown:.1%}, Erwartung {pf.metrics.expectancy_r:+.3f}R", flush=True)
    if pf.validated and len(pf.equity_curve):
        store.save_equity_point(float(pf.equity_curve.iloc[-1]), source="portfolio")
    return pf


def cmd_export_active(args) -> int:
    """Schreibt die aktuell validierten Portfolio-Kombinationen (+ gelernte Parameter)
    nach active_combos.yaml. Genau diese Datei handelt der Cloud-Bot (GitHub Actions) -
    die schwere Auswertung bleibt damit lokal, die CI-Ticks bleiben leicht und gratis."""
    import yaml

    settings = load_settings(args.config)
    store = Store(settings.db_path)
    pf = _refresh_portfolio(settings, store)
    learned = _load_learned_params(settings)
    combos = []
    for m in (pf.members if pf else []):
        combos.append({
            "strategy": m.strategy, "market": _market_of(m.symbol),
            "symbol": m.symbol, "timeframe": m.timeframe,
            "params": learned.get((m.strategy, m.symbol, m.timeframe), {}),
        })
    out_path = Path(__file__).resolve().parent / "active_combos.yaml"
    out_path.write_text(yaml.safe_dump({"combos": combos}, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
    print(f"{len(combos)} validierte Kombination(en) nach {out_path.name} geschrieben.")
    if not combos:
        print("HINWEIS: 0 validierte Kombinationen - der Cloud-Bot handelt dann nichts. "
              "Zuerst 'evaluate' (+ optional 'optimize') laufen lassen.")
    return 0


def cmd_ci_tick(args) -> int:
    """EIN Paper-Tick auf den Kombinationen aus active_combos.yaml - fuer GitHub Actions.
    Leichtgewichtig: keine schwere Auswertung, nur Handel + Zustand persistieren + Report.
    Der Zustand (Kapital/Positionen) lebt in der DB und wird von _make_broker restauriert,
    sodass die 30-min-Ticks nahtlos aufeinander aufbauen (kein PC noetig)."""
    import yaml

    from execution.paper_loop import PaperTradingLoop

    settings = load_settings(args.config)
    store = Store(settings.db_path)
    combos_file = Path(__file__).resolve().parent / "active_combos.yaml"
    if not combos_file.exists():
        print("active_combos.yaml fehlt - lokal 'python cli.py export-active' ausfuehren und committen.")
        return 1
    entries = (yaml.safe_load(combos_file.read_text(encoding="utf-8")) or {}).get("combos", [])
    if not entries:
        print("active_combos.yaml enthaelt 0 Kombinationen - nichts zu handeln.")
    combos = [(e["strategy"], e["market"], e["symbol"], e["timeframe"]) for e in entries]
    combo_params = {(e["strategy"], e["symbol"], e["timeframe"]): (e.get("params") or {}) for e in entries}

    broker = _make_broker(settings, store)
    risk = _make_risk_manager(settings, store, mode=Mode.PAPER)
    loop = PaperTradingLoop(
        broker, risk, store, combos, _make_loop_config(settings, 60), settings.data_loader(),
        {e["strategy"]: settings.params_for(e["strategy"]) for e in entries}, combo_params,
    )
    store.set_control("desired_state", "running")
    loop.tick()
    # WAL in die Haupt-DB schreiben, damit die committete cloud/paper.db vollstaendig ist
    # (die -wal/-shm-Dateien werden nicht mitcommittet).
    store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    store.conn.commit()
    print(f"CI-Tick fertig: Kapital {broker.get_account_balance():.2f}, "
          f"offen {len(broker.get_positions())}, tripped {risk.state.tripped}.")

    try:
        import bot_status_report
        bot_status_report.main()
    except Exception as exc:  # noqa: BLE001
        print("Report-Update uebersprungen:", exc)
    return 0


def _backtest_returns(rows: dict, labels: list[str], settings):
    """Fuehrt Backtests NUR fuer die uebergebenen Kandidaten aus und liefert
    Renditereihen + R-Multiples. Spart die restlichen Backtests."""
    from backtest.engine import run_backtest
    from data.loader import DataLoader, MarketSpec
    from strategies import REGISTRY

    loader, cfg = settings.data_loader(), settings.backtest_config()
    returns, r_mult = {}, {}
    for label in labels:
        row = rows[label]
        try:
            ohlcv = loader.load(MarketSpec(row["market"], row["symbol"], row["timeframe"]),
                                bars=settings.evaluation.get("bars", 1500))
            bt = run_backtest(REGISTRY[row["strategy"]](**settings.params_for(row["strategy"])),
                              ohlcv, market=row["market"], symbol=row["symbol"],
                              timeframe=row["timeframe"], config=cfg)
            returns[label] = bt.equity_curve.pct_change().fillna(0.0)
            r_mult[label] = bt.r_multiples
        except Exception:  # noqa: BLE001
            continue
    return returns, r_mult


def _market_of(symbol: str) -> str:
    if "/" in symbol:
        return "crypto"
    if symbol.endswith("=X"):
        return "forex"
    return "stocks"


def cmd_enable_live(args) -> int:
    """Manueller Bestaetigungsflow. Es gibt keinen nicht-interaktiven Weg hier durch."""
    from execution.gate import CONFIRM_PHRASE, LiveGateError, missing_credentials, new_confirmation_code, unlock_live

    print(RISK_BANNER)
    settings = load_settings(args.config)
    market = args.market

    print("Vorpruefung:")
    print(f"  config.yaml mode        : {settings.configured_mode.value}"
          f"{'  OK' if settings.configured_mode == Mode.LIVE else '  -> muss live sein'}")
    missing = missing_credentials(market)
    print(f"  API-Keys ({market})     : {'OK' if not missing else 'FEHLEN: ' + ', '.join(missing)}")
    try:
        settings.risk_limits()
        risk_ok = True
        print("  Risikolimits            : OK")
    except Exception as exc:  # noqa: BLE001
        risk_ok = False
        print(f"  Risikolimits            : UNGUELTIG ({exc})")

    if not sys.stdin.isatty():
        print("\nAbbruch: Live-Freischaltung erfordert eine interaktive Sitzung.")
        return 1

    print("\nEchtes Geld ist ab jetzt im Spiel. Verluste sind real und moeglicherweise total.")
    phrase = input(f'Tippe woertlich: "{CONFIRM_PHRASE}"\n> ')
    code = new_confirmation_code()
    entered = input(f"Bestaetigungscode {code} abtippen:\n> ")

    try:
        unlock_live(market, config_mode=settings.configured_mode.value, confirm_phrase=phrase,
                    expected_code=code, entered_code=entered, risk_checks_passed=risk_ok)
    except LiveGateError as exc:
        print(f"\nLive-Modus NICHT freigeschaltet: {exc}")
        return 1

    print("\nGate freigeschaltet - aber: Live-Order-Ausfuehrung ist in dieser Version "
          "bewusst nicht implementiert (siehe execution/live.py). Es werden keine "
          "echten Orders gesendet.")
    Store(settings.db_path).log("live_gate_unlocked", mode="live", market=market)
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Trading-Strategie-Dashboard")
    parser.add_argument("--config", default=None, help="Pfad zur config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("evaluate", help="Strategien evaluieren").set_defaults(func=cmd_evaluate)

    p_paper = sub.add_parser("paper", help="Paper-Trading-Loop")
    p_paper.add_argument("--interval", type=int, default=300)
    p_paper.add_argument("--once", action="store_true", help="nur einen Tick ausfuehren")
    p_paper.set_defaults(func=cmd_paper)

    sub.add_parser("dashboard", help="Streamlit-Dashboard").set_defaults(func=cmd_dashboard)

    sub.add_parser("doctor", help="Selbsttest: prueft ob alles bereit ist").set_defaults(func=cmd_doctor)

    p_serve = sub.add_parser("serve", help="Dauerbetrieb: Paper-Loop + periodische Auswertung")
    p_serve.add_argument("--interval", type=int, default=300, help="Paper-Tick in Sekunden")
    p_serve.add_argument("--reeval-hours", type=float, default=6.0, help="Auswertung alle N Stunden")
    p_serve.add_argument("--force", action="store_true",
                         help="Startet auch, wenn bereits ein Bot laeuft (normalerweise blockiert)")
    p_serve.set_defaults(func=cmd_serve)

    sub.add_parser("optimize", help="Selbst-Optimierung mit Overfitting-Waechter").set_defaults(func=cmd_optimize)

    p_stress = sub.add_parser("stress", help="Millionen-Trade-Stresstest")
    p_stress.add_argument("--strategy", required=True)
    p_stress.add_argument("--symbol", required=True)
    p_stress.add_argument("--timeframe", default="1h")
    p_stress.add_argument("--paths", type=int, default=1_000_000)
    p_stress.set_defaults(func=cmd_stress)

    p_pf = sub.add_parser("portfolio", help="Diversifiziertes Portfolio aus validierten Kombinationen")
    p_pf.add_argument("--max-positions", type=int, default=6)
    p_pf.add_argument("--max-correlation", type=float, default=0.6)
    p_pf.set_defaults(func=cmd_portfolio)

    p_vault = sub.add_parser("vault", help="Verschluesselten Schluessel-Tresor anlegen")
    p_vault.add_argument("--market", choices=["crypto", "stocks", "forex"], required=True)
    p_vault.set_defaults(func=cmd_vault)

    p_live = sub.add_parser("enable-live", help="Live-Modus manuell freischalten")
    p_live.add_argument("--market", choices=["crypto", "stocks", "forex"], required=True)
    p_live.set_defaults(func=cmd_enable_live)

    sub.add_parser("export-active", help="Validierte Kombinationen -> active_combos.yaml (fuer Cloud-Bot)"
                   ).set_defaults(func=cmd_export_active)
    sub.add_parser("ci-tick", help="Ein Paper-Tick auf active_combos.yaml (fuer GitHub Actions)"
                   ).set_defaults(func=cmd_ci_tick)

    args = parser.parse_args(argv)
    if args.config is None:
        from config.settings import DEFAULT_CONFIG_PATH
        args.config = DEFAULT_CONFIG_PATH
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 130
    except Exception as exc:  # noqa: BLE001 - idiotensicher: nie ein roher Stacktrace
        print(f"\n[FEHLER] {exc}")
        print("Tipp: Fuehre 'python cli.py doctor' aus - der Selbsttest sagt, was zu tun ist.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
