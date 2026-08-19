"""Walk-Forward-Validierung.

Die Daten werden in aufeinanderfolgende Train/Test-Fenster geteilt. Optimiert wird
(optional) nur auf dem Trainingsfenster; berichtet wird ausschliesslich die
Out-of-Sample-Performance der Testfenster. Nur diese OOS-Zahlen gehen in den
"Funktioniert gerade"-Score ein - In-Sample-Ergebnisse sind bekanntlich zu optimistisch.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

from backtest.engine import BacktestConfig, BacktestResult, run_backtest
from stats.metrics import Metrics, compute_metrics
from strategies.base import Strategy


@dataclass
class WalkForwardWindow:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    chosen_params: dict[str, Any]
    train_metrics: Metrics
    test_metrics: Metrics


@dataclass
class WalkForwardResult:
    strategy: str
    symbol: str
    timeframe: str
    windows: list[WalkForwardWindow] = field(default_factory=list)
    oos_r_multiples: np.ndarray = field(default_factory=lambda: np.array([]))
    oos_equity: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    oos_metrics: Metrics = field(default_factory=Metrics)

    @property
    def efficiency(self) -> float:
        """OOS-Performance / IS-Performance. < 0.5 ist ein starker Overfitting-Hinweis."""
        is_r = [w.train_metrics.avg_r for w in self.windows if w.train_metrics.n_trades > 0]
        oos_r = [w.test_metrics.avg_r for w in self.windows if w.test_metrics.n_trades > 0]
        if not is_r or not oos_r:
            return 0.0
        is_mean = float(np.mean(is_r))
        if abs(is_mean) < 1e-9:
            return 0.0
        return float(np.mean(oos_r) / is_mean)


def param_grid(grid: dict[str, Iterable]) -> list[dict]:
    if not grid:
        return [{}]
    keys = list(grid)
    return [dict(zip(keys, combo)) for combo in itertools.product(*(grid[k] for k in keys))]


def walk_forward(
    strategy_cls: type[Strategy],
    ohlcv: pd.DataFrame,
    *,
    train_bars: int = 500,
    test_bars: int = 125,
    step: int | None = None,
    grid: dict[str, Iterable] | None = None,
    base_params: dict | None = None,
    config: BacktestConfig | None = None,
    market: str = "crypto",
    symbol: str = "UNKNOWN",
    timeframe: str = "1h",
) -> WalkForwardResult:
    cfg = config or BacktestConfig()
    step = step or test_bars
    candidates = [dict(base_params or {}, **p) for p in param_grid(grid or {})]
    result = WalkForwardResult(strategy=strategy_cls.name, symbol=symbol, timeframe=timeframe)

    oos_r: list[float] = []
    oos_equity_parts: list[pd.Series] = []
    start = 0
    while start + train_bars + test_bars <= len(ohlcv):
        train = ohlcv.iloc[start:start + train_bars]
        test = ohlcv.iloc[start + train_bars:start + train_bars + test_bars]

        best_params, best_score, best_train_metrics = candidates[0], -np.inf, Metrics()
        for params in candidates:
            res = _run(strategy_cls, params, train, cfg, market, symbol, timeframe)
            m = compute_metrics(res.r_multiples, res.equity_curve, timeframe)
            # Selektionskriterium: Erwartungswert je Trade, gedaempft durch Drawdown
            score = m.expectancy_r * np.sqrt(max(m.n_trades, 0)) - m.max_drawdown
            if score > best_score:
                best_params, best_score, best_train_metrics = params, score, m

        # Warmup: Test bekommt den Trainings-Tail als Kontext, gewertet wird nur der Testteil
        context = ohlcv.iloc[max(start + train_bars - 250, 0):start + train_bars + test_bars]
        res_test = _run(strategy_cls, best_params, context, cfg, market, symbol, timeframe)
        test_trades = [t for t in res_test.trades if t.entry_time >= test.index[0].to_pydatetime()]
        test_r = np.array([t.r_multiple for t in test_trades if not t.is_open])
        test_equity = res_test.equity_curve.loc[res_test.equity_curve.index >= test.index[0]]
        test_metrics = compute_metrics(test_r, test_equity, timeframe)

        result.windows.append(
            WalkForwardWindow(
                train_start=train.index[0], train_end=train.index[-1],
                test_start=test.index[0], test_end=test.index[-1],
                chosen_params=best_params, train_metrics=best_train_metrics,
                test_metrics=test_metrics,
            )
        )
        oos_r.extend(test_r.tolist())
        if len(test_equity):
            oos_equity_parts.append(test_equity)
        start += step

    result.oos_r_multiples = np.array(oos_r)
    if oos_equity_parts:
        # Fenster-Equity zu einer durchgehenden Kurve verketten (Renditen multiplizieren)
        stitched, base = [], cfg.initial_capital
        for part in oos_equity_parts:
            rel = part / part.iloc[0]
            stitched.append(rel * base)
            base = float(rel.iloc[-1] * base)
        result.oos_equity = pd.concat(stitched)
        result.oos_equity = result.oos_equity[~result.oos_equity.index.duplicated(keep="last")].sort_index()
    result.oos_metrics = compute_metrics(result.oos_r_multiples, result.oos_equity, timeframe)
    return result


def _run(strategy_cls, params, data, cfg, market, symbol, timeframe) -> BacktestResult:
    return run_backtest(
        strategy_cls(**params), data, market=market, symbol=symbol,
        timeframe=timeframe, config=cfg,
    )
