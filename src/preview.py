from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from analysis import AnalysisResult, FileInfo, FileSet
import resolve


STRATEGY_GPS = 'gps'
STRATEGY_MANUAL = 'manual'
STRATEGY_SKIP = 'skip'

VALID_STRATEGIES = {STRATEGY_GPS, STRATEGY_MANUAL, STRATEGY_SKIP}


@dataclass
class SetDecision:
    strategy: str  # 'gps', 'manual', 'skip'
    manual_delta: timedelta | None = None  # only for 'manual'


@dataclass
class PreviewResult:
    set_id: str
    strategy: str
    file_results: list['FilePreview']


@dataclass
class FilePreview:
    path: Path
    current_embedded: datetime | None
    current_mtime: datetime | None
    current_gps: datetime | None
    target_embedded: datetime | None
    target_mtime: datetime | None
    source: str = ''  # 'embedded', 'matched <partner>', 'mtime'
    strategy_label: str = ''  # strategy prefix for CLI display


def compute_preview(
    analysis: AnalysisResult,
    decisions: dict[str, SetDecision],
    global_manual_delta: timedelta | None = None,
) -> list[PreviewResult]:
    results: list[PreviewResult] = []
    for fs in analysis.sets:
        decision = decisions.get(fs.id, SetDecision(strategy=STRATEGY_MANUAL, manual_delta=global_manual_delta))
        strategy = decision.strategy

        if strategy == STRATEGY_SKIP:
            file_previews = [_skip_preview(f, fs) for f in fs.files]
        elif strategy == STRATEGY_GPS:
            file_previews = [_gps_preview(fs, f) for f in fs.files]
        else:
            delta = decision.manual_delta or global_manual_delta or timedelta()
            file_previews = [_manual_preview(f, delta, fs) for f in fs.files]

        results.append(PreviewResult(
            set_id=fs.id,
            strategy=strategy,
            file_results=file_previews,
        ))
    return results


def _partner_embedded(fs: FileSet, fi: FileInfo) -> datetime | None:
    if fi.embedded_time is not None:
        return fi.embedded_time
    for f in fs.files:
        if f.embedded_time is not None:
            return f.embedded_time
    return None


def _source_label(fi: FileInfo, fs: FileSet) -> str:
    if fi.embedded_time is not None:
        return 'embedded'
    if fi.ext == '.thm':
        for f in fs.files:
            if f.embedded_time is not None:
                return f'matched {f.path.name}'
    return 'mtime'


def _skip_preview(fi: FileInfo, fs: FileSet) -> FilePreview:
    return FilePreview(
        path=fi.path,
        current_embedded=fi.embedded_time,
        current_mtime=fi.mtime,
        current_gps=fi.gps_time,
        target_embedded=fi.embedded_time,
        target_mtime=fi.mtime,
        source=_source_label(fi, fs),
        strategy_label='skip',
    )


def _gps_delta_for_set(fs: FileSet) -> timedelta | None:
    gps_source = None
    for f in fs.files:
        if f.gps_time is not None:
            gps_source = f
            break
    if gps_source is None or gps_source.gps_time is None:
        return None

    ref_file = None
    for f in fs.files:
        if f.embedded_time is not None:
            ref_file = f
            break
    if ref_file is None:
        return None

    return resolve.gps_delta(gps_source.gps_time, ref_file.embedded_time)


def _gps_preview(fs: FileSet, fi: FileInfo) -> FilePreview:
    delta = _gps_delta_for_set(fs)
    if delta is None:
        return _skip_preview(fi, fs)

    current_emb = fi.embedded_time
    current_mtime = fi.mtime
    ref_emb = _partner_embedded(fs, fi)
    target_emb = resolve.target_time(ref_emb, delta)
    target_mtime = resolve.target_time(current_mtime, delta)

    return FilePreview(
        path=fi.path,
        current_embedded=current_emb,
        current_mtime=current_mtime,
        current_gps=fi.gps_time,
        target_embedded=target_emb,
        target_mtime=target_mtime,
        source=_source_label(fi, fs),
        strategy_label='gps',
    )


def _manual_preview(fi: FileInfo, delta: timedelta, fs: FileSet | None = None) -> FilePreview:
    ref_emb = _partner_embedded(fs, fi) if fs else fi.embedded_time
    target_emb = resolve.target_time(ref_emb, delta)
    target_mtime = resolve.target_time(fi.mtime, delta)
    return FilePreview(
        path=fi.path,
        current_embedded=fi.embedded_time,
        current_mtime=fi.mtime,
        current_gps=fi.gps_time,
        target_embedded=target_emb,
        target_mtime=target_mtime,
        source=_source_label(fi, fs) if fs else 'embedded',
        strategy_label='manual',
    )
