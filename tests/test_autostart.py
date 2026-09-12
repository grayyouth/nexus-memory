"""
Tests for core/autostart.py (Phase 3: daemon autostart at system boot).

All external effects (subprocess, HOME) are mocked or redirected to
tmp_path; no test touches the real scheduler.
"""
import plistlib

import pytest

from core import autostart


def _mk_run(mapping):
    """Build a _run replacement. mapping: cmd[0] -> (rc, stdout, stderr)."""
    calls = []

    def fake_run(cmd, input_text=None):
        calls.append((list(cmd), input_text))
        rc, out, err = mapping.get(cmd[0], (0, "", ""))
        return rc, out, err

    fake_run.calls = calls
    return fake_run


def _use_platform(monkeypatch, platform):
    monkeypatch.setattr(autostart.sys, "platform", platform)


def _use_home(monkeypatch, tmp_path):
    monkeypatch.setattr(autostart.Path, "home",
                        classmethod(lambda cls: tmp_path))


class TestBootCommand:
    def test_cli_server_start(self, tmp_path):
        cmd = autostart.boot_command(tmp_path, python_exe="py.exe")
        assert cmd[0] == "py.exe"
        assert cmd[1] == str(tmp_path / "nexus_cli.py")
        assert cmd[-2:] == ["server", "start"]


class TestWindowsArgs:
    def test_create_args(self):
        cmd = ["C:/Program Files/py/python.exe",
               "C:/My Proj/nexus_cli.py", "server", "start"]
        args = autostart.win_create_args("NexusDaemon", cmd)
        assert args[0] == "schtasks"
        assert args[args.index("/TN") + 1] == "NexusDaemon"
        assert args[args.index("/SC") + 1] == "ONLOGON"
        tr = args[args.index("/TR") + 1]
        assert tr.startswith('"') and tr.endswith('"')
        assert '"C:/Program Files/py/python.exe"' in tr
        assert '"C:/My Proj/nexus_cli.py"' in tr
        assert "server start" in tr

    def test_delete_and_query_args(self):
        assert autostart.win_delete_args("NexusDaemon") == [
            "schtasks", "/Delete", "/TN", "NexusDaemon", "/F"]
        assert autostart.win_query_args("NexusDaemon") == [
            "schtasks", "/Query", "/TN", "NexusDaemon"]

    def test_run_key_data_format(self):
        cmd = ["C:/Program Files/py/python.exe",
               "C:/My Proj/nexus_cli.py", "server", "start"]
        data = autostart.win_tr_value(cmd)
        assert data.startswith('"C:/Program Files/py/python.exe"')
        assert '"C:/My Proj/nexus_cli.py"' in data
        assert data.endswith("server start")


class _FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_winreg(existing=None):
    """Build a winreg-like namespace backed by a plain dict."""
    import types
    store = dict(existing or {})

    def open_key(*args):
        return _FakeKey()

    def query_value_ex(key, name):
        if name in store:
            return store[name], "REG_SZ"
        raise FileNotFoundError(name)

    def set_value_ex(key, name, reserved, typ, data):
        store[name] = data

    def delete_value(key, name):
        if name in store:
            del store[name]
        else:
            raise FileNotFoundError(name)

    ns = types.SimpleNamespace(
        HKEY_CURRENT_USER="HKCU", REG_SZ="REG_SZ",
        KEY_SET_VALUE="KEY_SET_VALUE",
        OpenKey=open_key, QueryValueEx=query_value_ex,
        SetValueEx=set_value_ex, DeleteValue=delete_value)
    return ns, store


