"""Portfolio-Konstruktion: mehrere validierte, wenig korrelierte Strategie/Markt-
Kombinationen zu einem gemeinsamen Portfolio buendeln und das Risiko verteilen.

EHRLICHE GRUNDREGEL (wird im Ergebnis belegt):
  * Diversifikation senkt RISIKO und SCHWANKUNG (Drawdown, Volatilitaet) - das ist
    ihr echter, mathematisch belegbarer Nutzen.
  * Diversifikation erzeugt KEINEN neuen Vorteil. Die Portfolio-Erwartung ist rund
    der gewichtete Durchschnitt der Einzel-Erwartungen. Aus negativen Vorteilen wird
    also kein positiver - nur ein geglaetteterer.

Deshalb gibt es Qualitaets-Huerden: nur Kombinationen mit echtem, out-of-sample
validiertem Vorteil kommen ins handelbare Portfolio. Bestehen zu wenige die Huerden,
wird das offen gesagt - ein leeres Portfolio ist ein ehrliches Ergebnis, kein Fehler.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from stats.metrics import Metrics, compute_metrics, max_drawdown


@dataclass
class QualityGates:
    min_trades: int = 30
    min_expectancy_r: float = 0.0
    min_wf_efficiency: float = 0.5
    allow_synthetic: bool = False
    allowed_lights: tuple[str, ...] = ("green", "yellow")


@dataclass
class PortfolioMember:
    label: str
    strategy: str
    symbol: str
    timeframe: str
    weight: float
    expectancy_r: float
    n_trades: int
    max_drawdown: float


@dataclass
class Portfolio:
    members: list[PortfolioMember] = field(default_factory=list)
    equity_curve: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    metrics: Metrics = field(default_factory=Metrics)
    correlation: pd.DataFrame = field(default_factory=pd.DataFrame)
    validated: bool = False
    diversification_note: str = ""
    note: str = ""
    target_vol: float | None = None    # gewuenschte annualisierte Volatilitaet (falls gesetzt)
    realized_vol: float = 0.0          # tatsaechliche annual. Vola VOR dem Targeting
    leverage: float = 1.0             # Skalierung der Gesamt-Exposure fuers Vol-Target

    @property
    def n_members(self) -> int:
        return len(self.members)


def annualized_vol(returns: pd.Series, timeframe: str) -> float:
    """Annualisierte Volatilitaet einer Renditereihe (Std x sqrt(Perioden/Jahr))."""
    from stats.metrics import PERIODS_PER_YEAR
    r = returns.dropna()
    if len(r) < 2:
        return 0.0
    ppy = PERIODS_PER_YEAR.get(timeframe, 8_760)
    return float(r.std(ddof=0) * np.sqrt(ppy))


def passes_gates(row: dict, gates: QualityGates) -> bool:
    if not gates.allow_synthetic and row.get("data_source") == "synthetic":
        return False
    if row.get("n_trades", 0) < gates.min_trades:
        return False
    if row.get("expectancy_r", -1) <= gates.min_expectancy_r:
        return False
    if row.get("wf_efficiency", 0) < gates.min_wf_efficiency:
        return False
    if row.get("traffic_light") not in gates.allowed_lights:
        return False
    return True


def candidate_labels(rows_by_label: dict[str, dict], gates: QualityGates,
                     top_n_if_empty: int = 12) -> tuple[list[str], bool]:
    """Bestimmt OHNE Backtest, welche Kombinationen ueberhaupt in Frage kommen.
    So muessen anschliessend nur fuer diese wenigen die Backtests laufen (Tempo)."""
    qualified = [lbl for lbl, row in rows_by_label.items() if passes_gates(row, gates)]
    if qualified:
        qualified.sort(key=lambda l: rows_by_label[l].get("score", 0), reverse=True)
        return qualified, True
    fallback = sorted(rows_by_label, key=lambda l: rows_by_label[l].get("score", 0), reverse=True)
    return fallback[:top_n_if_empty], False


def correlation_matrix(returns: dict[str, pd.Series]) -> pd.DataFrame:
    if not returns:
        return pd.DataFrame()
    frame = pd.DataFrame(returns).fillna(0.0)
    return frame.corr().fillna(0.0)


def select_diversified(
    labels_by_score: list[str], corr: pd.DataFrame,
    *, max_positions: int = 6, max_correlation: float = 0.6,
) -> list[str]:
    """Greedy: nimm den bestbewerteten Kandidaten, fuege nur hinzu, wer mit ALLEN
    bereits Gewaehlten unter der Korrelationsschwelle liegt."""
    selected: list[str] = []
    for label in labels_by_score:
        if label not in corr.columns:
            continue
        if all(abs(corr.loc[label, s]) <= max_correlation for s in selected):
            selected.append(label)
        if len(selected) >= max_positions:
            break
    return selected


def inverse_vol_weights(returns: dict[str, pd.Series], selected: list[str]) -> dict[str, float]:
    """Risikoparitaet-light: wer staerker schwankt, bekommt weniger Gewicht."""
    vols = {}
    for label in selected:
        v = returns[label].std(ddof=0)
        vols[label] = v if v and np.isfinite(v) and v > 0 else np.nan
    inv = {k: (1.0 / v if np.isfinite(v) else 0.0) for k, v in vols.items()}
    total = sum(inv.values())
    if total <= 0:
        n = len(selected)
        return {k: 1.0 / n for k in selected} if n else {}
    return {k: v / total for k, v in inv.items()}


def combined_returns(returns: dict[str, pd.Series], weights: dict[str, float]) -> pd.Series:
    if not weights:
        return pd.Series(dtype=float)
    frame = pd.DataFrame({k: returns[k] for k in weights}).fillna(0.0)
    return sum(frame[k] * w for k, w in weights.items())


def combine_equity(
    returns: dict[str, pd.Series], weights: dict[str, float], initial_capital: float = 10_000.0,
    leverage: float = 1.0,
) -> pd.Series:
    combined = combined_returns(returns, weights)
    if combined.empty:
        return pd.Series(dtype=float)
    return initial_capital * (1.0 + combined * leverage).cumprod()


def build_portfolio(
    rows_by_label: dict[str, dict],
    returns_by_label: dict[str, pd.Series],
    r_multiples_by_label: dict[str, np.ndarray],
    *,
    gates: QualityGates | None = None,
    max_positions: int = 6,
    max_correlation: float = 0.6,
    initial_capital: float = 10_000.0,
    timeframe: str = "1h",
    illustrative_if_empty: bool = True,
    target_vol: float | None = None,
) -> Portfolio:
    gates = gates or QualityGates()
    qualified = [lbl for lbl, row in rows_by_label.items() if passes_gates(row, gates)]
    validated = bool(qualified)

    if not qualified and illustrative_if_empty:
        # Kein validierter Vorteil -> nur zur VERANSCHAULICHUNG die bestbewerteten
        # nehmen, klar als nicht-handelbar markiert.
        candidates = sorted(rows_by_label, key=lambda l: rows_by_label[l].get("score", 0), reverse=True)
        note = ("KEINE Kombination hat die Qualitaets-Huerden bestanden (validierter, "
                "out-of-sample positiver Vorteil). Das folgende Portfolio ist ILLUSTRATIV "
                "und NICHT handelbar - es zeigt nur, dass Diversifikation den Drawdown glaettet, "
                "ohne aus Verlierern Gewinner zu machen.")
    else:
        candidates = sorted(qualified, key=lambda l: rows_by_label[l].get("score", 0), reverse=True)
        note = (f"{len(qualified)} Kombination(en) haben die Qualitaets-Huerden bestanden."
                if validated else "Keine Kandidaten.")

    if not candidates:
        return Portfolio(note="Keine Daten fuer ein Portfolio vorhanden.")

    corr = correlation_matrix({l: returns_by_label[l] for l in candidates if l in returns_by_label})
    selected = select_diversified(candidates, corr, max_positions=max_positions,
                                  max_correlation=max_correlation)
    if not selected:
        selected = candidates[:1]
    weights = inverse_vol_weights(returns_by_label, selected)

    # Volatility-Targeting (optional): Gesamt-Exposure so skalieren, dass die
    # Portfolio-Volatilitaet ~ target_vol trifft. Reduziert bei ruhigen Phasen NICHT,
    # deckelt aber die Hebelung (0.2..1.5), damit es im Paper-Kontext ehrlich bleibt.
    realized_vol = annualized_vol(combined_returns(returns_by_label, weights), timeframe)
    leverage = 1.0
    if target_vol and realized_vol > 0:
        leverage = float(np.clip(target_vol / realized_vol, 0.2, 1.5))

    equity = combine_equity(returns_by_label, weights, initial_capital, leverage=leverage)
    metrics = compute_metrics(
        _portfolio_r_multiples(r_multiples_by_label, weights), equity, timeframe
    )

    members = [
        PortfolioMember(
            label=lbl, strategy=rows_by_label[lbl].get("strategy", ""),
            symbol=rows_by_label[lbl].get("symbol", ""), timeframe=rows_by_label[lbl].get("timeframe", ""),
            weight=weights[lbl], expectancy_r=rows_by_label[lbl].get("expectancy_r", 0.0),
            n_trades=rows_by_label[lbl].get("n_trades", 0),
            max_drawdown=rows_by_label[lbl].get("max_drawdown", 0.0),
        )
        for lbl in selected
    ]

    # Diversifikations-Beleg: Portfolio-Drawdown vs. Durchschnitt der Einzel-Drawdowns
    single_dds = [rows_by_label[lbl].get("max_drawdown", 0.0) for lbl in selected]
    avg_single_dd = float(np.mean(single_dds)) if single_dds else 0.0
    port_dd = metrics.max_drawdown
    div_note = (
        f"Portfolio-Drawdown {port_dd:.1%} vs. Durchschnitt der Einzel-Drawdowns "
        f"{avg_single_dd:.1%} - Diversifikation glaettet das Risiko."
    )
    if target_vol:
        div_note += (f" Vol-Target {target_vol:.0%}: gemessene Vola {realized_vol:.0%} "
                     f"-> Hebel {leverage:.2f}.")

    return Portfolio(
        members=members, equity_curve=equity, metrics=metrics, correlation=corr.loc[selected, selected],
        validated=validated, diversification_note=div_note, note=note,
        target_vol=target_vol, realized_vol=realized_vol, leverage=leverage,
    )


def _portfolio_r_multiples(r_by_label: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
    """Naeherung der Portfolio-Trade-Verteilung: gewichtete Zusammenfuehrung der
    R-Multiples aller Mitglieder (fuer Kennzahlen/Erwartungswert)."""
    parts = []
    for lbl, w in weights.items():
        arr = np.asarray(r_by_label.get(lbl, []), dtype=float)
        if arr.size:
            parts.append(arr * w * len(weights))  # auf Einzel-Skala normiert
    return np.concatenate(parts) if parts else np.array([])
