"""
Nexus Daemon Autostart at system boot (Фаза 3).

Проблема: демон (``nexus_http_server.py``) нужно запускать вручную
(``nexus server start``). После перезагрузки машины его нет, пока агент
или человек не вспомнит про него.

Решение: boot-запись, которая при входе пользователя выполняет
``python nexus_cli.py server start`` — тот же идемпотентный путь, что и
ручной запуск (детач-процесс, pid-file, автогенерация токена,
health-поллинг).

Бэкенды:
    - Windows: ``schtasks`` ONLOGON (если хватает прав), fallback:
               per-user ключ автозагрузки HKCU Run (без админа; запись
               через stdlib ``winreg``, без shell-кавычек)
    - Linux:   systemd user unit (``~/.config/systemd/user``),
               fallback: cron ``@reboot`` (если systemd недоступен)
    - macOS:   LaunchAgent (``~/Library/LaunchAgents``)

Всё — чистый stdlib; subprocess вынесен в ``_run()`` для тестируемости.

Usage:
    from core.autostart import enable, disable, status
    rep = enable(project_root)   # {"ok": True, "backend": "schtasks", ...}
    rep = status()               # {"enabled": True, "backend": "..."}
    rep = disable()              # {"ok": True, "enabled": False, ...}
"""

import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import winreg  # Windows only; replaced by a fake in tests
except ImportError:  # pragma: no cover - non-Windows hosts
    winreg = None

logger = logging.getLogger(__name__)

TASK_NAME = "NexusDaemon"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def boot_command(project_root: Path, python_exe: Optional[str] = None) -> List[str]:
    """Command the boot entry runs: CLI ``server start`` (idempotent spawn).

    Reuses the manual-start code path: detached daemon, pid-file, token
    auto-generation, health polling. Safe to run repeatedly at login.
    """
    exe = python_exe or sys.executable
    return [str(exe), str(Path(project_root) / "nexus_cli.py"),
            "server", "start"]


def _run(cmd: List[str], input_text: Optional[str] = None) -> Tuple[int, str, str]:
    """Run a command; never raises. Returns (returncode, stdout, stderr)."""
    try:
        proc = subprocess.run(cmd, input=input_text, capture_output=True,
                              text=True)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except OSError as e:
        return 1, "", str(e)


def _first_line(*texts: str) -> str:
    for t in texts:
        if t and t.strip():
            return t.strip().splitlines()[0]
    return ""


# --------------------------------------------------------------------------
# Windows (schtasks)
# --------------------------------------------------------------------------

def _win_quote(arg: str) -> str:
    """Quote an argument for the /TR command string (cmd-style)."""
    if arg and (" " in arg or "\t" in arg or '"' in arg):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def win_tr_value(cmd: List[str]) -> str:
    """The /TR value: full boot command as one string."""
    return " ".join(_win_quote(a) for a in cmd)


def win_create_args(task_name: str, cmd: List[str]) -> List[str]:
    """schtasks argv to create the ONLOGON task (current user, no admin)."""
    return ["schtasks", "/Create", "/TN", task_name,
            "/TR", '"' + win_tr_value(cmd) + '"',
            "/SC", "ONLOGON", "/F"]


def win_delete_args(task_name: str) -> List[str]:
    return ["schtasks", "/Delete", "/TN", task_name, "/F"]


def win_query_args(task_name: str) -> List[str]:
    return ["schtasks", "/Query", "/TN", task_name]


_WIN_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def win_run_get(task_name: str) -> Optional[str]:
    """Read the HKCU Run value; None when absent (stdlib winreg, no reg.exe).

    reg.exe mangles quoted /d data (doubling quotes), so we write the value
    directly via winreg — exact bytes, no shell parsing involved.
    """
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, task_name)
            return str(value)
    except OSError:
        return None


def win_run_set(task_name: str, data: str) -> bool:
    """Write the HKCU Run value directly (no admin, no quoting issues)."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, task_name, 0, winreg.REG_SZ, data)
            return True
    except OSError:
        return False


def win_run_remove(task_name: str) -> bool:
    """Delete the HKCU Run value; True when it existed."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, task_name)
            return True
    except OSError:
        return False


# --------------------------------------------------------------------------
# Linux (systemd user unit, fallback cron @reboot)
# --------------------------------------------------------------------------

def systemd_unit_path(task_name: str, home: Optional[Path] = None) -> Path:
    return (Path(home) if home else Path.home()) / ".config" / "systemd" / \
        "user" / f"{task_name}.service"


