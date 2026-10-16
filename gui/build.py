#!/usr/bin/env python3
"""Build Mail Migrator for the current platform (Windows, macOS or Linux).

    python build.py imapsync   # standalone imapsync binary -> imapsync-bin/
    python build.py app        # desktop app (PyInstaller)  -> dist/
    python build.py package    # zip / tar.gz / dmg          -> release/
    python build.py all        # the three steps above

The imapsync step needs Perl with imapsync's dependencies and PAR::Packer:
    cpanm --installdeps --with-develop ..        (run inside gui/)
The app step needs:  pip install -r requirements-build.txt
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAPSYNC_SCRIPT = HERE.parent / "imapsync"
BIN_DIR = HERE / "imapsync-bin"
DIST = HERE / "dist"
RELEASE = HERE / "release"
APP_NAME = "Mail Migrator"
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"

# Modules imapsync loads at run time with require(), which pp cannot see by
# scanning the script. Missing optional ones are skipped with a warning.
RUNTIME_MODULES = [
    ("Authen::NTLM", True),
    ("Digest::HMAC_MD5", True),
    ("Encode::Byte", True),
    ("HTML::Entities", True),
    ("IO::Socket::INET6", False),
    ("IO::Socket::IP", False),
    ("IO::Socket::SSL", True),
    ("JSON", True),
    ("JSON::WebToken", True),
    ("JSON::WebToken::Crypt::RSA", True),
    ("Crypt::OpenSSL::RSA", True),
    ("Crypt::OpenSSL::PKCS12", False),
    ("LWP::UserAgent", True),
    ("LWP::Protocol::https", False),
    ("Mozilla::CA", True),  # CA bundle for certificate checks
    ("Net::Ping", True),
    ("Net::SSLeay", True),
]
# Test::More (used by imapsync's built-in self tests) loads Test2 plugins lazily.
RUNTIME_NAMESPACES = ["Test2::**", "Test::Builder::**"]


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def perl_has(module: str) -> bool:
    return subprocess.run(["perl", f"-M{module}", "-e1"], capture_output=True).returncode == 0


def windows_dlls() -> list[Path]:
    """Strawberry Perl DLLs (OpenSSL, zlib, gcc runtime...) needed by XS modules.

    pp packs perl's own DLL but not the libraries that compiled modules such as
    Net::SSLeay depend on, so walk the import tables with objdump.
    """
    perl = Path(subprocess.check_output(["perl", "-e", "print $^X"], text=True))
    cbin = perl.parent.parent.parent / "c" / "bin"
    objdump = cbin / "objdump.exe"
    if not objdump.exists():
        objdump = Path(shutil.which("objdump") or "objdump")
    inc = subprocess.check_output(["perl", "-e", 'print join "\\n", grep { -d } @INC'],
                                  text=True).split("\n")

    def imports(dll: Path) -> set[str]:
        out = subprocess.run([str(objdump), "-p", str(dll)], capture_output=True,
                             text=True).stdout
        return {line.split(":", 1)[1].strip().lower()
                for line in out.splitlines() if "DLL Name:" in line}

    pending: set[str] = set()
    for libdir in inc:
        for xs in Path(libdir).glob("auto/**/*.dll"):
            pending |= imports(xs)
    available = {p.name.lower(): p for p in cbin.glob("*.dll")}
    found: dict[str, Path] = {}
    while pending:
        name = pending.pop()
        if name in found or name not in available:
            continue  # Windows system DLL, or already handled
        found[name] = available[name]
        pending |= imports(available[name])
    return sorted(found.values())


def standalone_env() -> dict[str, str]:
    """Environment without Perl, to prove the binary is self-contained."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("PERL")}
    if IS_WIN:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        env["PATH"] = os.pathsep.join([root + r"\System32", root])
    else:
        env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    return env