class TestGenerators:
    def test_systemd_unit_content(self):
        content = autostart.systemd_unit_content(
            "NexusDaemon", ["python", "/p/nexus_cli.py", "server", "start"])
        assert "ExecStart=python /p/nexus_cli.py server start" in content
        assert "Type=oneshot" in content
        assert "WantedBy=default.target" in content

    def test_systemd_unit_quotes_spaces(self):
        content = autostart.systemd_unit_content(
            "T", ["/opt/my python/bin/python", "/p/nexus_cli.py",
                  "server", "start"])
        assert ('ExecStart="/opt/my python/bin/python" '
                "/p/nexus_cli.py server start") in content

    def test_cron_line(self, tmp_path):
        log = tmp_path / "autostart.log"
        line = autostart.cron_line(
            ["python", "/p/nexus_cli.py", "server", "start"], log)
        assert line.startswith("@reboot python /p/nexus_cli.py")
        assert str(log) in line and "2>&1" in line

    def test_plist_content_valid_xml(self, tmp_path):
        log = tmp_path / "a.log"
        content = autostart.launchagent_plist_content(
            "NexusDaemon", ["python", "/p/nexus_cli.py", "server", "start"],
            log)
        pl = plistlib.loads(content.encode("utf-8"))
        assert pl["Label"] == "NexusDaemon"
        assert pl["RunAtLoad"] is True
        assert pl["ProgramArguments"][1] == "/p/nexus_cli.py"
        assert str(log) in pl["StandardOutPath"]


class TestWindowsFlow:
    def test_enable_schtasks_ok(self, monkeypatch):
        _use_platform(monkeypatch, "win32")
        fake = _mk_run({"schtasks": (0, "SUCCESS: The task was created.", "")})
        monkeypatch.setattr(autostart, "_run", fake)
        rep = autostart.enable("E:/p")
        assert rep["ok"] is True and rep["backend"] == "schtasks"
        assert "/Create" in fake.calls[0][0]

    def test_enable_falls_back_to_registry(self, monkeypatch):
        _use_platform(monkeypatch, "win32")
        monkeypatch.setattr(autostart, "_run",
                            _mk_run({"schtasks": (1, "", "Access is denied.")}))
        fake, store = _fake_winreg()
        monkeypatch.setattr(autostart, "winreg", fake)
        rep = autostart.enable("E:/p")
        assert rep["ok"] is True and rep["backend"] == "registry"
        assert "nexus_cli.py" in store["NexusDaemon"]
        assert "server start" in store["NexusDaemon"]

    def test_enable_failure_reports_detail(self, monkeypatch):
        _use_platform(monkeypatch, "win32")
        monkeypatch.setattr(autostart, "_run",
                            _mk_run({"schtasks": (1, "", "Access denied")}))
        monkeypatch.setattr(autostart, "winreg", None)  # no registry access
        rep = autostart.enable("E:/p")
        assert rep["ok"] is False
        assert "write failed" in rep["detail"]
        assert "Access denied" in rep["detail"]

    def test_win_run_get_set_remove_roundtrip(self, monkeypatch):
        fake, store = _fake_winreg()
        monkeypatch.setattr(autostart, "winreg", fake)
        assert autostart.win_run_get("NexusDaemon") is None
        assert autostart.win_run_set("NexusDaemon", "data here") is True
        assert autostart.win_run_get("NexusDaemon") == "data here"
        assert autostart.win_run_remove("NexusDaemon") is True
        assert autostart.win_run_get("NexusDaemon") is None
        assert autostart.win_run_remove("NexusDaemon") is False

    def test_status(self, monkeypatch):
        _use_platform(monkeypatch, "win32")
        monkeypatch.setattr(autostart, "winreg",
                            _fake_winreg({"NexusDaemon": "x"})[0])
        rep = autostart.status()
        assert rep["enabled"] is True and rep["backend"] == "registry"
        monkeypatch.setattr(autostart, "winreg", _fake_winreg()[0])
        monkeypatch.setattr(autostart, "_run",
                            _mk_run({"schtasks": (0, "info", "")}))
        rep = autostart.status()
        assert rep["enabled"] is True and rep["backend"] == "schtasks"
        monkeypatch.setattr(autostart, "_run",
                            _mk_run({"schtasks": (1, "", "ERROR")}))
        rep = autostart.status()
        assert rep["enabled"] is False

    def test_disable_removes_both_entries(self, monkeypatch):
        _use_platform(monkeypatch, "win32")
        fake, store = _fake_winreg({"NexusDaemon": "x"})
        monkeypatch.setattr(autostart, "winreg", fake)
        monkeypatch.setattr(autostart, "_run",
                            _mk_run({"schtasks": (0, "", "")}))
        rep = autostart.disable()
        assert rep["ok"] is True and rep["enabled"] is False
        assert "NexusDaemon" not in store


