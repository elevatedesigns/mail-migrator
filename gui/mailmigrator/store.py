"""Saved presets and migration history, kept as JSON in the app data folder.

Neither ever contains passwords or tokens.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

HISTORY_LIMIT = 500


class Store:
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.presets_dir = self.root / "presets"
        self.history_file = self.root / "history.json"
        self.logs_dir = self.root / "logs"
        for d in (self.presets_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    # -- presets ----------------------------------------------------------

    def list_presets(self) -> list[dict]:
        out = []
        for path in self.presets_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                data["_path"] = str(path)
                out.append(data)
        out.sort(key=lambda d: d.get("saved", ""), reverse=True)
        return out

    def save_preset(self, name: str, profile: dict) -> Path:
        slug = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip() or "preset"
        path = self.presets_dir / f"{slug}.json"
        data = dict(profile)
        data["name"] = name
        data["saved"] = datetime.now().isoformat(timespec="seconds")
        _strip_secrets(data)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def delete_preset(self, path: str) -> None:
        p = Path(path)
        if p.parent == self.presets_dir:
            p.unlink(missing_ok=True)

    # -- history ----------------------------------------------------------

    def history(self) -> list[dict]:
        try:
            data = json.loads(self.history_file.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, ValueError):
            return []

    def add_history(self, entry: dict) -> None:
        items = self.history()
        entry = dict(entry)
        entry.setdefault("when", datetime.now().isoformat(timespec="seconds"))
        _strip_secrets(entry)
        items.insert(0, entry)
        tmp = self.history_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(items[:HISTORY_LIMIT], indent=2), encoding="utf-8")
        os.replace(tmp, self.history_file)

    def clear_history(self) -> None:
        self.history_file.unlink(missing_ok=True)

    # -- logs ---------------------------------------------------------------

    def log_files(self) -> list[Path]:
        files = [p for p in self.logs_dir.rglob("*.txt") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files


def _strip_secrets(obj) -> None:
    if isinstance(obj, dict):
        for key in list(obj):
            if key.lower() in ("password", "secret", "secret1", "secret2", "token"):
                del obj[key]
            else:
                _strip_secrets(obj[key])
    elif isinstance(obj, list):
        for item in obj:
            _strip_secrets(item)