def build_imapsync() -> Path:
    pp = shutil.which("pp") or shutil.which("pp.bat")
    if not pp:
        sys.exit("pp (PAR::Packer) was not found. Install it with: cpanm PAR::Packer")
    BIN_DIR.mkdir(exist_ok=True)
    out = BIN_DIR / ("imapsync.exe" if IS_WIN else "imapsync")
    cmd = [pp, "-o", out]
    for module, required in RUNTIME_MODULES:
        if perl_has(module):
            cmd += ["-M", module]
        elif required:
            sys.exit(f"Perl module {module} is missing. Run: cpanm --installdeps ..")
        else:
            print(f"note: optional module {module} not installed, skipping")
    for pattern in RUNTIME_NAMESPACES:
        cmd += ["-M", pattern]
    if IS_WIN:
        for dll in windows_dlls():
            cmd += ["--link", dll]
    cmd.append(IMAPSYNC_SCRIPT)
    run(cmd)
    # Smoke test: the binary must start and report its version.
    version = subprocess.run([str(out), "--version"], capture_output=True, text=True,
                             timeout=300, env=standalone_env())
    if version.returncode != 0:
        print(version.stdout, version.stderr)
        sys.exit("The imapsync binary does not run.")
    print(f"Built {out} (imapsync {version.stdout.strip()})")
    shutil.copy(HERE.parent / "LICENSE", BIN_DIR / "LICENSE-imapsync.txt")
    return out


def build_app() -> None:
    if not BIN_DIR.joinpath("imapsync.exe" if IS_WIN else "imapsync").exists():
        print("warning: imapsync-bin/ is empty; the app will need imapsync installed "
              "separately. Run 'python build.py imapsync' first to bundle it.")
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         HERE / "mailmigrator.spec"], cwd=HERE)
    if IS_MAC:
        app = DIST / f"{APP_NAME}.app"
        target = app / "Contents" / "Resources" / "imapsync-bin"
    else:
        app = DIST / APP_NAME
        target = app / "imapsync-bin"
    if BIN_DIR.is_dir() and any(BIN_DIR.iterdir()):
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(BIN_DIR, target)
    if IS_MAC:
        # Adding files changed the bundle, so seal it again (ad-hoc signature).
        run(["codesign", "--force", "--sign", "-", app])
        run(["codesign", "--verify", "--verbose", app])


def arch_name() -> str:
    machine = platform.machine().lower()
    return {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(
        machine, machine)


def package() -> Path:
    from mailmigrator import __version__

    RELEASE.mkdir(exist_ok=True)
    stem = f"MailMigrator-{__version__}"
    if IS_WIN:
        target = RELEASE / f"{stem}-windows-{arch_name()}.zip"
        src = DIST / APP_NAME
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in src.rglob("*"):
                zf.write(path, Path(APP_NAME) / path.relative_to(src))
    elif IS_MAC:
        target = RELEASE / f"{stem}-macos-{arch_name()}.dmg"
        target.unlink(missing_ok=True)
        staging = DIST / "dmg"
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        run(["ditto", DIST / f"{APP_NAME}.app", staging / f"{APP_NAME}.app"])
        os.symlink("/Applications", staging / "Applications")
        run(["hdiutil", "create", "-volname", APP_NAME, "-srcfolder", staging,
             "-ov", "-format", "UDZO", target])
        shutil.rmtree(staging, ignore_errors=True)
    else:
        target = RELEASE / f"{stem}-linux-{arch_name()}.tar.gz"
        with tarfile.open(target, "w:gz") as tf:
            tf.add(DIST / APP_NAME, arcname="MailMigrator")
    print(f"Created {target}")
    return target


def main() -> None:
    steps = sys.argv[1:] or ["all"]
    for step in steps:
        if step in ("imapsync", "all"):
            build_imapsync()
        if step in ("app", "all"):
            build_app()
        if step in ("package", "all"):
            package()
        if step not in ("imapsync", "app", "package", "all"):
            sys.exit(__doc__)


if __name__ == "__main__":
    sys.path.insert(0, str(HERE))
    main()
