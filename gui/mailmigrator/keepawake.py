"""Keep the computer (and this app) awake while a migration runs.

macOS: without this, App Nap throttles the app when its window is hidden or
minimized, so imapsync's output stops being read and the migration stalls;
idle sleep would also cut the network. Windows and Linux: prevent idle sleep.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import subprocess
import sys

_state: dict = {"active": False, "token": None}


def _mac_begin():
    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.objc_getClass.argtypes = [ctypes.c_char_p]
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.sel_registerName.argtypes = [ctypes.c_char_p]
    send = objc.objc_msgSend

    def msg(restype, argtypes, *args):
        send.restype = restype
        send.argtypes = argtypes
        return send(*args)

    sel = objc.sel_registerName
    info = msg(ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p],
               objc.objc_getClass(b"NSProcessInfo"), sel(b"processInfo"))
    reason = msg(ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p],
                 objc.objc_getClass(b"NSString"), sel(b"stringWithUTF8String:"),
                 b"Migrating mail")
    # NSActivityUserInitiated: no App Nap, no idle system sleep.
    activity = msg(ctypes.c_void_p,
                   [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_void_p],
                   info, sel(b"beginActivityWithOptions:reason:"), 0x00FFFFFF | (1 << 20),
                   reason)
    msg(ctypes.c_void_p, [ctypes.c_void_p, ctypes.c_void_p], activity, sel(b"retain"))

    def end():
        msg(None, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], info,
            sel(b"endActivity:"), activity)
        msg(None, [ctypes.c_void_p, ctypes.c_void_p], activity, sel(b"release"))
    return end


def _windows_begin():
    es_continuous, es_system_required = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)

    def end():
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous)
    return end


def _linux_begin():
    try:
        proc = subprocess.Popen(["systemd-inhibit", "--what=idle:sleep", "--who=Mail Migrator",
                                 "--why=Migrating mail", "sleep", "infinity"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return lambda: None
    return proc.terminate


def set_active(active: bool) -> None:
    """Idempotent: call with the current 'is anything running' state."""
    if active == _state["active"]:
        return
    try:
        if active:
            begin = (_mac_begin if sys.platform == "darwin" else
                     _windows_begin if sys.platform == "win32" else _linux_begin)
            _state["token"] = begin()
        elif _state["token"]:
            _state["token"]()
            _state["token"] = None
    except Exception:  # never let power management break a migration
        _state["token"] = None
    _state["active"] = active
