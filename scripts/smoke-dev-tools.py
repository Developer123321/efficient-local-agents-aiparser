#!/usr/bin/env python3
"""Exercise installed developer tools against a disposable project, without models.

Run from the Nix development shell:
    uv run --locked python scripts/smoke-dev-tools.py

This is an explicit integration test, not a package-presence or mocked unit test.
It never enrolls the real checkout or reads its memory database.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from hacklab.devtools import DevelopmentTools, project_root, state_directory

REQUIRED = ("ichnora", "ast-index", "agent-evidence", "agent-tool-meter", "tabula")
MAX_RESPONSE_BYTES = 262_144


class SmokeError(RuntimeError):
    """A fixed, actionable failure without external response bodies."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise SmokeError(message)


def command(
    arguments: list[str],
    cwd: Path,
    *,
    codes: tuple[int, ...] = (0,),
    timeout: float = 20,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            arguments,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeError("A developer-tool smoke operation timed out.") from exc
    except OSError as exc:
        raise SmokeError("An installed developer-tool command could not start.") from exc
    require(result.returncode in codes, "A developer-tool command failed its smoke contract.")
    require(
        len(result.stdout.encode()) <= MAX_RESPONSE_BYTES,
        "A developer-tool response exceeded the smoke-test limit.",
    )
    return result


def json_object(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except (ValueError, UnicodeError) as exc:
        raise SmokeError("A developer tool returned invalid JSON.") from exc
    require(isinstance(value, dict), "A developer tool did not return a JSON object.")
    return value


class McpClient:
    """Line-delimited stdio JSON-RPC with bounded reads and owned-child cleanup."""

    def __init__(self, entry: dict[str, Any], foreign_cwd: Path):
        require(Path(entry["command"]).is_absolute(), "Generated MCP interpreter is not absolute.")
        require(
            entry["command"] == str(Path(sys.executable).absolute()),
            "Generated MCP interpreter does not preserve the selected Python environment.",
        )
        self.stderr = tempfile.TemporaryFile()
        self.process = subprocess.Popen(
            [entry["command"], *entry["args"]],
            cwd=foreign_cwd,
            # No Git/tool PATH, shell activation, inherited credentials or AST root.
            env={"PATH": "/nonexistent", "LANG": "C.UTF-8"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            start_new_session=True,
        )
        require(self.process.stdout is not None, "MCP stdout was not created.")
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        os.set_blocking(self.process.stdout.fileno(), False)
        self.buffer = bytearray()
        self.seen_bytes = 0
        self.next_id = 1

    def __enter__(self):
        try:
            initialized = self.request(
                "initialize",
                {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "hacklab-dev-smoke", "version": "1"},
                },
            )
            require(
                initialized.get("protocolVersion") == "2025-11-25",
                "Generated MCP server did not negotiate the expected protocol.",
            )
            self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            return self
        except Exception:
            self.close()
            raise

    def __exit__(self, *_args):
        self.close()

    def close(self) -> None:
        if self.process.stdin is not None:
            with contextlib.suppress(OSError):
                self.process.stdin.close()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=2)
        self.selector.close()
        if self.process.stdout is not None:
            self.process.stdout.close()
        self.stderr.close()

    def send(self, message: dict[str, Any]) -> None:
        require(self.process.stdin is not None, "MCP stdin was not created.")
        try:
            self.process.stdin.write(json.dumps(message).encode() + b"\n")
            self.process.stdin.flush()
        except (OSError, BrokenPipeError) as exc:
            raise SmokeError("Generated MCP launcher exited before accepting a request.") from exc

    def request(self, method: str, parameters: dict[str, Any]) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": parameters})
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            while b"\n" in self.buffer:
                line, _, self.buffer = self.buffer.partition(b"\n")
                message = json_object(line.decode())
                if message.get("id") != request_id:
                    continue
                require("error" not in message, "Generated MCP server returned a protocol error.")
                result = message.get("result")
                require(isinstance(result, dict), "Generated MCP response has no result object.")
                return result
            remaining = max(0, deadline - time.monotonic())
            if not self.selector.select(remaining):
                continue
            chunk = os.read(self.process.stdout.fileno(), 8192)
            require(bool(chunk), "Generated MCP launcher exited without a complete response.")
            self.seen_bytes += len(chunk)
            require(
                self.seen_bytes <= MAX_RESPONSE_BYTES,
                "Generated MCP output exceeded the smoke-test limit.",
            )
            self.buffer.extend(chunk)
        raise SmokeError("Generated MCP launcher exceeded its response deadline.")

    def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = self.request("tools/call", {"name": name, "arguments": arguments})
        require(not result.get("isError"), "Generated MCP tool reported an operation error.")
        for item in result.get("content", []):
            if item.get("type") == "text":
                return json_object(item["text"])
        raise SmokeError("Generated MCP tool returned no JSON text result.")


def exercise_mcp(tools: DevelopmentTools, foreign: Path) -> None:
    generic = json_object((tools.project / ".mcp.json").read_text())
    bob = json_object((tools.project / ".bob/mcp.json").read_text())
    expected_unrelated = {"command": "/unused-unrelated-server", "args": []}
    require(
        generic["mcpServers"].get("unrelated") == expected_unrelated,
        "Setup changed the unrelated generic MCP entry.",
    )
    require(
        bob["mcpServers"].get("unrelated") == expected_unrelated,
        "Setup changed the unrelated Bob MCP entry.",
    )
    for name in ("hacklab-memory", "hacklab-code"):
        require(
            generic["mcpServers"][name] == bob["mcpServers"][name],
            "Bob and generic MCP entries disagree.",
        )
    manifest = tools.manifest()
    with McpClient(generic["mcpServers"]["hacklab-memory"], foreign) as memory:
        listing = memory.request("tools/list", {})
        require(
            "memory_status" in {tool["name"] for tool in listing["tools"]},
            "Ichnora MCP did not expose memory_status.",
        )
        status = memory.call("memory_status", {})
        require(status.get("ok"), "Ichnora MCP memory_status failed.")
        scope = status["result"].get("scope", {})
        require(
            all(scope.get(key) == value for key, value in manifest["scope"].items()),
            "Ichnora MCP resolved a different project/worktree scope.",
        )
        require(
            status["result"]["service"].get("model_calls") == "none",
            "Ichnora smoke unexpectedly reports model calls.",
        )
    with McpClient(generic["mcpServers"]["hacklab-code"], foreign) as ast:
        listing = ast.request("tools/list", {})
        require(
            [tool["name"] for tool in listing["tools"]] == ["code_explore"],
            "AST MCP did not expose its expected single-tool contract.",
        )
        status = ast.call("code_explore", {"action": "status", "format": "json"})
        require(status.get("indexed"), "AST MCP did not open the prepared index.")
        require(status.get("root") == str(tools.project), "AST MCP opened a different root.")
        found = ast.call("code_explore", {"action": "search", "query": "smoke_", "format": "json"})
        require(
            {(item["name"], item["file"]) for item in found["items"]}
            == {("smoke_visible", "src/demo.py")},
            "AST search missed the source symbol or included excluded benchmark/data symbols.",
        )


def exercise_cli(executables: dict[str, str], project: Path, private: Path) -> None:
    read = [
        executables["agent-evidence"],
        "--root",
        str(project),
        "read",
        "src/demo.py",
        "--start",
        "1",
        "--lines",
        "10",
        "--max-bytes",
        "8192",
    ]
    evidence = command(read, project)
    receipt = json_object(evidence.stdout)
    require(receipt.get("status") == "ok", "Evidence read did not succeed.")
    require(
        receipt.get("sha256") == hashlib.sha256((project / "src/demo.py").read_bytes()).hexdigest(),
        "Evidence digest does not match the fixture source.",
    )
    log = private / "measurements.jsonl"
    measured = command(
        [
            executables["agent-tool-meter"],
            "run",
            "--log",
            str(log),
            "--label",
            "source_read",
            "--timeout",
            "15",
            "--",
            *read,
        ],
        project,
    )
    require(measured.stdout == evidence.stdout, "Tool measurement altered source evidence output.")
    report = json_object(
        command([executables["agent-tool-meter"], "report", "--log", str(log)], project).stdout
    )
    group = report["groups"]["source_read"]
    require(
        group["calls"] == 1
        and group["failed"] == 0
        and group["stdout_bytes"] == len(evidence.stdout.encode())
        and group["stderr_bytes"] == 0,
        "Tool meter did not report the exact successful command output.",
    )
    require("smoke_visible" not in log.read_text(), "Tool meter stored source content.")
    tabula = executables["tabula"]
    command([tabula, "update", str(project)], project)
    checked = json_object(command([tabula, "check", str(project), "--json"], project).stdout)
    require(checked.get("status") == "current", "Tabula did not recognize current source hashes.")
    source = project / "src/demo.py"
    source.write_text("def smoke_visible():\n    return 8\n")
    changed = json_object(
        command([tabula, "check", str(project), "--json"], project, codes=(1,)).stdout
    )
    require(changed.get("status") == "stale", "Tabula did not detect the edited source.")


def main() -> int:
    executables = {name: shutil.which(name) for name in REQUIRED}
    missing = [name for name, executable in executables.items() if executable is None]
    if missing:
        print(
            json.dumps(
                {
                    "status": "failed",
                    "reason": "Missing installed tools: " + ", ".join(missing),
                    "action": "Enter `nix develop`, then rerun this integration test.",
                }
            ),
            file=sys.stderr,
        )
        return 2
    git = shutil.which("git")
    if git is None:
        print('{"status":"failed","reason":"Git is required; enter nix develop."}', file=sys.stderr)
        return 2
    tools = None
    try:
        with tempfile.TemporaryDirectory(prefix="hl-dev-") as temporary:
            base = Path(temporary)
            project = base / "workspace/demo"
            project.mkdir(parents=True)
            for directory in ("src", "bench", "data", ".tabula", ".bob"):
                (project / directory).mkdir()
            (project / "README.md").write_text("# Disposable developer-tool smoke fixture\n")
            (project / "src/demo.py").write_text("def smoke_visible():\n    return 7\n")
            (project / "bench/hidden.py").write_text(
                "def smoke_benchmark_secret():\n    return 1\n"
            )
            (project / "data/hidden.py").write_text("def smoke_data_secret():\n    return 2\n")
            (project / ".tabula/ENTRY.md").write_text(
                "# Demo\n\n## Entry point\n\n"
                "The [entry point](../src/demo.py) returns a constant integer.\n"
            )
            unrelated = {
                "mcpServers": {"unrelated": {"command": "/unused-unrelated-server", "args": []}}
            }
            for filename in (".mcp.json", ".bob/mcp.json"):
                (project / filename).write_text(json.dumps(unrelated))
            command([git, "init", "-q", str(project)], base)
            foreign = base / "foreign"
            foreign.mkdir()
            tools = DevelopmentTools(
                project_root(project), state_directory(project.resolve(), base / "private")
            )
            try:
                setup = tools.setup()
                require(
                    setup.get("running") and setup.get("scope_verified"),
                    "Developer setup did not confirm a running project-scoped service.",
                )
                first = json_object(tools.process_path.read_text())
                repeated = tools.setup()
                require(
                    repeated.get("running")
                    and repeated.get("scope_verified")
                    and json_object(tools.process_path.read_text()) == first,
                    "Repeated setup did not preserve the owned service identity.",
                )
                exercise_mcp(tools, foreign)
                exercise_cli(executables, project, base / "private")
            finally:
                tools.down()
                require(not tools.status().get("running"), "Owned smoke service was not stopped.")
        print(
            json.dumps(
                {
                    "status": "passed",
                    "tools": dict.fromkeys(REQUIRED, "passed"),
                    "generated_mcp": "foreign cwd, empty tool PATH, negotiated 2025-11-25",
                    "scope": "disposable project/worktree only",
                    "model_calls": "none",
                    "cleanup": "owned service stopped; temporary project removed",
                },
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        reason = str(exc) if isinstance(exc, SmokeError) else type(exc).__name__
        print(json.dumps({"status": "failed", "reason": reason}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
