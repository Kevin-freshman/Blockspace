"""Atomic runtime storage for longitudinal research snapshots."""

from __future__ import annotations

import gzip
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from research_engine import research_slot_id


class ResearchStore:
    def __init__(self, data_dir: Path, retention_days: int = 90) -> None:
        self.root = data_dir / "research"
        self.root.mkdir(parents=True, exist_ok=True)
        self.retention_days = max(1, int(retention_days))

    def path_for_epoch(self, slot_epoch: int) -> Path:
        return self.root / ("leaderboard-%s.json.gz" % research_slot_id(slot_epoch))

    def analysis_path_for_epoch(self, slot_epoch: int) -> Path:
        return self.root / ("analysis-%s.json.gz" % research_slot_id(slot_epoch))

    def exists(self, slot_epoch: int) -> bool:
        return self.path_for_epoch(slot_epoch).is_file()

    def save(self, snapshot: Dict[str, Any]) -> None:
        path = self.path_for_epoch(int(snapshot["slot_epoch"]))
        self._save(path, snapshot)
        if isinstance(snapshot.get("analysis"), dict):
            self._save(
                self.analysis_path_for_epoch(int(snapshot["slot_epoch"])),
                {
                    "slot_epoch": snapshot["slot_epoch"],
                    "slot_at": snapshot.get("slot_at"),
                    "analysis": snapshot["analysis"],
                },
            )
        self.prune(int(snapshot["slot_epoch"]))

    def load_epoch(self, slot_epoch: int) -> Optional[Dict[str, Any]]:
        return self._load(self.path_for_epoch(slot_epoch))

    def load_latest(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        paths = sorted(self.root.glob("leaderboard-*.json.gz"), reverse=True)
        if limit is not None:
            paths = paths[: max(0, int(limit))]
        snapshots = []
        for path in paths:
            snapshot = self._load(path)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def load_oldest(self) -> Optional[Dict[str, Any]]:
        paths = sorted(self.root.glob("leaderboard-*.json.gz"))
        return self._load(paths[0]) if paths else None

    def load_latest_analyses(
        self, limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        paths = sorted(self.root.glob("analysis-*.json.gz"), reverse=True)
        if limit is not None:
            paths = paths[: max(0, int(limit))]
        result = []
        for path in paths:
            value = self._load(path)
            if value is not None and isinstance(value.get("analysis"), dict):
                result.append(value)
        return result

    def count(self) -> int:
        return sum(1 for _path in self.root.glob("leaderboard-*.json.gz"))

    def prune(self, latest_slot_epoch: int) -> None:
        cutoff = int(latest_slot_epoch) - self.retention_days * 86400
        for pattern in ("leaderboard-*.json.gz", "analysis-*.json.gz"):
            for path in self.root.glob(pattern):
                slot_epoch = self._epoch_from_path(path)
                if slot_epoch is not None and slot_epoch < cutoff:
                    path.unlink()

    @staticmethod
    def _epoch_from_path(path: Path) -> Optional[int]:
        try:
            value = path.name.split("-", 1)[1].split(".", 1)[0]
            parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
            return int(parsed.timestamp())
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _save(path: Path, value: Dict[str, Any]) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(str(temporary), "wt", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
        temporary.replace(path)

    @staticmethod
    def _load(path: Path) -> Optional[Dict[str, Any]]:
        try:
            with gzip.open(str(path), "rt", encoding="utf-8") as handle:
                value = json.load(handle)
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, TypeError):
            return None
