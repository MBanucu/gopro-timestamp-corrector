from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from analysis import AnalysisResult, FileInfo, FileSet


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
            file_previews = [_skip_preview(f) for f in fs.files]
        elif strategy == STRATEGY_GPS:
            file_previews = [_gps_preview(fs, f) for f in fs.files]
        else:
            delta = decision.manual_delta or global_manual_delta or timedelta()
            file_previews = [_manual_preview(f, delta) for f in fs.files]

        results.append(PreviewResult(
            set_id=fs.id,
            strategy=strategy,
            file_results=file_previews,
        ))
    return results


def _skip_preview(fi: FileInfo) -> FilePreview:
    return FilePreview(
        path=fi.path,
        current_embedded=fi.embedded_time,
        current_mtime=fi.mtime,
        current_gps=fi.gps_time,
        target_embedded=fi.embedded_time,
        target_mtime=fi.mtime,
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

    gps_utc = gps_source.gps_time
    gps_utc_tz = gps_utc.replace(tzinfo=timezone.utc)
    local_tz = datetime.now().astimezone().tzinfo
    actual_dt = gps_utc_tz.astimezone(local_tz).replace(tzinfo=None)
    return actual_dt - ref_file.embedded_time


def _gps_preview(fs: FileSet, fi: FileInfo) -> FilePreview:
    delta = _gps_delta_for_set(fs)
    if delta is None:
        return _skip_preview(fi)

    current_emb = fi.embedded_time
    current_mtime = fi.mtime
    target_emb = current_emb + delta if current_emb else None
    target_mtime = current_mtime + delta if current_mtime else None

    return FilePreview(
        path=fi.path,
        current_embedded=current_emb,
        current_mtime=current_mtime,
        current_gps=fi.gps_time,
        target_embedded=target_emb,
        target_mtime=target_mtime,
    )


def _manual_preview(fi: FileInfo, delta: timedelta) -> FilePreview:
    target_emb = fi.embedded_time + delta if fi.embedded_time else None
    target_mtime = fi.mtime + delta if fi.mtime else None
    return FilePreview(
        path=fi.path,
        current_embedded=fi.embedded_time,
        current_mtime=fi.mtime,
        current_gps=fi.gps_time,
        target_embedded=target_emb,
        target_mtime=target_mtime,
    )