def _posix_quote(arg: str) -> str:
    """POSIX shell quoting (shared by systemd ExecStart and cron)."""
    if arg and (" " in arg or '"' in arg or "\t" in arg):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def systemd_unit_content(task_name: str, cmd: List[str]) -> str:
    exec_start = " ".join(_posix_quote(a) for a in cmd)
    return (
        "[Unit]\n"
        f"Description=Nexus memory daemon (autostart: {task_name})\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={exec_start}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def cron_line(cmd: List[str], log_path: Path) -> str:
    quoted = " ".join(_posix_quote(a) for a in cmd)
    return f"@reboot {quoted} >> {log_path} 2>&1"


def _is_nexus_cron(line: str) -> bool:
    return "@reboot" in line and "nexus_cli.py" in line


# --------------------------------------------------------------------------
# macOS (LaunchAgent)
# --------------------------------------------------------------------------

def launchagent_plist_path(task_name: str,
                           home: Optional[Path] = None) -> Path:
    return (Path(home) if home else Path.home()) / "Library" / \
        "LaunchAgents" / f"{task_name}.plist"


def _xml_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def launchagent_plist_content(task_name: str, cmd: List[str],
                              log_path: Path) -> str:
    args = "\n".join(
        f"        <string>{_xml_escape(a)}</string>" for a in cmd)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "    <key>Label</key>\n"
        f"    <string>{_xml_escape(task_name)}</string>\n"
        "    <key>ProgramArguments</key>\n"
        "    <array>\n"
        f"{args}\n"
        "    </array>\n"
        "    <key>RunAtLoad</key>\n"
        "    <true/>\n"
        "    <key>StandardOutPath</key>\n"
        f"    <string>{_xml_escape(str(log_path))}</string>\n"
        "    <key>StandardErrorPath</key>\n"
        f"    <string>{_xml_escape(str(log_path))}</string>\n"
        "</dict>\n"
        "</plist>\n"
    )


# --------------------------------------------------------------------------
# public API: enable / disable / status
# --------------------------------------------------------------------------

def enable(project_root: Path, task_name: str = TASK_NAME,
           python_exe: Optional[str] = None,
           home: Optional[Path] = None) -> dict:
    """Install the boot entry. Returns {"ok", "backend", "enabled", "detail"}."""
    cmd = boot_command(project_root, python_exe)

    if sys.platform == "win32":
        # Preferred: a proper scheduled task; on admin-locked machines it is
        # denied, so fall back to the per-user HKCU Run key (no admin).
        rc, out, err = _run(win_create_args(task_name, cmd))
        if rc == 0:
            return {"ok": True, "backend": "schtasks", "enabled": True,
                    "detail": _first_line(out)}
        run_key = _WIN_RUN_KEY + "\\" + task_name
        if win_run_set(task_name, win_tr_value(cmd)):
            return {"ok": True, "backend": "registry", "enabled": True,
                    "detail": run_key}
        return {"ok": False, "backend": "registry", "enabled": False,
                "detail": f"{run_key} write failed; schtasks: "
                          f"{_first_line(err)}"}

    if sys.platform == "darwin":
        plist = launchagent_plist_path(task_name, home)
        plist.parent.mkdir(parents=True, exist_ok=True)
        log = Path(project_root) / "nexus_store" / "_logs" / "autostart.log"
        plist.write_text(launchagent_plist_content(task_name, cmd, log),
                         encoding="utf-8")
        # best effort: loaded now; the plist alone suffices at next login
        rc, _, _ = _run(["launchctl", "load", str(plist)])
        detail = str(plist) if rc == 0 else f"{plist} (launchctl rc={rc})"
        return {"ok": True, "backend": "launchd", "enabled": True,
                "detail": detail}

    # Linux / POSIX: systemd user unit first, cron @reboot fallback
    unit = systemd_unit_path(task_name, home)
    try:
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text(systemd_unit_content(task_name, cmd), encoding="utf-8")
    except OSError as e:
        return {"ok": False, "backend": "systemd", "enabled": False,
                "detail": str(e)}

    rc, _, _ = _run(["systemctl", "--user", "daemon-reload"])
    if rc == 0:
        rc2, out2, err2 = _run(["systemctl", "--user", "enable",
                                f"{task_name}.service"])
        if rc2 == 0:
            return {"ok": True, "backend": "systemd", "enabled": True,
                    "detail": str(unit)}
        detail = _first_line(out2, err2)
    else:
        detail = "systemctl --user unavailable"

    log = Path(project_root) / "nexus_store" / "_logs" / "autostart.log"
    return _cron_enable(cmd, log, detail)


