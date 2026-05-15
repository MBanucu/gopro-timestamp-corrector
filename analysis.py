from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re

import media


@dataclass
class FileInfo:
    path: Path
    stem: str
    ext: str
    mtime: datetime | None = None
    embedded_time: datetime | None = None
    gps_time: datetime | None = None


@dataclass
class FileSet:
    id: str
    files: list[FileInfo] = field(default_factory=list)

    @property
    def has_any_gps(self) -> bool:
        return any(f.gps_time is not None for f in self.files)

    @property
    def has_any_embedded(self) -> bool:
        return any(f.embedded_time is not None for f in self.files)

    @property
    def kind(self) -> str:
        types = set(f.ext for f in self.files)
        parts = []
        if '.mp4' in types:
            parts.append('MP4')
        if '.lrv' in types:
            parts.append('LRV')
        if '.thm' in types:
            parts.append('THM')
        return '+'.join(parts) if parts else '?'


@dataclass
class AnalysisResult:
    directory: str
    sets: list[FileSet]

    @property
    def total_files(self) -> int:
        return sum(len(s.files) for s in self.sets)


def _group_key(path: Path) -> str | None:
    m = re.search(r'(\d{6,})', path.stem)
    return m.group(1) if m else None


def analyze(directory: str | Path) -> AnalysisResult:
    target = Path(directory)
    raw_files = media.collect(target)

    batch = media.read_tags_batch(raw_files)

    groups: dict[str, list[FileInfo]] = {}
    for f in raw_files:
        key = _group_key(f)
        if not key:
            continue
        embedded, gps = batch.get(f, (None, None))
        info = FileInfo(
            path=f,
            stem=f.stem,
            ext=f.suffix.lower(),
            mtime=media.read_mtime(f),
            embedded_time=embedded,
            gps_time=gps,
        )
        groups.setdefault(key, []).append(info)

    return AnalysisResult(
        directory=str(target),
        sets=[FileSet(id=k, files=v) for k, v in sorted(groups.items())],
    )
