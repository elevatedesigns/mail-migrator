"""Runs imapsync for one or more mailboxes, one after the other."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from .engine import Account, Command, CommandError, Options, Server, build_command
from .progress import Progress, ProgressParser

CREATE_NO_WINDOW = 0x08000000


@dataclass
class AccountResult:
    account: Account
    exit_code: int | None = None   # None: not run (cancelled or invalid)
    error: str = ""
    progress: Progress | None = None


class MigrationRunner(QObject):
    line = Signal(str)
    progress = Signal(int, object)          # account index, Progress
    account_started = Signal(int, str)      # index, shown command
    account_finished = Signal(int, object)  # index, AccountResult
    finished = Signal(list)                 # list[AccountResult]

    def __init__(self, engine: list[str], workdir: str, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.workdir = workdir
        self.logdir = os.path.join(workdir, "logs")
        self.tmpdir = os.path.join(workdir, "tmp")
        self._proc: QProcess | None = None
        self._queue: list[int] = []
        self._results: list[AccountResult] = []
        self._current = -1
        self._command: Command | None = None
        self._parser = ProgressParser()
        self._buffer = ""
        self._stopping = False
        self._kill_timer = QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._force_kill)

    # -- public -----------------------------------------------------------

    def is_running(self) -> bool:
        return self._proc is not None

    def start(self, src: Server, dst: Server, accounts: list[Account],
              options: Options) -> None:
        if self.is_running():
            return
        os.makedirs(self.logdir, exist_ok=True)
        os.makedirs(self.tmpdir, exist_ok=True)
        self._src, self._dst, self._options = src, dst, options
        self._results = [AccountResult(a) for a in accounts]
        self._queue = list(range(len(accounts)))
        self._stopping = False
        self._next()

    def stop(self) -> None:
        self._stopping = True
        self._queue.clear()
        if self._proc is not None:
            self.line.emit("Stopping…")
            self._proc.terminate()
            self._kill_timer.start(4000)

    # -- internals --------------------------------------------------------

    def _next(self) -> None:
        if self._stopping or not self._queue:
            self._finish()
            return
        self._current = self._queue.pop(0)
        result = self._results[self._current]
        try:
            self._command = build_command(self.engine, self._src, self._dst,
                                          result.account, self._options,
                                          logdir=self.logdir, tmpdir=self.tmpdir)
        except CommandError as exc:
            result.error = str(exc)
            self.line.emit(f"Skipped {result.account.user1 or '(no user)'}: {exc}")
            self.account_finished.emit(self._current, result)
            QTimer.singleShot(0, self._next)
            return

        self._parser = ProgressParser()
        self._buffer = ""
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.setWorkingDirectory(self.workdir)
        env = QProcessEnvironment.systemEnvironment()
        for key, value in self._command.env.items():
            env.insert(key, value)
        proc.setProcessEnvironment(env)
        if sys.platform == "win32":
            _hide_console_window(proc)
        proc.readyReadStandardOutput.connect(self._read)
        proc.finished.connect(self._done)
        proc.errorOccurred.connect(self._error)
        self._proc = proc
        self.account_started.emit(self._current, self._command.display())
        proc.start(self._command.args[0], self._command.args[1:])
        proc.closeWriteChannel()  # never wait on a terminal prompt

    def _read(self) -> None:
        if self._proc is None:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
        self._buffer += data.replace("\r\n", "\n").replace("\r", "\n")
        *lines, self._buffer = self._buffer.split("\n")
        for line in lines:
            self._handle_line(line)

    def _handle_line(self, line: str) -> None:
        self.line.emit(line)
        if self._parser.feed(line):
            self.progress.emit(self._current, self._parser.progress)

    def _error(self, err: QProcess.ProcessError) -> None:
        if err == QProcess.FailedToStart and self._proc is not None:
            self.line.emit(f"Could not start imapsync: {self._proc.errorString()}")
            self._done(-1, QProcess.CrashExit)

    def _done(self, code: int, status: QProcess.ExitStatus) -> None:
        if self._proc is None:
            return
        self._kill_timer.stop()
        if self._buffer:
            self._handle_line(self._buffer)
            self._buffer = ""
        proc, self._proc = self._proc, None
        proc.deleteLater()
        for path in self._command.temp_files if self._command else []:
            try:
                os.remove(path)
            except OSError:
                pass
        result = self._results[self._current]
        result.progress = self._parser.progress
        if self._stopping:
            result.exit_code = 6
        elif status == QProcess.CrashExit and code == 0:
            result.exit_code = 1
        else:
            result.exit_code = code
        self.account_finished.emit(self._current, result)
        QTimer.singleShot(0, self._next)

    def _force_kill(self) -> None:
        if self._proc is not None:
            self._proc.kill()

    def _finish(self) -> None:
        self._current = -1
        self.finished.emit(self._results)


def _hide_console_window(proc: QProcess) -> None:
    """On Windows, stop imapsync.exe from flashing a console window."""
    modifier = getattr(proc, "setCreateProcessArgumentsModifier", None)
    if modifier is None:
        return

    def add_flag(args) -> None:
        try:
            args.flags |= CREATE_NO_WINDOW
        except Exception:
            pass

    try:
        modifier(add_flag)
    except Exception:
        pass