def _cron_enable(cmd: List[str], log_path: Path,
                 detail: str = "") -> dict:
    line = cron_line(cmd, log_path)
    rc, out, _ = _run(["crontab", "-l"])
    existing = out if rc == 0 else ""
    if any(_is_nexus_cron(l) for l in existing.splitlines()):
        return {"ok": True, "backend": "cron", "enabled": True,
                "detail": "already in crontab"}
    new = (existing.rstrip("\n") + "\n" if existing.strip() else "") + \
        line + "\n"
    rc2, _, err2 = _run(["crontab", "-"], input_text=new)
    if rc2 == 0:
        d = f"{detail}; crontab: {line}" if detail else line
        return {"ok": True, "backend": "cron", "enabled": True, "detail": d}
    return {"ok": False, "backend": "cron", "enabled": False,
            "detail": _first_line(err2, detail)}


def disable(task_name: str = TASK_NAME,
            home: Optional[Path] = None) -> dict:
    """Remove the boot entry. Returns {"ok", "backend", "enabled", "detail"}."""
    if sys.platform == "win32":
        # remove both possible entries; ok if at least one of them existed
        removed = win_run_remove(task_name)
        rc2, out2, err2 = _run(win_delete_args(task_name))
        ok = removed or rc2 == 0
        return {"ok": ok, "backend": "registry+schtasks", "enabled": False,
                "detail": "" if ok else _first_line(err2, out2)}

    if sys.platform == "darwin":
        plist = launchagent_plist_path(task_name, home)
        _run(["launchctl", "unload", str(plist)])  # best effort
        existed = plist.exists()
        if existed:
            plist.unlink()
        return {"ok": True, "backend": "launchd", "enabled": False,
                "detail": str(plist) if existed else "no plist found"}

    # Linux / POSIX: systemd + cron cleanup (whichever was used)
    unit = systemd_unit_path(task_name, home)
    removed_unit = False
    if unit.exists():
        _run(["systemctl", "--user", "disable", f"{task_name}.service"])
        _run(["systemctl", "--user", "daemon-reload"])
        try:
            unit.unlink()
            removed_unit = True
        except OSError as e:
            return {"ok": False, "backend": "systemd", "enabled": False,
                    "detail": str(e)}

    rc, out, _ = _run(["crontab", "-l"])
    removed_cron = False
    if rc == 0 and any(_is_nexus_cron(l) for l in out.splitlines()):
        kept = "\n".join(l for l in out.splitlines() if not _is_nexus_cron(l))
        rc2, _, _ = _run(["crontab", "-"],
                         input_text=kept + "\n" if kept.strip() else "")
        removed_cron = rc2 == 0

    if removed_unit or removed_cron:
        return {"ok": True, "backend": "systemd+cron", "enabled": False,
                "detail": str(unit) if removed_unit else "removed from crontab"}
    return {"ok": True, "backend": "systemd+cron", "enabled": False,
            "detail": "no boot entry found"}


def status(task_name: str = TASK_NAME,
           home: Optional[Path] = None) -> dict:
    """Check whether the boot entry exists."""
    if sys.platform == "win32":
        run_key = _WIN_RUN_KEY + "\\" + task_name
        if win_run_get(task_name) is not None:
            return {"enabled": True, "backend": "registry",
                    "detail": run_key}
        rc2, _, _ = _run(win_query_args(task_name))
        if rc2 == 0:
            return {"enabled": True, "backend": "schtasks",
                    "detail": task_name}
        return {"enabled": False, "backend": "registry+schtasks",
                "detail": "no boot entry"}

    if sys.platform == "darwin":
        plist = launchagent_plist_path(task_name, home)
        return {"enabled": plist.exists(), "backend": "launchd",
                "detail": str(plist) if plist.exists() else "no plist found"}

    unit = systemd_unit_path(task_name, home)
    rc, _, _ = _run(["systemctl", "--user", "is-enabled",
                     f"{task_name}.service"])
    if rc == 0:
        return {"enabled": True, "backend": "systemd", "detail": str(unit)}
    rc2, out2, _ = _run(["crontab", "-l"])
    if rc2 == 0 and any(_is_nexus_cron(l) for l in out2.splitlines()):
        return {"enabled": True, "backend": "cron",
                "detail": "crontab @reboot"}
    return {"enabled": False, "backend": "systemd/cron",
            "detail": "no boot entry"}


