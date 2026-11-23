"""Policy: which files a run may read or write, and where traces may lead.

Two ideas:

* **Roots** are the declared allow-list: a path is readable/writable when it sits
  under one of them.
* **Trace following** widens *reading* only: a path that a tool result already
  mentioned (a delta-iz change line, a session record, a search hit) becomes
  readable, because the user's own data surfaced it. Writing never follows traces.

Secrets and system locations are refused in every path — even when they appear in
a trace and even when a root would cover them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable

# A path that lands here is never followed: credentials and system trees.
FORBIDDEN = re.compile(
    r"^(/etc|/usr|/bin|/sbin|/System|/Library|/opt/homebrew|/private/etc|/private/var/db)"
    r"|/\.ssh(/|$)|/\.gnupg(/|$)|/\.hermes(/|$)|/\.config(/|$)|/\.aws(/|$)"
)

# Names that hold credentials: refused by name, never by directory name.
SECRET_NAME = re.compile(r"(^\.env($|\.)|key|\.pem$)", re.IGNORECASE)

# Where a path may start inside free text: absolute or `~`-relative.
PATH_IN_TEXT = re.compile(r"(?:~|/)[^\s\"'`()\[\]{}<>|,;]+")


class PolicyError(Exception):
    """Raised when a path is refused, with the reason spelled out."""


def _expand(raw: str | Path) -> Path:
    text = os.path.expanduser(str(raw).strip())
    path = Path(text)
    if not path.is_absolute():
        path = Path.home() / path
    return path.resolve()


class Policy:
    """Read/write allow-list with read-only trace following."""

    def __init__(
        self,
        roots: Iterable[str | Path] = (),
        files: Iterable[str | Path] = (),
        follow_traces: bool = True,
    ) -> None:
        self.roots = tuple(_expand(root) for root in roots)
        self.files = frozenset(_expand(item) for item in files)
        self.follow_traces = follow_traces
        self._files_seen: set[Path] = set()
        self._dirs_seen: set[Path] = set()

    # -- trace ledger -------------------------------------------------------
    def clear_traces(self) -> None:
        """Forget everything the previous turn taught us."""
        self._files_seen.clear()
        self._dirs_seen.clear()

    def remember(self, text: str) -> int:
        """Register every path mentioned in ``text``; return how many were new."""
        added = 0
        for raw in PATH_IN_TEXT.findall(str(text or "")):
            if self.remember_path(raw):
                added += 1
        return added

    def remember_path(self, raw: str) -> bool:
        """Register one path. False when it is refused or does not exist."""
        text = re.sub(r":\d+$", "", str(raw).rstrip(".,;:)"))  # drop a `:LINE` suffix
        try:
            path = _expand(text)
        except (OSError, ValueError):
            return False
        if not str(path).startswith("/") or FORBIDDEN.search(str(path)):
            return False
        if self.is_secret(path) or not path.exists():
            return False
        if path.is_dir():
            self._dirs_seen.add(path)
        else:
            self._files_seen.add(path)
        return True

    def follows(self, path: Path) -> bool:
        """Was this path (or its directory) surfaced by a tool result?"""
        if path in self._files_seen:
            return True
        return any(path == seen or path.is_relative_to(seen) for seen in self._dirs_seen)

    # -- decisions ----------------------------------------------------------
    def is_secret(self, path: Path) -> bool:
        return bool(SECRET_NAME.search(Path(path).name))

    def allow(self, raw: str | Path, *, writing: bool = False) -> Path:
        """Resolve ``raw`` and return it, or raise ``PolicyError`` with the reason."""
        text = str(raw).strip()
        if not text:
            raise PolicyError("missing input: a path is required")
        path = _expand(text)
        if self.is_secret(path):
            raise PolicyError(f"secret file refused: {path.name}")
        if path in self.files:
            return path
        if any(path == root or path.is_relative_to(root) for root in self.roots):
            return path
        followed = self.follow_traces and self.follows(path)
        if followed and not writing:
            return path
        reason = "outside the allowed roots"
        if followed:  # the path came from a trace, but writing it is not allowed
            reason = "trace following widens reading only"
        elif not path.exists():
            reason = "does not exist on this machine (not a permission problem)"
        raise PolicyError(f"path refused: {path} ({reason})")

    def __len__(self) -> int:
        return len(self.roots) + len(self.files) + len(self._files_seen) + len(self._dirs_seen)
