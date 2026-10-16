"""Reading and writing mailbox lists (CSV) for batch migrations.

The format is the one used by imapsync's own batch examples:

    user1;password1;user2;password2

Commas are accepted as separators too. Blank lines and lines starting
with '#' are ignored, and so is a header row such as
"user1;password1;user2;password2" or "source_user,...".
"""

from __future__ import annotations

import csv
import io

from .engine import Account

_HEADER_WORDS = {"user1", "user", "source", "source_user", "sourceuser",
                 "email", "username", "login", "from"}


def parse_accounts(text: str) -> tuple[list[Account], list[str]]:
    """Return (accounts, problems). Problems are human readable line notes."""
    accounts: list[Account] = []
    problems: list[str] = []
    lines = text.splitlines()
    sample = "\n".join(l for l in lines if l.strip() and not l.lstrip().startswith("#"))[:4096]
    delimiter = ";"
    if sample:
        semis, commas = sample.count(";"), sample.count(",")
        if commas > semis:
            delimiter = ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    first_row = True
    for lineno, row in enumerate(reader, start=1):
        if not row or not "".join(row).strip():
            continue
        if row[0].lstrip().startswith("#"):
            continue
        cells = [c.strip() for c in row]
        is_header = first_row and cells[0].lower().replace(" ", "_") in _HEADER_WORDS
        first_row = False
        if is_header:
            continue
        if len(cells) < 4:
            problems.append(f"Line {lineno}: expected 4 values "
                            f"(source user, source password, destination user, "
                            f"destination password), found {len(cells)}.")
            continue
        # Passwords keep their inner spaces; only the CSV quoting is removed.
        accounts.append(Account(cells[0], row[1], cells[2], row[3]))
    return accounts, problems


def format_accounts(accounts: list[Account], include_passwords: bool) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(["user1", "password1", "user2", "password2"])
    for a in accounts:
        writer.writerow([a.user1, a.secret1 if include_passwords else "",
                         a.user2, a.secret2 if include_passwords else ""])
    return buf.getvalue()
