import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from hacklab import devtools


@pytest.fixture
def lab(tmp_path):
    project = tmp_path / "workspace/project"
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    return devtools.DevelopmentTools(project, tmp_path / "state")


@pytest.fixture
def manifest(lab, tmp_path):
    binary = tmp_path / "fake-tool"
    binary.write_text("#!/bin/sh\nexit 3\n")
    binary.chmod(0o700)
    return {
        "version": 1,
        "project": str(lab.project),
        "state_dir": str(lab.state),
        "tools": {"ichnora": str(binary), "ast-index": str(binary)},
        "scope": {"project_id": "prj_test", "worktree": "main"},
    }


def test_scope_and_state_are_canonical_and_outside_workspace(lab, monkeypatch):
    nested = lab.project / "src"
    nested.mkdir()
    assert devtools.project_root(nested) == lab.project
    with pytest.raises(devtools.DevtoolsError, match="outside"):
        devtools.state_directory(lab.project, lab.project / ".state")
    with pytest.raises(devtools.DevtoolsError, match="outside"):
        devtools.state_directory(lab.project, lab.project.parent / "state")
    assert devtools.state_directory(lab.project, lab.state) == lab.state
    monkeypatch.setenv("XDG_STATE_HOME", str(lab.state))
    assert devtools.state_directory(lab.project).is_relative_to(lab.state / "hacklab")


def test_status_does_not_create_or_start_anything(lab):
    assert lab.status() == {"configured": False, "running": False}
    assert not lab.state.exists()


def test_existing_state_files_are_preserved(lab):
    lab.state.mkdir(mode=0o755)
    source = lab.state / "unrelated"
    source.write_text("keep")
    mode_before = lab.state.stat().st_mode
    with pytest.raises(devtools.DevtoolsError, match="existing files"):
        lab.up()
    assert source.read_text() == "keep"
    assert lab.state.stat().st_mode == mode_before


def test_missing_tools_can_be_retried_after_bootstrap_lock(lab, monkeypatch):
    monkeypatch.setattr(devtools.shutil, "which", lambda _: None)
    for _ in range(2):
        with pytest.raises(devtools.DevtoolsError, match="Ichnora and AST"):
            lab.up()
    assert set(path.name for path in lab.state.iterdir()) == {"lock"}


def test_both_mcp_files_preserve_existing_servers_and_venv_executable(lab, tmp_path, monkeypatch):
    local = lab.project / ".mcp.json"
    local.write_text(json.dumps({"mcpServers": {"existing": {"command": "keep"}}, "custom": 1}))
    bob = lab.project / ".bob/mcp.json"
    bob.parent.mkdir()
    bob.write_text(json.dumps({"mcpServers": {"bob-other": {"command": "keep-too"}}}))
    venv = tmp_path / "venv/bin/python"
    venv.parent.mkdir(parents=True)
    venv.symlink_to(sys.executable)
    monkeypatch.setattr(devtools.sys, "executable", str(venv))
    plan = lab._mcp_plan()
    for _, config in plan:
        for name in devtools.SERVER_NAMES.values():
            entry = config["mcpServers"][name]
            assert entry["command"] == str(venv)
            assert "admin.sock" not in json.dumps(entry)
            assert str(lab.project) in entry["args"]
    assert plan[0][1]["custom"] == 1
    assert "existing" in plan[0][1]["mcpServers"]
    assert "bob-other" in plan[1][1]["mcpServers"]


def test_second_mcp_conflict_does_not_write_first_or_start_service(lab, monkeypatch):
    original = '{"mcpServers":{"existing":{"command":"keep"}}}'
    generic = lab.project / ".mcp.json"
    generic.write_text(original)
    bob = lab.project / ".bob/mcp.json"
    bob.parent.mkdir()
    bob.write_text('{"mcpServers":{"hacklab-memory":{"command":"different"}}}')
    monkeypatch.setattr(lab, "_up", lambda: pytest.fail("Must preflight all clients first"))
    with pytest.raises(devtools.DevtoolsError, match="conflicts"):
        lab.setup()
    assert generic.read_text() == original
    assert not lab.state.exists()


def test_setup_verifies_enrollment_and_is_idempotent(lab, manifest, monkeypatch):
    calls = []

    def run(command, *args, **kwargs):
        calls.append(command)
        if "enroll" in command:
            return {
                "ok": True,
                "result": {
                    "id": "prj_test",
                    "collect": False,
                    "recall": False,
                    "roots": [{"path": str(lab.project), "worktree": "main"}],
                },
            }
        return {"root": str(lab.project), "indexed": True, "generation": 1}

    monkeypatch.setattr(devtools, "_run_json", run)
    monkeypatch.setattr(lab, "_up", lambda: dict(manifest))
    monkeypatch.setattr(lab, "_memory_status", lambda _: {"result": {"scope": manifest["scope"]}})
    first = lab.setup()
    contents = [(lab.project / p).read_bytes() for p in first["mcp_files"]]
    lab.setup()
    assert contents == [(lab.project / p).read_bytes() for p in first["mcp_files"]]
    assert all("--collect" not in c and "--recall" not in c for c in calls)
    assert first["collection"] is first["automatic_recall"] is False


def test_wrong_returned_scope_never_writes_mcp_files(lab, manifest, monkeypatch):
    monkeypatch.setattr(lab, "_up", lambda: dict(manifest))
    monkeypatch.setattr(
        devtools,
        "_run_json",
        lambda *a, **k: {
            "result": {"id": "prj_test", "roots": [{"path": str(lab.project), "worktree": "main"}]}
        },
    )
    monkeypatch.setattr(
        lab,
        "_memory_status",
        lambda _: {"result": {"scope": {"project_id": "prj_foreign", "worktree": "main"}}},
    )
    with pytest.raises(devtools.DevtoolsError, match="different project scope"):
        lab.setup()
    assert not (lab.project / ".mcp.json").exists()
    assert not (lab.project / ".bob/mcp.json").exists()


