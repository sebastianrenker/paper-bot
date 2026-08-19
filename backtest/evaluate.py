"""Orchestrierung: fuer jede Strategie/Markt/Timeframe-Kombination
Rolling Backtest -> Walk-Forward -> Monte-Carlo -> Regime -> Score -> DB.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import pandas as pd

from backtest.engine import BacktestConfig, BacktestResult, run_backtest
from backtest.montecarlo import MonteCarloResult, monte_carlo
from backtest.walkforward import WalkForwardResult, walk_forward
from config.settings import Settings
from core.store import Store
from data.loader import DataLoader, MarketSpec
from stats.metrics import Metrics, compute_metrics
from stats.regime import Regime, detect_regime
from stats.score import ScoreBreakdown, compute_score
from strategies import REGISTRY

log = logging.getLogger(__name__)


@dataclass
class Evaluation:
    strategy: str
    market: str
    symbol: str
    timeframe: str
    data_source: str
    backtest: BacktestResult
    metrics: Metrics
    rolling: dict[int, Metrics] = field(default_factory=dict)
    walk_forward: WalkForwardResult | None = None
    monte_carlo: MonteCarloResult | None = None
    regime: Regime | None = None
    score: ScoreBreakdown | None = None

    def to_row(self) -> dict:
        mc, wf, s = self.monte_carlo, self.walk_forward, self.score
        return {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "strategy": self.strategy, "market": self.market, "symbol": self.symbol,
            "timeframe": self.timeframe, "data_source": self.data_source,
            "score": s.total if s else 0.0,
            "traffic_light": s.traffic_light if s else "red",
            "n_trades": self.metrics.n_trades,
            "win_rate": self.metrics.win_rate,
            "profit_factor": min(self.metrics.profit_factor, 1e6),
            "expectancy_r": self.metrics.expectancy_r,
            "sharpe": self.metrics.sharpe,
            "sortino": self.metrics.sortino,
            "max_drawdown": self.metrics.max_drawdown,
            "mc_prob_profitable": mc.prob_profitable if mc else 0.0,
            "mc_return_ci_low": mc.return_ci_low if mc else 0.0,
            "mc_return_ci_high": mc.return_ci_high if mc else 0.0,
            "mc_drawdown_ci_high": mc.drawdown_ci_high if mc else 0.0,
            "wf_efficiency": wf.efficiency if wf else 0.0,
            "regime": self.regime.label if self.regime else "",
            "payload": {
                "score": s.as_dict() if s else {},
                "walk_forward_oos": wf.oos_metrics.as_dict() if wf else {},
                "monte_carlo": mc.as_dict() if mc else {},
                "rolling": {str(k): v.as_dict() for k, v in self.rolling.items()},
                "warnings": s.warnings if s else [],
            },
        }


def evaluate_combo(
    strategy_name: str, ohlcv: pd.DataFrame, settings: Settings,
    *, market: str, symbol: str, timeframe: str, data_source: str = "unknown",
) -> Evaluation:
    cls = REGISTRY[strategy_name]
    params = settings.params_for(strategy_name)
    cfg: BacktestConfig = settings.backtest_config()
    ev_cfg = settings.evaluation

    bt = run_backtest(cls(**params), ohlcv, market=market, symbol=symbol,
                      timeframe=timeframe, config=cfg, data_source=data_source)
    metrics = compute_metrics(bt.r_multiples, bt.equity_curve, timeframe)

    # Rolling Windows: dieselbe Strategie auf den letzten N Tagen
    rolling: dict[int, Metrics] = {}
    for days in ev_cfg.get("rolling_windows_days", [90, 180, 365]):
        cutoff = ohlcv.index.max() - pd.Timedelta(days=days)
        window = ohlcv[ohlcv.index >= cutoff]
        if len(window) < 100:
            continue
        res = run_backtest(cls(**params), window, market=market, symbol=symbol,
                           timeframe=timeframe, config=cfg)
        rolling[days] = compute_metrics(res.r_multiples, res.equity_curve, timeframe)

    wf_cfg = ev_cfg.get("walk_forward", {})
    wf = walk_forward(
        cls, ohlcv,
        train_bars=wf_cfg.get("train_bars", 500),
        test_bars=wf_cfg.get("test_bars", 125),
        grid=settings.grid_for(strategy_name),
        base_params=params, config=cfg,
        market=market, symbol=symbol, timeframe=timeframe,
    )

    mc_cfg = ev_cfg.get("monte_carlo", {})
    mc = monte_carlo(
        wf.oos_r_multiples if wf.oos_r_multiples.size else bt.r_multiples,
        risk_per_trade=cfg.risk_per_trade,
        n_runs=mc_cfg.get("runs", 1000),
        ruin_threshold=mc_cfg.get("ruin_threshold", 0.5),
    )

    regime = detect_regime(ohlcv)
    score = compute_score(wf, mc, regime, cls.category)
    if data_source == "synthetic":
        score.warnings.append(
            "SYNTHETISCHE Daten - dieses Ergebnis hat keinerlei Aussagekraft fuer echte Maerkte."
        )

    return Evaluation(
        strategy=strategy_name, market=market, symbol=symbol, timeframe=timeframe,
        data_source=data_source, backtest=bt, metrics=metrics, rolling=rolling,
        walk_forward=wf, monte_carlo=mc, regime=regime, score=score,
    )


def evaluate_universe(
    settings: Settings, store: Store | None = None, loader: DataLoader | None = None,
    *, persist: bool = True, progress=None,
) -> list[Evaluation]:
    loader = loader or settings.data_loader()
    store = store or (Store(settings.db_path) if persist else None)
    bars = settings.evaluation.get("bars", 1500)

    combos = [
        (s, m, sym, tf)
        for (m, sym, tf) in settings.universe()
        for s in settings.strategy_names()
        if REGISTRY[s]().supports(m, tf)
    ]
    results: list[Evaluation] = []
    for i, (strategy_name, market, symbol, timeframe) in enumerate(combos, 1):
        if progress:
            progress(i, len(combos), f"{strategy_name} / {symbol} / {timeframe}")
        spec = MarketSpec(market, symbol, timeframe)
        try:
            ohlcv = loader.load(spec, bars=bars)
            if len(ohlcv) < 300:
                log.warning("Zu wenige Bars fuer %s - uebersprungen.", spec.slug)
                continue
            ev = evaluate_combo(strategy_name, ohlcv, settings, market=market, symbol=symbol,
                                timeframe=timeframe, data_source=loader.source_of(spec))
        except Exception as exc:  # noqa: BLE001 - eine kaputte Kombination darf den Lauf nicht killen
            log.exception("Evaluation fehlgeschlagen fuer %s/%s: %s", strategy_name, spec.slug, exc)
            continue

        results.append(ev)
        if store is not None:
            store.save_evaluation(ev.to_row())
            frame = ev.backtest.trades_frame()
            if not frame.empty:
                frame["market"] = market
                store.save_trades(frame.to_dict("records"), source="backtest")
    return sorted(results, key=lambda e: e.score.total if e.score else 0.0, reverse=True)
