"""Unified correction plan — shared across CLI and GUI.

A :class:`CorrectionPlan` bundles analysis, strategy decisions, global delta,
and btime method into a single object. It provides:

* Controls to set strategies and delta (plan authoring)
* Lazy cached preview computation (plan review)
* WriteJob generation (plan execution)
* JSON serialization (plan persistence)
"""

import json
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from analysis import AnalysisResult
from options import (
    STRATEGY_GPS, STRATEGY_MANUAL, STRATEGY_SKIP,
    VALID_STRATEGIES, DEFAULT_STRATEGY,
    BTIME_AUTO,
)
from preview import SetDecision, compute_preview, PreviewResult
from writer import WriteJob


@dataclass
class CorrectionPlan:
    """A complete correction plan — analysis, decisions, settings, preview.

    Parameters
    ----------
    analysis : AnalysisResult
        The file analysis to build a plan for.
    decisions : dict[str, SetDecision]
        Per-set strategy decisions.  If empty, populated with defaults.
    manual_delta : timedelta | None
        Global manual delta applied to all ``manual``-strategy sets.
    btime_method : str
        Btime fix method passed to Writer.
    """

    analysis: AnalysisResult
    decisions: dict[str, SetDecision] = field(default_factory=dict)
    manual_delta: timedelta | None = None
    btime_method: str = BTIME_AUTO

    _preview: list[PreviewResult] | None = field(default=None, repr=False)
    _dirty: bool = field(default=True, repr=False)

    def __post_init__(self):
        if not self.decisions:
            self._init_decisions()

    def _init_decisions(self):
        self.decisions = {
            fs.id: SetDecision(strategy=DEFAULT_STRATEGY,
                               manual_delta=self.manual_delta)
            for fs in self.analysis.sets
        }

    # ── Strategy controls ───────────────────────────────────────

    def set_strategy(self, set_id: str, strategy: str,
                     *, manual_delta: timedelta | None = None):
        if strategy not in VALID_STRATEGIES:
            raise ValueError(f"Invalid strategy: {strategy}")
        if strategy == STRATEGY_MANUAL:
            self.decisions[set_id] = SetDecision(
                strategy=strategy,
                manual_delta=manual_delta
                if manual_delta is not None
                else self.manual_delta,
            )
        else:
            self.decisions[set_id] = SetDecision(strategy=strategy)
        self._mark_dirty()

    def set_all_strategies(self, strategy: str):
        for fs in self.analysis.sets:
            self.set_strategy(fs.id, strategy)

    def set_all_manual(self):
        self.set_all_strategies(STRATEGY_MANUAL)

    def set_all_gps(self):
        self.set_all_strategies(STRATEGY_GPS)

    def set_all_skip(self):
        self.set_all_strategies(STRATEGY_SKIP)

    # ── Global controls ─────────────────────────────────────────

    def set_manual_delta(self, delta: timedelta | None):
        self.manual_delta = delta
        for set_id, dec in self.decisions.items():
            if dec.strategy == STRATEGY_MANUAL:
                self.decisions[set_id] = SetDecision(
                    strategy=STRATEGY_MANUAL, manual_delta=delta)
        self._mark_dirty()

    def set_btime_method(self, method: str):
        self.btime_method = method

    # ── Preview (lazy, cached, auto-invalidated) ────────────────

    @property
    def preview(self) -> list[PreviewResult]:
        if self._preview is None or self._dirty:
            self._preview = compute_preview(
                self.analysis, self.decisions, self.manual_delta)
            self._dirty = False
        return self._preview

    def _mark_dirty(self):
        self._dirty = True

    def invalidate_preview(self):
        self._mark_dirty()

    # ── Output ──────────────────────────────────────────────────

    def to_jobs(self) -> list[WriteJob]:
        return [
            WriteJob(
                path=fp.path,
                target_embedded=fp.target_embedded,
                target_mtime=fp.target_mtime,
            )
            for pr in self.preview
            for fp in pr.file_results
        ]

    def summary(self) -> str:
        total = sum(len(pr.file_results) for pr in self.preview)
        by_strategy: dict[str, int] = {}
        for pr in self.preview:
            by_strategy[pr.strategy] = (
                by_strategy.get(pr.strategy, 0) + len(pr.file_results)
            )
        parts = [f"{total} files"]
        for strategy in sorted(by_strategy):
            parts.append(f"{by_strategy[strategy]}x {strategy}")
        if self.manual_delta is not None:
            parts.append(f"delta={_fmt_delta(self.manual_delta)}")
        parts.append(f"btime={self.btime_method}")
        return ", ".join(parts)

    def get_decisions(self) -> dict[str, dict]:
        return {
            sid: {'strategy': d.strategy}
            for sid, d in self.decisions.items()
        }

    # ── Serialization ───────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            'btime_method': self.btime_method,
            'manual_delta_seconds': (
                self.manual_delta.total_seconds()
                if self.manual_delta else None
            ),
            'decisions': {
                set_id: {
                    'strategy': dec.strategy,
                    'manual_delta_seconds': (
                        dec.manual_delta.total_seconds()
                        if dec.manual_delta else None
                    ),
                }
                for set_id, dec in self.decisions.items()
            },
        }

    def save_state(self, path: Path):
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load_state(cls, path: Path,
                   analysis: AnalysisResult) -> 'CorrectionPlan':
        data = json.loads(path.read_text())
        decisions = {}
        for set_id, d in data.get('decisions', {}).items():
            manual_delta = None
            secs = d.get('manual_delta_seconds')
            if secs is not None:
                manual_delta = timedelta(seconds=secs)
            decisions[set_id] = SetDecision(
                strategy=d['strategy'], manual_delta=manual_delta)
        manual_delta = None
        secs = data.get('manual_delta_seconds')
        if secs is not None:
            manual_delta = timedelta(seconds=secs)
        plan = cls(
            analysis=analysis,
            decisions=decisions,
            manual_delta=manual_delta,
            btime_method=data.get('btime_method', BTIME_AUTO),
        )
        for fs in analysis.sets:
            if fs.id not in plan.decisions:
                plan.decisions[fs.id] = SetDecision(
                    strategy=DEFAULT_STRATEGY,
                    manual_delta=manual_delta)
        return plan


def _fmt_delta(delta: timedelta) -> str:
    negative = delta.total_seconds() < 0
    if negative:
        delta = -delta
    parts = []
    if delta.days:
        parts.append(f'{delta.days}d')
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    seconds = delta.seconds % 60
    if hours:
        parts.append(f'{hours}h')
    if minutes:
        parts.append(f'{minutes}m')
    if seconds:
        parts.append(f'{seconds}s')
    elif not parts:
        parts.append('0s')
    return ('-' if negative else '+') + ' '.join(parts)