def test_ast_index_uses_explicit_exclusions_and_clean_environment(lab, manifest, monkeypatch):
    monkeypatch.setenv("AST_INDEX_DB", "/foreign/index.sqlite")
    monkeypatch.setenv("AST_INDEX_ROOT", "/foreign/root")
    monkeypatch.setenv("SMALL_MODEL_API_KEY", "private-test-value")
    observed = {}

    def run(command, project, **kwargs):
        observed.update(command=command, environment=kwargs["env"])
        return {"root": str(project), "indexed": True}

    monkeypatch.setattr(devtools, "_run_json", run)
    lab._index(manifest)
    command = observed["command"]
    assert command[command.index("--root") + 1] == str(lab.project)
    assert all(path in command for path in devtools.EXCLUDES)
    assert "AST_INDEX_DB" not in observed["environment"]
    assert "SMALL_MODEL_API_KEY" not in observed["environment"]
    assert observed["environment"]["AST_INDEX_STATE_DIR"] == str(lab.state / "ast")


def test_failed_start_has_no_own_pid_and_can_retry(lab, manifest, monkeypatch):
    monkeypatch.setattr(devtools.shutil, "which", lambda name: manifest["tools"].get(name))
    for _ in range(2):
        with pytest.raises(devtools.DevtoolsError, match="startup failed"):
            lab.up()
        assert not lab.process_path.exists()
    assert (lab.state / "ichnora.log").stat().st_mode & 0o777 == 0o600


def test_stop_does_not_kill_reused_pid():
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
    try:
        record = devtools._identity(child.pid)
        assert record
        forged = {**record, "start_ticks": "different"}
        assert not devtools._stop_owned(forged)
        assert child.poll() is None
        assert devtools._stop_owned(record, grace=1)
        child.wait(timeout=3)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_stale_socket_recovery_preserves_live_listener(lab):
    lab.agent_socket.parent.mkdir(parents=True)
    with socket.socket(socket.AF_UNIX) as stale:
        stale.bind(str(lab.agent_socket))
    assert lab._remove_unlistening_sockets()
    assert not lab.agent_socket.exists()
    with socket.socket(socket.AF_UNIX) as live:
        live.bind(str(lab.agent_socket))
        live.listen()
        assert not lab._remove_unlistening_sockets()
        assert lab.agent_socket.exists()


def test_mcp_stdout_has_no_setup_text_and_never_exposes_admin(lab, manifest, monkeypatch, capsys):
    lab.state.mkdir()
    devtools._write_json(lab.manifest_path, manifest)
    monkeypatch.setenv("ICHNORA_ADMIN_SOCKET", "/foreign/admin.sock")
    monkeypatch.setattr(lab, "_memory_status", lambda _: {"result": {"scope": manifest["scope"]}})
    monkeypatch.setattr(devtools.os, "chdir", lambda _: None)
    called = []

    class Executed(Exception):
        pass

    def execute(binary, argv, environment):
        assert "ICHNORA_ADMIN_SOCKET" not in environment
        assert "SMALL_MODEL_API_KEY" not in environment
        called.extend(argv)
        raise Executed

    monkeypatch.setenv("SMALL_MODEL_API_KEY", "private-test-value")
    monkeypatch.setattr(devtools.os, "execve", execute)
    with pytest.raises(Executed):
        lab.mcp("ichnora")
    assert called[-1] == "reader,capture"
    assert str(lab.agent_socket) in called
    assert str(lab.admin_socket) not in called
    assert capsys.readouterr().out == ""


def test_down_remains_available_when_old_package_path_disappears(lab, manifest):
    lab.state.mkdir()
    devtools._write_json(lab.manifest_path, manifest)
    Path(manifest["tools"]["ichnora"]).unlink()
    assert lab.down() == {"configured": True, "stopped": False}


def test_refresh_tools_preserves_memory_after_old_package_disappears(lab, manifest, monkeypatch):
    lab.state.mkdir()
    devtools._write_json(lab.manifest_path, manifest)
    old_binary = Path(manifest["tools"]["ichnora"])
    new_binary = old_binary.with_name("updated-tool")
    new_binary.write_bytes(old_binary.read_bytes())
    new_binary.chmod(0o700)
    old_binary.unlink()
    memory = lab.state / "memory"
    memory.mkdir()
    owned_data = memory / "service-owned.data"
    owned_data.write_bytes(b"must-be-preserved")
    monkeypatch.setattr(devtools.shutil, "which", lambda _: str(new_binary))
    assert lab.refresh_tools()["tools_refreshed"]
    assert lab.manifest()["tools"]["ichnora"] == str(new_binary)
    assert lab.manifest()["scope"] == manifest["scope"]
    assert owned_data.read_bytes() == b"must-be-preserved"


def test_refresh_tools_requires_own_service_stop(lab, manifest, monkeypatch):
    lab.state.mkdir()
    devtools._write_json(lab.manifest_path, manifest)
    record = {"pid": 12345}
    devtools._write_json(lab.process_path, record)
    monkeypatch.setattr(devtools.shutil, "which", lambda _: manifest["tools"]["ichnora"])
    monkeypatch.setattr(devtools, "_identity", lambda _: record)
    with pytest.raises(devtools.DevtoolsError, match="Stop this project's memory"):
        lab.refresh_tools()
    assert devtools._read_json(lab.process_path) == record
