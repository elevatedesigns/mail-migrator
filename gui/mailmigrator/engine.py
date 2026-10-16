"""Building imapsync command lines and locating the imapsync engine.

Nothing in this module depends on Qt so it can be unit tested on its own.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .providers import PROVIDERS

SECURITY_SSL = "ssl"          # Implicit TLS (IMAPS, usually port 993)
SECURITY_STARTTLS = "starttls"  # Plain connection upgraded with STARTTLS (usually 143)
SECURITY_NONE = "none"        # No encryption at all

AUTH_PASSWORD = "password"
AUTH_OAUTH2 = "oauth2"

EXIT_MESSAGES = {
    0: "Success",
    1: "Unexpected error",
    6: "Stopped",
    7: "Stopped by abort file",
    8: "Another migration with the same accounts is already running",
    10: "Could not connect to a server",
    12: "Secure connection (TLS) failed",
    16: "Login failed",
    21: "The requested source subfolder does not exist",
    64: "Invalid option (check Extra arguments)",
    66: "Cannot read an input file",
    69: "Service unavailable",
    70: "Internal imapsync error",
    101: "Could not connect to the source server",
    102: "Could not connect to the destination server",
    111: "Finished with some errors",
    112: "Stopped: too many errors",
    113: "Destination mailbox is over quota",
    114: "Destination refused to store some messages",
    115: "Could not read some messages from the source",
    116: "Could not create some folders on the destination",
    117: "Could not open some folders",
    118: "Stopped: transfer limit reached",
    119: "Destination rejected messages as viruses",
    120: "Could not set some message flags",
    121: "Search command failed on a server",
    161: "Login failed on the source account",
    162: "Login failed on the destination account",
}


def describe_exit(code: int) -> str:
    return EXIT_MESSAGES.get(code, f"imapsync exited with code {code}")


@dataclass
class Server:
    """One side (source "1" or destination "2") of a migration."""

    provider: str = "custom"
    host: str = ""
    port: int = 0                 # 0 means "imapsync default for the security mode"
    security: str = SECURITY_SSL
    auth: str = AUTH_PASSWORD

    def to_dict(self) -> dict:
        return dict(provider=self.provider, host=self.host, port=self.port,
                    security=self.security, auth=self.auth)

    @classmethod
    def from_dict(cls, data: dict) -> "Server":
        s = cls()
        for key in ("provider", "host", "security", "auth"):
            if isinstance(data.get(key), str):
                setattr(s, key, data[key])
        try:
            s.port = int(data.get("port") or 0)
        except (TypeError, ValueError):
            s.port = 0
        return s


@dataclass
class Account:
    """A mailbox pair. ``secret`` is a password, or an OAuth2 access token."""

    user1: str = ""
    secret1: str = ""
    user2: str = ""
    secret2: str = ""


@dataclass
class Options:
    dry_run: bool = False
    just_folders: bool = False
    just_login: bool = False
    just_sizes: bool = False       # Only count messages and sizes (--justfoldersizes)
    automap: bool = True
    verify_certs: bool = True      # Check TLS certificates (imapsync does not by default)
    max_age_days: int = 0          # 0 = no limit
    min_age_days: int = 0          # 0 = no limit
    max_size_mb: int = 0           # Skip bigger messages; 0 = no limit
    only_folders: list[str] = field(default_factory=list)
    exclude: str = ""              # Regular expression, imapsync --exclude
    subfolder2: str = ""
    delete2: bool = False          # Remove on destination what is not on source
    delete1: bool = False          # Remove from source once copied (a "move")
    extra_args: str = ""

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d.pop("just_login", None)
        d.pop("just_sizes", None)
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Options":
        o = cls()
        for key, default in o.__dict__.items():
            if key in ("just_login", "just_sizes") or key not in data:
                continue
            value = data[key]
            if isinstance(default, bool):
                o.__dict__[key] = bool(value)
            elif isinstance(default, int):
                try:
                    o.__dict__[key] = max(0, int(value))
                except (TypeError, ValueError):
                    pass
            elif isinstance(default, list):
                if isinstance(value, list):
                    o.__dict__[key] = [str(v) for v in value if str(v).strip()]
            elif isinstance(value, str):
                o.__dict__[key] = value
        return o


class CommandError(ValueError):
    """The form is incomplete or inconsistent; message is user facing."""


@dataclass
class Command:
    args: list[str]
    env: dict[str, str]
    # Files holding OAuth2 tokens; the caller deletes them when the run ends.
    temp_files: list[str] = field(default_factory=list)

    def display(self) -> str:
        """Shell-like rendering safe to show or copy (no secrets inside)."""
        shown = []
        for a in self.args:
            if a in self.temp_files:
                shown.append("<token-file>")
            else:
                shown.append(a)
        return " ".join(shlex.quote(a) for a in shown)


def _side_args(n: int, server: Server, user: str) -> list[str]:
    provider = PROVIDERS.get(server.provider, PROVIDERS["custom"])
    args: list[str] = []
    if provider.flag:
        # imapsync knows these providers and sets host and tuned options itself.
        args.append(f"--{provider.flag}{n}")
        if provider.flag == "exchange":
            args += [f"--host{n}", server.host]
    else:
        args += [f"--host{n}", server.host]
    if server.port:
        args += [f"--port{n}", str(server.port)]
    if not provider.flag or provider.flag == "exchange":
        if server.security == SECURITY_SSL:
            args.append(f"--ssl{n}")
        elif server.security == SECURITY_STARTTLS:
            args += [f"--nossl{n}", f"--tls{n}"]
        else:
            args += [f"--nossl{n}", f"--notls{n}"]
    args += [f"--user{n}", user]
    return args


def validate(src: Server, dst: Server, account: Account) -> None:
    for label, server, user, secret in (
        ("Source", src, account.user1, account.secret1),
        ("Destination", dst, account.user2, account.secret2),
    ):
        provider = PROVIDERS.get(server.provider, PROVIDERS["custom"])
        if (not provider.flag or provider.flag == "exchange") and not server.host.strip():
            raise CommandError(f"{label} server address is empty.")
        if not user.strip():
            raise CommandError(f"{label} username is empty.")
        if not secret:
            what = "access token" if server.auth == AUTH_OAUTH2 else "password"
            raise CommandError(f"{label} {what} is empty for {user}.")
        if not 0 <= server.port <= 65535:
            raise CommandError(f"{label} port must be between 1 and 65535.")


def parse_extra_args(text: str) -> list[str]:
    if not text.strip():
        return []
    try:
        extra = shlex.split(text, posix=True)
    except ValueError as exc:
        raise CommandError(f"Extra arguments are not valid: {exc}") from exc
    for word in extra:
        if word.startswith(("--password", "--passfile")):
            raise CommandError("Do not put passwords in Extra arguments; "
                               "use the password fields instead.")
    return extra


def single_side(server: Server, user: str, secret: str) -> tuple[Server, Server, Account]:
    """Settings that log in to one account on both sides, to test it alone.

    imapsync always needs two accounts; with --justlogin, logging in twice
    to the same mailbox is read-only and checks just that account.
    """
    return server, server, Account(user, secret, user, secret)


def preflight(src: Server, dst: Server, account: Account, options: Options) -> None:
    """Raise CommandError when build_command would refuse these settings."""
    validate(Server(**src.to_dict()), Server(**dst.to_dict()),
             Account(account.user1.strip(), account.secret1,
                     account.user2.strip(), account.secret2))
    parse_extra_args(options.extra_args)


def build_command(engine: list[str], src: Server, dst: Server, account: Account,
                  options: Options, logdir: str | None = None,
                  tmpdir: str | None = None) -> Command:
    """Return the imapsync argv plus the environment that carries the secrets.

    Passwords travel in IMAPSYNC_PASSWORD1/2 so they never show up in the
    process list or in imapsync's own log of its command line.
    """
    src = Server(**src.to_dict())
    dst = Server(**dst.to_dict())
    src.host, dst.host = src.host.strip(), dst.host.strip()
    account = Account(account.user1.strip(), account.secret1,
                      account.user2.strip(), account.secret2)
    validate(src, dst, account)

    args = list(engine)
    env: dict[str, str] = {}
    temp_files: list[str] = []

    for n, server, user, secret in ((1, src, account.user1, account.secret1),
                                    (2, dst, account.user2, account.secret2)):
        args += _side_args(n, server, user)
        if options.verify_certs and (PROVIDERS.get(server.provider, PROVIDERS["custom"]).flag
                                     or server.security != SECURITY_NONE):
            args += [f"--sslargs{n}", "SSL_verify_mode=1"]
        if server.auth == AUTH_OAUTH2:
            # --oauthaccesstoken accepts a file name; keep the token off argv.
            path = _write_secret_file(secret, tmpdir)
            temp_files.append(path)
            args += [f"--oauthaccesstoken{n}", path]
        else:
            env[f"IMAPSYNC_PASSWORD{n}"] = secret

    if options.just_login:
        args.append("--justlogin")
    else:
        if options.just_sizes:
            args.append("--justfoldersizes")
        if options.dry_run:
            args.append("--dry")
        if options.just_folders:
            args.append("--justfolders")
        if options.automap:
            args.append("--automap")
        if options.max_age_days:
            args += ["--maxage", str(options.max_age_days)]
        if options.min_age_days:
            args += ["--minage", str(options.min_age_days)]
        for folder in options.only_folders:
            folder = folder.strip()
            if folder:
                args += ["--folderrec", folder]
        if options.exclude.strip():
            args += ["--exclude", options.exclude.strip()]
        if options.max_size_mb:
            args += ["--maxsize", str(options.max_size_mb * 1_000_000)]
        if options.subfolder2.strip():
            args += ["--subfolder2", options.subfolder2.strip()]
        if options.delete2 and not options.just_sizes:
            args.append("--delete2")
        if options.delete1 and not options.just_sizes:
            args.append("--delete1")

    if logdir:
        args += ["--logdir", logdir]
    if tmpdir:
        args += ["--tmpdir", tmpdir]

    args += parse_extra_args(options.extra_args)

    return Command(args=args, env=env, temp_files=temp_files)


def _write_secret_file(secret: str, tmpdir: str | None) -> str:
    import tempfile

    fd, path = tempfile.mkstemp(prefix="mm_token_", suffix=".txt", dir=tmpdir)
    try:
        os.write(fd, secret.strip().encode("utf-8") + b"\n")
    finally:
        os.close(fd)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


# --------------------------------------------------------------------------
# Locating the imapsync engine
# --------------------------------------------------------------------------

def _bundle_dirs() -> list[Path]:
    dirs = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(Path(meipass))
        exe_dir = Path(sys.executable).resolve().parent
        dirs += [exe_dir, exe_dir.parent / "Resources"]
    return dirs


def find_imapsync() -> list[str] | None:
    """Return the argv prefix that runs imapsync, or None when not found.

    Search order:
      1. $MAILMIGRATOR_IMAPSYNC (a binary, or the Perl script)
      2. The standalone binary bundled with the packaged app
      3. The imapsync script next to this GUI in the source tree (needs perl)
      4. imapsync on the PATH
    """
    exe_name = "imapsync.exe" if os.name == "nt" else "imapsync"

    override = os.environ.get("MAILMIGRATOR_IMAPSYNC")
    if override:
        cmd = _command_for(Path(override))
        if cmd:
            return cmd

    for base in _bundle_dirs():
        candidate = base / "imapsync-bin" / exe_name
        if candidate.is_file():
            return [str(candidate)]

    source_tree = Path(__file__).resolve().parents[2] / "imapsync"
    cmd = _command_for(source_tree)
    if cmd:
        return cmd

    on_path = shutil.which("imapsync")
    if on_path:
        cmd = _command_for(Path(on_path))
        if cmd:
            return cmd
    return None


def _command_for(path: Path) -> list[str] | None:
    if not path.is_file():
        return None
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return None
    if head.startswith(b"#!") and b"perl" in head:
        perl = shutil.which("perl")
        return [perl, str(path)] if perl else None
    return [str(path)]
