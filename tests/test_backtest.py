"""Tests fuer Backtest-Engine, Walk-Forward, Monte-Carlo und Kennzahlen."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.engine import BacktestConfig, run_backtest
from backtest.montecarlo import monte_carlo
from backtest.walkforward import param_grid, walk_forward
from stats.metrics import MIN_TRADES_FOR_SIGNIFICANCE, compute_metrics, max_drawdown
from strategies import build
from strategies.base import Strategy


class AlwaysLong(Strategy):
    name = "always_long"
    category = "trend"

    @staticmethod
    def default_params() -> dict:
        return {}

    def compute(self, ohlcv):
        out = self.empty_frame(ohlcv.index)
        out["direction"] = 1
        out["confidence"] = 1.0
        return out


def test_backtest_produces_trades_and_equity(ohlcv):
    res = run_backtest(build("ema_crossover"), ohlcv, symbol="BTC/USDT", timeframe="1h")
    assert len(res.equity_curve) == len(ohlcv) - 1
    assert res.equity_curve.notna().all()
    for t in res.trades:
        if not t.is_open:
            assert t.exit_time >= t.entry_time


def test_stop_loss_caps_loss_at_about_one_r():
    """Bei ausgeloestem Stop darf der Verlust nicht wesentlich ueber -1R liegen."""
    n = 300
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    close = pd.Series(np.linspace(100, 60, n), index=idx)  # stetig fallend
    df = pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5,
                       "close": close, "volume": 1000.0}, index=idx)
    res = run_backtest(AlwaysLong(), df, symbol="X", timeframe="1h")
    closed = [t for t in res.trades if not t.is_open]
    assert closed, "Es sollten Trades ausgestoppt werden"
    assert min(t.r_multiple for t in closed) > -1.6  # Slippage/Gap-Puffer


def test_no_lookahead_signal_executed_next_bar(ohlcv):
    """Entry-Preis muss der OPEN des Folgebars sein (plus Slippage), nie der Close des Signalbars."""
    cfg = BacktestConfig(fee_rate=0.0, slippage_rate=0.0)
    res = run_backtest(AlwaysLong(), ohlcv, symbol="X", timeframe="1h", config=cfg)
    first = res.trades[0]
    i = ohlcv.index.get_loc(pd.Timestamp(first.entry_time))
    assert first.entry_price == pytest.approx(float(ohlcv["open"].iloc[i]))


def test_fees_and_slippage_reduce_result(ohlcv):
    free = run_backtest(build("ema_crossover"), ohlcv, config=BacktestConfig(fee_rate=0.0, slippage_rate=0.0))
    costly = run_backtest(build("ema_crossover"), ohlcv, config=BacktestConfig(fee_rate=0.002, slippage_rate=0.002))
    assert costly.equity_curve.iloc[-1] <= free.equity_curve.iloc[-1]


def test_metrics_flag_small_samples():
    m = compute_metrics([0.5, -1.0, 2.0], pd.Series([100, 101, 99, 105]), "1h")
    assert m.n_trades == 3
    assert not m.statistically_significant
    assert str(MIN_TRADES_FOR_SIGNIFICANCE) in m.warning


def test_metrics_basic_math():
    m = compute_metrics([2.0, 2.0, -1.0, -1.0], pd.Series([100, 110, 105, 120]), "1d")
    assert m.win_rate == pytest.approx(0.5)
    assert m.profit_factor == pytest.approx(2.0)
    assert m.avg_r == pytest.approx(0.5)


def test_max_drawdown():
    eq = pd.Series([100, 120, 60, 90])
    assert max_drawdown(eq) == pytest.approx(0.5)


def test_walk_forward_only_reports_out_of_sample(ohlcv):
    from strategies.ema_crossover import EmaCrossover

    wf = walk_forward(EmaCrossover, ohlcv, train_bars=400, test_bars=100,
                      grid={"fast": [9, 12]}, base_params={"trend_filter": 0})
    assert wf.windows, "Es muessen Fenster erzeugt werden"
    for w in wf.windows:
        assert w.train_end < w.test_start   # keine Ueberlappung
        assert w.chosen_params["fast"] in (9, 12)


def test_param_grid():
    grid = param_grid({"a": [1, 2], "b": [3]})
    assert grid == [{"a": 1, "b": 3}, {"a": 2, "b": 3}]
    assert param_grid({}) == [{}]


def test_monte_carlo_ranges_and_reliability():
    rng = np.random.default_rng(1)
    r = rng.normal(0.1, 1.0, 200)
    mc = monte_carlo(r, n_runs=500, risk_per_trade=0.01)
    assert mc.n_trades == 200
    assert mc.reliable
    assert 0.0 <= mc.prob_profitable <= 1.0
    assert mc.return_ci_low <= mc.median_return <= mc.return_ci_high
    assert 0.0 <= mc.median_max_drawdown <= mc.drawdown_ci_high <= 1.0


def test_monte_carlo_marks_small_sample_unreliable():
    mc = monte_carlo([1.0, -1.0, 0.5], n_runs=200)
    assert not mc.reliable
    assert "Trades" in mc.note


def test_monte_carlo_empty():
    mc = monte_carlo([])
    assert mc.n_trades == 0 and not mc.reliable


def test_losing_strategy_has_low_probability():
    mc = monte_carlo([-1.0] * 50 + [0.5] * 50, n_runs=500)
    assert mc.prob_profitable < 0.2
