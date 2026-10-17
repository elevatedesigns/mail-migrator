"""Remembering what was already copied, and mapping old addresses to new ones."""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading


class State:
    """Per mailbox pair: source item -> destination item, with a content fingerprint.

    Re-running a migration skips unchanged items and updates changed ones, the
    same way imapsync never copies a message twice.
    """

    def __init__(self, directory: str, source: str, destination: str) -> None:
        os.makedirs(directory, exist_ok=True)
        key = hashlib.sha1(f"{source.lower()}→{destination.lower()}".encode()).hexdigest()[:20]
        self.path = os.path.join(directory, f"{key}.sqlite")
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS items (kind TEXT, source TEXT, dest TEXT, "
                        "fingerprint TEXT, PRIMARY KEY (kind, source))")
        self.db.commit()

    def get(self, kind: str, source: str) -> tuple[str, str] | None:
        with self._lock:
            row = self.db.execute("SELECT dest, fingerprint FROM items WHERE kind=? AND source=?",
                                  (kind, source)).fetchone()
        return (row[0], row[1]) if row else None

    def put(self, kind: str, source: str, dest: str, fingerprint: str = "") -> None:
        with self._lock:
            self.db.execute("INSERT OR REPLACE INTO items VALUES (?, ?, ?, ?)",
                            (kind, source, dest, fingerprint))
            self.db.commit()

    def forget(self, kind: str, source: str) -> None:
        with self._lock:
            self.db.execute("DELETE FROM items WHERE kind=? AND source=?", (kind, source))
            self.db.commit()

    def close(self) -> None:
        with self._lock:
            self.db.close()


class UserMap:
    """Rewrites email addresses for sharing, attendees and shared drive members.

    One rule per line, either an address or a whole domain:
        alice@old.com = alice.smith@new.com
        @old.com = @new.com
    """

    LINE = re.compile(r"^\s*(\S+)\s*(?:=|->|→|,|;)\s*(\S+)\s*$")

    def __init__(self, text: str = "") -> None:
        self.users: dict[str, str] = {}
        self.domains: dict[str, str] = {}
        self.problems: list[str] = []
        for n, line in enumerate(text.splitlines(), 1):
            if not line.strip() or line.strip().startswith("#"):
                continue
            m = self.LINE.match(line)
            if not m:
                self.problems.append(f"Line {n}: expected 'old = new'")
                continue
            old, new = m.group(1).lower(), m.group(2)
            if old.startswith("@") and new.startswith("@"):
                self.domains[old[1:]] = new[1:]
            elif "@" in old and "@" in new and not old.startswith("@"):
                self.users[old] = new
            else:
                self.problems.append(f"Line {n}: use two addresses or two @domains")

    def map(self, email: str) -> str:
        if not email:
            return email
        low = email.lower()
        if low in self.users:
            return self.users[low]
        if "@" in low:
            local, domain = low.rsplit("@", 1)
            if domain in self.domains:
                return f"{email.rsplit('@', 1)[0]}@{self.domains[domain]}"
        return email

    def map_domain(self, domain: str) -> str:
        return self.domains.get(domain.lower(), domain)

    def __bool__(self) -> bool:
        return bool(self.users or self.domains)
