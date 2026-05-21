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
class Planner:
    """Single source of truth for plan‑step options.

    The GUI binds to this object — when the user toggles a checkbox or
    reorders the btime list the planner is updated immediately, and
    external changes (e.g. filesystem detection) are pushed back to the
    UI via :meth:`~gui.steps.plan.StepPlan.set_filesystem`.
    """

    fix_embedded: bool = True
    fix_mtime: bool = True
    fix_btime: bool = False
    btime_methods: list[str] = field(default_factory=lambda: ['clock'])
    dry_run: bool = True
    force: bool = False

    def to_dict(self) -> dict:
        return {
            'fix_embedded': self.fix_embedded,
            'fix_mtime': self.fix_mtime,
            'fix_btime': list(self.btime_methods) if self.fix_btime else 'off',
            'dry_run': self.dry_run,
            'force': self.force,
        }


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

    def to_string(self, *, btime_chain: list[str] | str | None = None) -> str:
        """Like :meth:`summary` but accepts the actual btime chain
        selected in the Plan step (a list of method names) instead of
        the internal ``btime_method`` placeholder.

        Pass an empty list or ``'off'`` to indicate btime is disabled.
        """
        s = self.summary()
        chain_str: str | None = None
        if btime_chain is None:
            chain_str = None  # keep the default from summary()
        elif isinstance(btime_chain, list) and btime_chain:
            chain_str = ' > '.join(btime_chain)
        else:
            chain_str = 'off'
        if chain_str is None:
            return s
        parts = s.split(', ')
        return ', '.join(
            f'btime={chain_str}' if p.startswith('btime=') else p
            for p in parts
        )

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


# ── Instruction / PlanBuilder ──────────────────────────────────

INSTRUCTION_STATUS = ('pending', 'running', 'done', 'failed', 'skipped')


@dataclass
class Instruction:
    """A single executable step in a correction plan.

    Built by :class:`PlanBuilder` and executed in order when the user
    presses **Apply** in the Run step.  Each instruction holds its
    current status so the UI can display progress live.
    """
    type: str          # 'build' | 'capture' | 'write' | 'report' | 'cli'
    label: str         # human‑readable description shown in the Run step
    enabled: bool = True
    status: str = 'pending'   # one of INSTRUCTION_STATUS
    error: str | None = None
    args: dict = field(default_factory=dict)