class TestSystemdFlow:
    def test_enable_writes_unit(self, monkeypatch, tmp_path):
        _use_platform(monkeypatch, "linux")
        _use_home(monkeypatch, tmp_path)
        fake = _mk_run({"systemctl": (0, "", "")})
        monkeypatch.setattr(autostart, "_run", fake)
        rep = autostart.enable(tmp_path / "proj", home=tmp_path)
        assert rep["ok"] is True and rep["backend"] == "systemd"
        unit = tmp_path / ".config" / "systemd" / "user" / "NexusDaemon.service"
        content = unit.read_text(encoding="utf-8")
        assert "nexus_cli.py" in content and "server start" in content
        cmds = [c[0] for c in fake.calls]
        assert ["systemctl", "--user", "daemon-reload"] in cmds
        assert ["systemctl", "--user", "enable", "NexusDaemon.service"] in cmds

    def test_enable_cron_fallback_when_no_systemd(self, monkeypatch, tmp_path):
        _use_platform(monkeypatch, "linux")
        fake = _mk_run({"systemctl": (127, "", "not found"),
                        "crontab": (0, "", "")})
        monkeypatch.setattr(autostart, "_run", fake)
        rep = autostart.enable(tmp_path, home=tmp_path)
        assert rep["ok"] is True and rep["backend"] == "cron"
        inputs = [inp for cmd, inp in fake.calls
                  if cmd[:2] == ["crontab", "-"]]
        assert inputs and "@reboot" in inputs[0]
        assert "nexus_cli.py" in inputs[0]

    def test_disable_removes_unit_and_cron_keeps_other_lines(
            self, monkeypatch, tmp_path):
        _use_platform(monkeypatch, "linux")
        _use_home(monkeypatch, tmp_path)
        unit = autostart.systemd_unit_path("NexusDaemon", tmp_path)
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("unit", encoding="utf-8")
        cron = ("0 5 * * * backup-job\n"
                "@reboot python /p/nexus_cli.py server start >> /l.log 2>&1\n")
        fake = _mk_run({"systemctl": (0, "", ""),
                        "crontab": (0, cron, "")})
        monkeypatch.setattr(autostart, "_run", fake)
        rep = autostart.disable(home=tmp_path)
        assert rep["ok"] is True
        assert not unit.exists()
        inputs = [inp for cmd, inp in fake.calls
                  if cmd[:2] == ["crontab", "-"]]
        assert inputs
        assert "nexus_cli.py" not in inputs[0]
        assert "backup-job" in inputs[0]  # unrelated crontab lines kept

    def test_status(self, monkeypatch, tmp_path):
        _use_platform(monkeypatch, "linux")
        _use_home(monkeypatch, tmp_path)
        monkeypatch.setattr(autostart, "_run",
                            _mk_run({"systemctl": (0, "", ""),
                                     "crontab": (1, "", "")}))
        assert autostart.status(home=tmp_path)["enabled"] is True
        monkeypatch.setattr(autostart, "_run",
                            _mk_run({"systemctl": (1, "", ""),
                                     "crontab": (1, "", "")}))
        assert autostart.status(home=tmp_path)["enabled"] is False


class TestLaunchdFlow:
    def test_enable_status_disable(self, monkeypatch, tmp_path):
        _use_platform(monkeypatch, "darwin")
        fake = _mk_run({})
        monkeypatch.setattr(autostart, "_run", fake)
        rep = autostart.enable(tmp_path, home=tmp_path)
        assert rep["ok"] is True and rep["backend"] == "launchd"
        plist = tmp_path / "Library" / "LaunchAgents" / "NexusDaemon.plist"
        assert plist.exists()
        pl = plistlib.loads(plist.read_bytes())
        assert pl["RunAtLoad"] is True
        assert pl["ProgramArguments"][-2:] == ["server", "start"]
        assert autostart.status(home=tmp_path)["enabled"] is True
        rep = autostart.disable(home=tmp_path)
        assert rep["ok"] is True
        assert not plist.exists()

