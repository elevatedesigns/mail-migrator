"""Turns imapsync's text output into progress information."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ETA = re.compile(r"ETA: .*?\s(\d+)/(\d+) msgs left")
_FOLDER = re.compile(r"^Folder\s+(\d+)/(\d+)\s+(.*?)\s+->\s+(.*)$")
_TRANSFERRED = re.compile(r"^Messages transferred\s*:\s*(\d+)")
_SKIPPED = re.compile(r"^Messages skipped\s*:\s*(\d+)")
_BYTES = re.compile(r"^Total bytes transferred\s*:\s*\d+\s*\((.*?)\)")
_ERRORS = re.compile(r"^Detected (\d+) errors")
_SIZES = re.compile(r"^Host([12]) (Nb folders|Nb messages|Total size):\s+(\d+)\s+\S+(?:\s+\((.*?)\))?")
_LOGFILE = re.compile(r"^Log file is (.+?) \( to change it")


@dataclass
class Progress:
    done: int = 0
    total: int = 0
    folder: str = ""
    folder_index: int = 0
    folder_count: int = 0
    transferred: int | None = None
    skipped: int | None = None
    bytes_text: str = ""
    errors: int | None = None
    logfile: str = ""
    cert_error: bool = False
    # From imapsync's size summary, per side: {"folders": n, "messages": n, "bytes": n, "size": "1.2 GiB"}
    sizes: dict = field(default_factory=lambda: {1: {}, 2: {}})

    @property
    def fraction(self) -> float | None:
        if self.total <= 0:
            return None
        return min(1.0, max(0.0, self.done / self.total))


def is_error_line(line: str) -> bool:
    s = line.lstrip()
    return (s.startswith(("Err ", "Error", "Failure", "failure"))
            or "Login failed" in s
            or "[AUTHENTICATIONFAILED]" in s)


def is_warning_line(line: str) -> bool:
    s = line.lstrip()
    return s.startswith(("Warning", "WARNING"))


class ProgressParser:
    def __init__(self) -> None:
        self.progress = Progress()

    def feed(self, line: str) -> bool:
        """Consume one output line. Returns True when progress changed."""
        line = line.rstrip("\r\n")
        p = self.progress
        if "certificate verify failed" in line or "hostname verification failed" in line:
            p.cert_error = True
        if m := _ETA.search(line):
            left, total = int(m.group(1)), int(m.group(2))
            p.total = total
            p.done = max(0, total - left)
            return True
        if m := _FOLDER.match(line):
            p.folder_index, p.folder_count = int(m.group(1)), int(m.group(2))
            p.folder = m.group(3).strip().strip("[]")
            return True
        if m := _TRANSFERRED.match(line):
            p.transferred = int(m.group(1))
            return True
        if m := _SKIPPED.match(line):
            p.skipped = int(m.group(1))
            return True
        if m := _BYTES.match(line):
            p.bytes_text = m.group(1)
            return True
        if m := _ERRORS.match(line):
            p.errors = int(m.group(1))
            return True
        if m := _SIZES.match(line):
            side, what, number, human = int(m.group(1)), m.group(2), int(m.group(3)), m.group(4)
            key = {"Nb folders": "folders", "Nb messages": "messages", "Total size": "bytes"}[what]
            p.sizes[side][key] = number
            if human:
                p.sizes[side]["size"] = human
            return True
        if m := _LOGFILE.match(line):
            p.logfile = m.group(1).strip()
            return True
        return False