class PlanBuilder:
    """Builds a list of :class:`Instruction` objects from a
    :class:`Planner` and a :class:`CorrectionPlan`, then executes
    them sequentially with progress callbacks."""

    def build(self, planner: Planner, correction_plan: CorrectionPlan | None,
              target_dir: Path) -> list[Instruction]:
        jobs = correction_plan.to_jobs() if correction_plan is not None else []
        opts = planner.to_dict()
        btime_val = opts['fix_btime']
        btime_str = ' > '.join(btime_val) if isinstance(btime_val, list) else 'off'
        delta = correction_plan.manual_delta if correction_plan is not None else None

        # History metadata used by begin_run / finalize_run
        history_meta = {
            'fix_embedded': opts['fix_embedded'],
            'fix_mtime': opts['fix_mtime'],
            'fix_btime': btime_val,
            'global_delta': str(delta) if delta else None,
        }
        if correction_plan is not None:
            history_meta['sets'] = {
                sid: {'strategy': d.strategy}
                for sid, d in correction_plan.decisions.items()
            }

        instructions: list[Instruction] = []

        # 1. Build
        n = len(jobs)
        dry_run = opts['dry_run']
        instructions.append(Instruction(
            type='build', label=f'Collect {n} file{"s" if n != 1 else ""} from analysis'
                         + (' (dry run)' if dry_run else ''),
            args={'jobs': jobs, 'correction_plan': correction_plan,
                  'target_dir': str(target_dir), 'opts': opts,
                  'delta': delta, 'history_meta': history_meta},
            enabled=n > 0))

        if correction_plan is not None:
            # 2. Capture before (includes begin_run)
            instructions.append(Instruction(
                type='capture', label='Save before-state (metadata + btime)',
                args={'phase': 'before', 'jobs': jobs, 'target_dir': str(target_dir),
                      'delta': delta, 'history_meta': history_meta},
                enabled=n > 0))

            # 3. Write embedded
            instructions.append(Instruction(
                type='write', label='EXIF / QuickTime embedded metadata',
                args={'mode': 'embedded', 'jobs': jobs, 'target_dir': str(target_dir),
                      'btime_val': btime_val, 'opts': opts, 'delta': delta},
                enabled=opts['fix_embedded'] and n > 0))

            # 4. Write mtime
            instructions.append(Instruction(
                type='write', label='Filesystem modification time (mtime)',
                args={'mode': 'mtime', 'jobs': jobs, 'target_dir': str(target_dir),
                      'btime_val': btime_val, 'opts': opts, 'delta': delta},
                enabled=opts['fix_mtime'] and n > 0))

            # 5. Write btime
            instructions.append(Instruction(
                type='write', label=f'Filesystem birth time (btime={btime_str})',
                args={'mode': 'btime', 'jobs': jobs, 'target_dir': str(target_dir),
                      'btime_val': btime_val, 'opts': opts, 'delta': delta},
                enabled=isinstance(btime_val, list) and bool(btime_val) and n > 0))

            # 6. Capture after
            instructions.append(Instruction(
                type='capture', label='Save after-state (metadata + btime)',
                args={'phase': 'after', 'jobs': jobs, 'target_dir': str(target_dir),
                      'delta': delta, 'history_meta': history_meta},
                enabled=n > 0))

            # 7. Report
            instructions.append(Instruction(
                type='report', label='Finalize run history',
                args={'target_dir': str(target_dir), 'history_meta': history_meta,
                      'delta': delta},
                enabled=n > 0))
        else:
            # CLI fallback (no plan loaded)
            instructions.append(Instruction(
                type='cli', label='Run CLI fallback',
                args={'target_dir': str(target_dir), 'opts': opts},
                enabled=True))

        return instructions

    def execute(self, instructions: list[Instruction],
                log_fn=lambda msg: None,
                progress_fn=lambda idx, status: None,
                session=None) -> dict:
        """Execute *instructions* in order, calling *progress_fn* after
        each step with ``(index, new_status)``.

        Returns a dict with ``'exit_code'``, ``'written'``, ``'skipped'``,
        ``'errors'``.
        """
        from history import begin_run, capture_before, capture_after, finalize_run
        from writer import Writer, BTIME_OFF
        from datetime import datetime, timezone
        from pathlib import Path

        result: dict = {'exit_code': 0, 'written': 0, 'skipped': 0, 'errors': []}
        run_dir: Path | None = None
        writer: Writer | None = None

        def set_status(i, status):
            instructions[i].status = status
            progress_fn(i, status)

        def get_writer(target, btime_val, delta_str):
            nonlocal writer
            if writer is None:
                delta = timedelta(seconds=delta_str) if isinstance(delta_str, (int, float)) else None
                writer = Writer(target, fix_btime=btime_val,
                                delta=delta, dry_run=False, session=session)
            return writer

        for i, inst in enumerate(instructions):
            if not inst.enabled:
                set_status(i, 'skipped')
                continue
            if inst.status == 'done':
                continue

            set_status(i, 'running')
            try:
                if inst.type == 'build':
                    jobs = inst.args.get('jobs', [])
                    opts = inst.args.get('opts', {})
                    log_fn(f'Plan: {len(jobs)} files ready')
                    if opts.get('dry_run'):
                        log_fn('  (dry run — no actual writes)')
                    if not jobs:
                        log_fn('  No files to process.')

                elif inst.type == 'capture':
                    phase = inst.args['phase']
                    jobs = inst.args['jobs']
                    target = Path(inst.args['target_dir'])
                    history_meta = inst.args.get('history_meta', {})
                    if phase == 'before':
                        meta = dict(history_meta)
                        meta.setdefault('timestamp',
                                        datetime.now(timezone.utc).strftime(
                                            '%Y%m%dT%H%M%S%fZ'))
                        run_dir = begin_run(target, meta)
                        capture_before(session, run_dir, [j.path for j in jobs])
                        log_fn(f'  Captured before-state ({len(jobs)} files)')
                    else:
                        if run_dir:
                            capture_after(session, run_dir, [j.path for j in jobs])
                            log_fn(f'  Captured after-state ({len(jobs)} files)')

                elif inst.type == 'write':
                    mode = inst.args['mode']
                    jobs = inst.args['jobs']
                    target = Path(inst.args['target_dir'])
                    opts = inst.args.get('opts', {})
                    btime_val = inst.args['btime_val']
                    delta = inst.args.get('delta')

                    dry_run = opts.get('dry_run', False)
                    if dry_run:
                        log_fn(f'  Would write {len(jobs)} file(s) — {mode}')
                        result['written'] += len(jobs)
                        set_status(i, 'done')
                        continue

                    w = get_writer(target, btime_val, delta)

                    if mode == 'embedded':
                        written = 0
                        for job in jobs:
                            if w.write_embedded_only(job):
                                written += 1
                        log_fn(f'  {written} file(s) — embedded metadata')
                        result['written'] += written

                    elif mode == 'mtime':
                        for job in jobs:
                            w.write_mtime_only(job)
                        log_fn(f'  {len(jobs)} file(s) — mtime')
                        result['written'] += len(jobs)

                    elif mode == 'btime':
                        for job in jobs:
                            w.write_btime_only(job)
                        log_fn(f'  {len(jobs)} file(s) — btime ({btime_val})')
                        result['written'] += len(jobs)

                elif inst.type == 'report':
                    if run_dir:
                        written = result.get('written', 0)
                        errors = result.get('errors', [])
                        finalize_run(run_dir, written, 0, errors)
                        log_fn(f'  Done — {written} corrected, {len(errors)} errors')
                    if writer:
                        writer.close()

                elif inst.type == 'cli':
                    log_fn('CLI fallback: not implemented in instruction mode')
                    result['exit_code'] = -1

                set_status(i, 'done')
            except Exception as e:
                inst.status = 'failed'
                inst.error = str(e)
                set_status(i, 'failed')
                log_fn(f'  ✗  {inst.label}: {e}')
                result['errors'].append(str(e))
                result['exit_code'] = -1
                break

        if writer:
            try:
                writer.close()
            except Exception:
                pass

        return result
