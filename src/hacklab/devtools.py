"""Project-local development memory and code navigation, outside competition runs.

The service owns its database. This module uses the public CLI and MCP contracts,
never a database fallback or automatic transcript collection.
"""

from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 262_144
EXCLUDES = ("bench/", "data/", ".state/", ".venv/", "results/", "dist/", "vendor/", ".git/")
SERVER_NAMES = {"ichnora": "hacklab-memory", "ast": "hacklab-code"}


class DevtoolsError(RuntimeError):
    """Fixed messages that never include external response bodies or secret values."""


def _tool_environment(state: Path) -> dict[str, str]:
    """Do not inherit a different Lab VM's AST selection or model credentials."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(Path.home()),
        "LANG": "C.UTF-8",
        "AST_INDEX_STATE_DIR": str(state / "ast"),
    }


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise DevtoolsError("Expected a bounded regular JSON file; symbolic links are refused.")
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError, UnicodeError) as exc:
        raise DevtoolsError("A local configuration file contains invalid JSON.") from exc
    if not isinstance(value, dict):
        raise DevtoolsError("A local configuration must contain a JSON object.")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    if path.is_symlink():
        raise DevtoolsError("Refusing to replace a symbolic-link configuration.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".hacklab-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _run_json(
    command: list[str], project: Path, *, timeout: float = 15, env: dict[str, str] | None = None
) -> dict[str, Any]:
    # File-backed capture bounds memory even if a failing tool produces a large log.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(command, cwd=project, env=env, stdout=stdout, stderr=stderr)
            try:
                code = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise DevtoolsError("A development tool exceeded its operation timeout.") from None
        except OSError as exc:
            raise DevtoolsError(
                "A configured development-tool executable could not start."
            ) from exc
        if stdout.tell() > MAX_JSON_BYTES:
            raise DevtoolsError("A development-tool response exceeded the JSON limit.")
        stdout.seek(0)
        try:
            output = json.load(stdout)
        except (ValueError, UnicodeError) as exc:
            raise DevtoolsError(
                "A development tool returned invalid JSON; inspect its local log."
            ) from exc
        if code or not isinstance(output, dict) or output.get("ok") is False:
            raise DevtoolsError(
                "A development-tool request failed; inspect service status locally."
            )
        return output


def project_root(value: Path) -> Path:
    try:
        directory = value.expanduser().resolve(strict=True)
        result = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        root = Path(result.stdout.strip()).resolve(strict=True)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DevtoolsError("Select an existing Git checkout with --project.") from exc
    if root == root.parent or root == Path.home().resolve():
        raise DevtoolsError("The home directory or filesystem root cannot be a project scope.")
    return root


def state_directory(project: Path, selected: Path | None = None) -> Path:
    if selected is None:
        base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
        if not base.is_absolute():
            raise DevtoolsError("XDG_STATE_HOME must be an absolute directory.")
        key = hashlib.sha256(str(project).encode()).hexdigest()[:16]
        selected = base / "hacklab" / key
    state = selected.expanduser().resolve()
    # Ichnora workspace_roots contains the checkout's parent. State must be outside it.
    if state == state.parent or state.is_relative_to(project.parent):
        raise DevtoolsError("Development state must be outside the project's workspace parent.")
    if str(state).startswith("/nix/store/"):
        raise DevtoolsError("Development state cannot be stored in the Nix store.")
    return state


def _identity(pid: int) -> dict[str, Any] | None:
    if type(pid) is not int or pid <= 1 or pid == os.getpid():
        return None
    try:
        proc = Path(f"/proc/{pid}")
        if proc.stat().st_uid != os.getuid():
            return None
        fields = (proc / "stat").read_text().rpartition(")")[2].split()
        if fields[0] == "Z":
            return None
        return {
            "pid": pid,
            "start_ticks": fields[19],
            "command_sha256": hashlib.sha256((proc / "cmdline").read_bytes()).hexdigest(),
            "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        }
    except (OSError, IndexError):
        return None


def _stop_owned(record: dict[str, Any], *, grace: float = 5) -> bool:
    pid = record.get("pid")
    if type(pid) is not int or _identity(pid) != record:
        return False
    if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
        raise DevtoolsError("Safe service stop requires Linux pidfd support.")
    try:
        fd = os.pidfd_open(pid)
    except ProcessLookupError:
        return False
    try:
        if _identity(pid) != record:
            return False
        signal.pidfd_send_signal(fd, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if _identity(pid) != record:
                return True
            time.sleep(0.05)
        if _identity(pid) == record:
            signal.pidfd_send_signal(fd, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True
    finally:
        os.close(fd)


class DevelopmentTools:
    def __init__(self, project: Path, state: Path):
        self.project, self.state = project, state
        self.manifest_path = state / "manifest.json"
        self.process_path = state / "process.json"
        self.config_path = state / "ichnora.json"
        self.agent_socket = state / "run/agent.sock"
        self.admin_socket = state / "run/admin.sock"

    def _prepare(self) -> None:
        leftovers = list(self.state.iterdir()) if self.state.exists() else []
        bootstrap_lock = (
            len(leftovers) == 1
            and leftovers[0].name == "lock"
            and not leftovers[0].is_symlink()
            and leftovers[0].is_file()
            and leftovers[0].stat().st_uid == os.getuid()
            and leftovers[0].stat().st_size == 0
        )
        if leftovers and not self.manifest_path.exists() and not bootstrap_lock:
            raise DevtoolsError(
                "Choose an empty dedicated state directory; existing files are preserved."
            )
        if self.manifest_path.exists():
            self.manifest(validate_tools=False)
        self.state.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state.chmod(0o700)

    @contextlib.contextmanager
    def locked(self):
        self._prepare()
        if (self.state / "lock").is_symlink():
            raise DevtoolsError("The state lock cannot be a symbolic link.")
        descriptor = os.open(self.state / "lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise DevtoolsError(
                    "Another development-tool lifecycle command is active."
                ) from None
            yield
        finally:
            os.close(descriptor)

    def manifest(self, *, validate_tools: bool = True) -> dict[str, Any]:
        manifest = _read_json(self.manifest_path)
        if (
            manifest.get("version") != 1
            or manifest.get("project") != str(self.project)
            or manifest.get("state_dir") != str(self.state)
            or not isinstance(manifest.get("tools"), dict)
        ):
            raise DevtoolsError("The selected state belongs to a different project or contract.")
        for name in ("ichnora", "ast-index") if validate_tools else ():
            value = manifest["tools"].get(name)
            if (
                not isinstance(value, str)
                or not Path(value).is_absolute()
                or not os.access(value, os.X_OK)
            ):
                raise DevtoolsError(
                    "A pinned development-tool executable is missing; enter its Nix shell."
                )
        return manifest

    def _configure(self, *, refresh: bool = False) -> dict[str, Any]:
        if self.manifest_path.exists() and not refresh:
            return self.manifest()
        previous = self.manifest(validate_tools=False) if self.manifest_path.exists() else {}
        tools = {}
        for name in ("ichnora", "ast-index", "tabula", "agent-evidence", "agent-tool-meter"):
            if found := shutil.which(name):
                tools[name] = str(Path(found).resolve())
        if any(name not in tools for name in ("ichnora", "ast-index")):
            raise DevtoolsError("Enter `nix develop`; Ichnora and AST Index are required.")
        if len(os.fsencode(self.admin_socket)) >= 108:
            raise DevtoolsError("Choose a shorter state directory for Linux Unix sockets.")
        if refresh:
            if self.process_path.exists():
                record = _read_json(self.process_path)
                if _identity(record.get("pid", 0)) == record:
                    raise DevtoolsError("Stop this project's memory with `hacklab-dev down` first.")
            if not self._remove_unlistening_sockets():
                raise DevtoolsError("A process still listens on the managed memory sockets.")
            self.process_path.unlink(missing_ok=True)
        self.agent_socket.parent.mkdir(mode=0o700, exist_ok=True)
        manifest = {
            "version": 1,
            "project": str(self.project),
            "state_dir": str(self.state),
            "tools": tools,
        }
        if "scope" in previous:
            manifest["scope"] = previous["scope"]
        evidence = {
            key: tools[name]
            for key, name in (
                ("tabula", "tabula"),
                ("agent_evidence", "agent-evidence"),
                ("ast_index", "ast-index"),
            )
            if name in tools
        }
        evidence["ast_root"] = str(self.project)
        config = {
            "state_dir": str(self.state / "memory"),
            "agent_socket": str(self.agent_socket),
            "admin_socket": str(self.admin_socket),
            "workspace_roots": [str(self.project.parent)],
            "session_sources": {},
            "tools": evidence,
        }
        _write_json(self.config_path, config)
        _write_json(self.manifest_path, manifest)
        return manifest

    def refresh_tools(self) -> dict[str, Any]:
        """Rebind changed package paths while preserving the service-owned memory."""
        with self.locked():
            self.manifest(validate_tools=False)
            self._configure(refresh=True)
        return {"configured": True, "tools_refreshed": True, "next": "hacklab-dev setup"}

    def _memory_status(self, manifest: dict[str, Any]) -> dict[str, Any]:
        return _run_json(
            [manifest["tools"]["ichnora"], "status", "--socket", str(self.agent_socket)],
            self.project,
            timeout=3,
        )

    def _up(self) -> dict[str, Any]:
        manifest = self._configure()
        if self.process_path.exists():
            record = _read_json(self.process_path)
            if _identity(record.get("pid", 0)) == record:
                self._memory_status(manifest)
                return manifest
            if not self._remove_unlistening_sockets():
                raise DevtoolsError("A different process still listens on the managed sockets.")
            self.process_path.unlink()
        if self.agent_socket.exists() or self.admin_socket.exists():
            raise DevtoolsError("Unmanaged or stale sockets exist; do not start a second service.")
        log_path = self.state / "ichnora.log"
        if log_path.is_symlink():
            raise DevtoolsError("The service log cannot be a symbolic link.")
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        environment = _tool_environment(self.state)
        try:
            process = subprocess.Popen(
                [manifest["tools"]["ichnora"], "serve", "--config", str(self.config_path)],
                cwd=self.project,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=fd,
                stderr=fd,
                start_new_session=True,
            )
        finally:
            os.close(fd)
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise DevtoolsError("Ichnora startup failed; inspect the private ichnora.log.")
                if self.agent_socket.is_socket() and self.admin_socket.is_socket():
                    self._memory_status(manifest)
                    if record := _identity(process.pid):
                        _write_json(self.process_path, record)
                        return manifest
                time.sleep(0.05)
            raise DevtoolsError("Ichnora did not become ready within eight seconds.")
        except Exception:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            self.process_path.unlink(missing_ok=True)
            self._remove_unlistening_sockets()
            raise

    def _remove_unlistening_sockets(self) -> bool:
        """Recover only owned, unchanged socket files that have no listener."""
        for path in (self.agent_socket, self.admin_socket):
            if not path.exists():
                continue
            if path.is_symlink() or not path.is_socket():
                return False
            before = path.stat()
            if before.st_uid != os.getuid():
                return False
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(0.2)
                try:
                    probe.connect(str(path))
                    return False
                except OSError as exc:
                    if exc.errno not in {errno.ECONNREFUSED, errno.ENOENT}:
                        return False
            if path.exists():
                after = path.stat()
                if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
                    return False
                path.unlink()
        return True

    def up(self) -> dict[str, Any]:
        with self.locked():
            self._up()
        return self.status()

    def down(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"configured": False, "stopped": False}
        with self.locked():
            stopped = (
                _stop_owned(_read_json(self.process_path)) if self.process_path.exists() else False
            )
            if stopped and self._remove_unlistening_sockets():
                self.process_path.unlink(missing_ok=True)
        return {"configured": True, "stopped": stopped}

    def status(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"configured": False, "running": False}
        manifest = self.manifest()
        record = _read_json(self.process_path) if self.process_path.exists() else {}
        running = _identity(record.get("pid", 0)) == record if record else False
        result: dict[str, Any] = {
            "configured": True,
            "running": running,
            "tools": sorted(manifest["tools"]),
            "scope_verified": False,
        }
        if running:
            try:
                health = self._memory_status(manifest)["result"]
                scope = health.get("scope")
                result["scope_verified"] = self._scope_matches(manifest, scope)
                result["model_calls"] = health.get("service", {}).get("model_calls")
            except (DevtoolsError, KeyError, TypeError):
                result["service_unavailable"] = True
        try:
            ast = _run_json(
                [
                    manifest["tools"]["ast-index"],
                    "--root",
                    str(self.project),
                    "--state-dir",
                    str(self.state / "ast"),
                    "--json",
                    "status",
                ],
                self.project,
                timeout=5,
                env=_tool_environment(self.state),
            )
            result["ast"] = {
                key: ast[key]
                for key in (
                    "root",
                    "indexed",
                    "generation",
                    "files",
                    "freshness",
                    "reference_coverage",
                )
                if key in ast
            }
            result["ast"]["root_verified"] = ast.get("root") == str(self.project)
        except DevtoolsError:
            result["ast"] = {"available": False}
        return result

    def _index(self, manifest: dict[str, Any]) -> dict[str, Any]:
        command = [
            manifest["tools"]["ast-index"],
            "--root",
            str(self.project),
            "--state-dir",
            str(self.state / "ast"),
            "--json",
            "index",
        ]
        for excluded in EXCLUDES:
            command.extend(["--exclude", excluded])
        return _run_json(command, self.project, timeout=120, env=_tool_environment(self.state))

    def index(self) -> dict[str, Any]:
        with self.locked():
            return self._index(self.manifest())

    def _mcp_plan(self) -> list[tuple[Path, dict[str, Any]]]:
        servers = {}
        for target, name in SERVER_NAMES.items():
            servers[name] = {
                # A virtualenv interpreter is often a symlink. Resolving it
                # would lose its environment and the installed hacklab module.
                "command": str(Path(sys.executable).absolute()),
                "args": [
                    "-m",
                    "hacklab.devtools",
                    "--project",
                    str(self.project),
                    "--state-dir",
                    str(self.state),
                    "mcp",
                    target,
                ],
            }
        planned = []
        for path in (self.project / ".mcp.json", self.project / ".bob/mcp.json"):
            if path.parent.is_symlink():
                raise DevtoolsError("MCP configuration directories cannot be symbolic links.")
            original = _read_json(path) if path.exists() or path.is_symlink() else {}
            current = original.get("mcpServers", {})
            if not isinstance(current, dict):
                raise DevtoolsError("Existing MCP configuration has an invalid mcpServers object.")
            for name, entry in servers.items():
                if name in current and current[name] != entry:
                    raise DevtoolsError(
                        "Existing hacklab MCP server conflicts; preserve and review it first."
                    )
            planned.append((path, {**original, "mcpServers": {**current, **servers}}))
        return planned

    def setup(self) -> dict[str, Any]:
        # Preflight both client files before starting services or replacing either file.
        planned = self._mcp_plan()
        with self.locked():
            manifest = self._up()
            enrolled = _run_json(
                [
                    manifest["tools"]["ichnora"],
                    "enroll",
                    str(self.project),
                    "--admin-socket",
                    str(self.admin_socket),
                ],
                self.project,
            )
            project = enrolled.get("result", {})
            roots = [
                root for root in project.get("roots", []) if root.get("path") == str(self.project)
            ]
            if (
                not project.get("id")
                or len(roots) != 1
                or not roots[0].get("worktree")
                or project.get("collect")
                or project.get("recall")
            ):
                raise DevtoolsError("Enrollment did not confirm the selected manual-memory scope.")
            manifest["scope"] = {"project_id": project["id"], "worktree": roots[0]["worktree"]}
            _write_json(self.manifest_path, manifest)
            health = self._memory_status(manifest)
            scope = health.get("result", {}).get("scope")
            if not self._scope_matches(manifest, scope):
                raise DevtoolsError(
                    "Ichnora returned a different project scope; no MCP config was written."
                )
            self._index(manifest)
            for path, configuration in planned:
                _write_json(path, configuration)
        return {
            **self.status(),
            "mcp_files": [".mcp.json", ".bob/mcp.json"],
            "collection": False,
            "automatic_recall": False,
        }

    def mcp(self, target: str) -> None:
        manifest = self.manifest()
        environment = _tool_environment(self.state)
        if target == "ichnora":
            health = self._memory_status(manifest)
            scope = health.get("result", {}).get("scope")
            if not self._scope_matches(manifest, scope):
                raise DevtoolsError(
                    "Memory scope is unavailable or different; run hacklab-dev setup."
                )
            command = [
                manifest["tools"]["ichnora"],
                "mcp",
                "--socket",
                str(self.agent_socket),
                "--role",
                "reader,capture",
            ]
            environment["ICHNORA_SOCKET"] = str(self.agent_socket)
        else:
            # An existing Lab VM can export its own workspace-wide bindings.
            # Explicit project navigation must not inherit those selections.
            command = [
                manifest["tools"]["ast-index"],
                "--root",
                str(self.project),
                "--state-dir",
                str(self.state / "ast"),
                "mcp",
            ]
        os.chdir(self.project)
        os.execve(command[0], command, environment)

    @staticmethod
    def _scope_matches(manifest: dict[str, Any], scope: Any) -> bool:
        expected = manifest.get("scope")
        return bool(
            isinstance(expected, dict)
            and isinstance(scope, dict)
            and scope.get("project_id") == expected.get("project_id")
            and scope.get("worktree") == expected.get("worktree")
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Project-local coding memory and AST navigation")
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--state-dir", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("setup", "up", "down", "status", "index", "refresh-tools"):
        subparsers.add_parser(command)
    subparsers.add_parser("mcp").add_argument("target", choices=("ichnora", "ast"))
    args = parser.parse_args()
    try:
        # Generated MCP entries already bind an explicit canonical checkout and
        # private manifest. Rediscovering Git would require a shell-only PATH.
        project = (
            args.project.expanduser().resolve(strict=True)
            if args.command == "mcp"
            else project_root(args.project)
        )
        if not project.is_dir():
            raise DevtoolsError("The selected project directory is unavailable.")
        tools = DevelopmentTools(project, state_directory(project, args.state_dir))
        if args.command == "mcp":
            tools.mcp(args.target)
        else:
            print(json.dumps(getattr(tools, args.command.replace("-", "_"))(), indent=2))
        return 0
    except (DevtoolsError, OSError) as exc:
        message = (
            str(exc) if isinstance(exc, DevtoolsError) else "Local development-tool I/O failed."
        )
        print(f"hacklab-dev: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
