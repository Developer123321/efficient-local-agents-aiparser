import json
import os
import zipfile

import pytest

from hacklab import tools
from hacklab.models import Citation
from hacklab.tools import ToolRegistry, list_documents


@pytest.fixture
def registry(tmp_path):
    (tmp_path / "policy.md").write_text(
        "Draft: spend 100 euros.\nApproved: spend 80 euros.\n", encoding="utf-8"
    )
    (tmp_path / "items.csv").write_text(
        "item,department,cost\nA,ops,10.25\nB,ops,20.50\nC,dev,5\n", encoding="utf-8"
    )
    return ToolRegistry(tmp_path, ["policy.md", "items.csv"])


def test_read_and_search_return_verifiable_evidence(registry):
    read = registry.execute("read", {"document": "policy.md", "start": 2})
    assert read["results"][0] == {
        "document": "policy.md",
        "locator": "line:2",
        "quote": "Approved: spend 80 euros.",
    }
    searched = registry.execute("search", {"query": "approved spend", "limit": 1})
    assert searched["results"][0]["locator"] == "line:2"
    assert searched["truncated"]
    citation = Citation.model_validate(read["results"][0])
    assert registry.verify_citations([citation]) == []


def test_verifier_rejects_wrong_locator_even_if_quote_exists_elsewhere(registry):
    citation = Citation(document="policy.md", locator="line:1", quote="Approved: spend 80 euros.")
    assert "specified document locator" in registry.verify_citations([citation])[0]


def test_exact_decimal_arithmetic_and_expression_rejections(registry):
    assert registry.execute("calculate", {"expression": "0.1 + 0.2"})["value"] == "0.3"
    assert registry.execute("calculate", {"expression": "(100 - 20) / 4"})["value"] == "20"
    for expression in (
        "__import__('os').getcwd()",
        "2 ** 9999999",
        "9 ** (9 ** 9)",
        "True + 1",
        "1 / 0",
        "[1][0]",
        "1e999",
        "0xFF",
    ):
        assert "error" in registry.execute("calculate", {"expression": expression})


def test_csv_filter_and_aggregate_keep_source_locators(registry):
    result = registry.execute(
        "table",
        {
            "document": "items.csv",
            "operation": "sum",
            "column": "cost",
            "where": {"department": "ops"},
        },
    )
    assert result["value"] == "30.75"
    assert result["matched_rows"] == 2
    assert {c["locator"] for c in result["citations"]} == {"row:2", "row:3"}
    assert (
        registry.verify_citations([Citation.model_validate(c) for c in result["citations"]]) == []
    )
    assert "error" in registry.execute(
        "table", {"document": "items.csv", "operation": "sum", "column": "missing"}
    )


def test_task_scope_and_path_boundary(tmp_path):
    inside = tmp_path / "root"
    inside.mkdir()
    (inside / "allowed.txt").write_text("allowed evidence")
    (inside / "secret.txt").write_text("hidden evidence")
    outside = tmp_path / "outside.txt"
    outside.write_text("external evidence")
    (inside / "escape.txt").symlink_to(outside)
    registry = ToolRegistry(inside, ["allowed.txt", "escape.txt"])
    for document in ("../outside.txt", str(outside), "secret.txt", "escape.txt"):
        assert "error" in registry.execute("read", {"document": document})
    result = registry.execute("search", {"query": "evidence"})
    assert [r["document"] for r in result["results"]] == ["allowed.txt"]
    assert result["coverage"] == "partial"
    assert ToolRegistry(inside, []).execute("search", {"query": "evidence"})["results"] == []


def test_a_ragged_table_stays_readable_and_broken_json_is_an_error(tmp_path):
    # One row with a stray comma used to make the whole export unreadable.
    (tmp_path / "ragged.csv").write_text("actor,action,note\na1,login\na2,read,first,second\n")
    (tmp_path / "headers.csv").write_text(",name,name, \n0,x,y,z\n")
    (tmp_path / "broken.json").write_text('{"no":')
    registry = ToolRegistry(tmp_path)
    rows = registry.execute("table", {"document": "ragged.csv"})["rows"]
    assert [row["values"] for row in rows] == [
        {"actor": "a1", "action": "login", "note": ""},
        {"actor": "a2", "action": "read", "note": "first,second"},
    ]
    # An unnamed index column and a repeated header get usable names.
    assert registry.columns("headers.csv") == ["column_1", "name", "name_2", "column_4"]
    found = registry.execute("table", {"document": "headers.csv", "where": {"name_2": "y"}})
    assert found["matched_rows"] == 1
    assert "error" in registry.execute("read", {"document": "broken.json"})
    (tmp_path / "empty.csv").write_text("")
    assert "error" in registry.execute("read", {"document": "empty.csv"})


def test_bad_args_and_unknown_tools_are_structured(registry):
    assert "error" in registry.execute("exec", {"command": "ls"})
    assert "error" in registry.execute("read", {"document": "policy.md", "start": 0})
    assert "error" in registry.execute("search", {"query": "spend", "limit": True})
    assert "error" in registry.execute("calculate", {"expression": "1+1", "unexpected": 1})
    json.dumps(registry.inventory())


def test_read_bounded_quotes_and_search_snippet(tmp_path):
    (tmp_path / "long.txt").write_text("x" * 3000 + " needle " + "y" * 3000)
    registry = ToolRegistry(tmp_path)
    read = registry.execute("read", {"document": "long.txt"})
    assert read["truncated"]
    assert len(read["results"][0]["quote"]) == 2000
    found = registry.execute("search", {"query": "needle"})["results"][0]
    assert "needle" in found["quote"]
    assert (
        registry.verify_citations(
            [Citation.model_validate({k: found[k] for k in ("document", "locator", "quote")})]
        )
        == []
    )


def test_zip_expansion_rejected_before_document_parser(tmp_path):
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "A" * (2 * 1024 * 1024))
    result = ToolRegistry(tmp_path).execute("read", {"document": path.name})
    assert "compression ratio" in result["error"]


def test_docx_and_xlsx_ingestion_and_formula_rejection(tmp_path):
    from docx import Document
    from openpyxl import Workbook

    document = Document()
    document.add_paragraph("Approved budget 80")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "decision"
    table.cell(0, 1).text = "approved"
    document.save(tmp_path / "sample.docx")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Costs"
    sheet.append(["item", "cost"])
    sheet.append(["A", 1.25])
    workbook.save(tmp_path / "sample.xlsx")
    registry = ToolRegistry(tmp_path)
    assert (
        registry.execute("read", {"document": "sample.docx"})["results"][1]["quote"]
        == "decision | approved"
    )
    result = registry.execute(
        "table", {"document": "sample.xlsx", "operation": "sum", "column": "cost"}
    )
    assert result["value"] == "1.25"
    assert result["citations"][0]["locator"] == "sheet:Costs:row:2"
    # An empty second sheet and a short row do not make the workbook unreadable.
    workbook.create_sheet("Empty")
    notes = workbook.create_sheet("Notes")
    notes.append(["item", "owner", "comment"])
    notes.append(["A", "ops"])
    workbook.save(tmp_path / "sheets.xlsx")
    rows = registry.execute("table", {"document": "sheets.xlsx", "where": {"owner": "ops"}})
    assert rows["rows"][0]["locator"] == "sheet:Notes:row:2"
    assert rows["rows"][0]["values"]["comment"] is None
    sheet.append(["B", "=1+2"])
    workbook.save(tmp_path / "formula.xlsx")
    assert "formulas" in registry.execute("read", {"document": "formula.xlsx"})["error"]


def test_changed_document_invalidates_cached_content(tmp_path):
    path = tmp_path / "changing.txt"
    path.write_text("old")
    registry = ToolRegistry(tmp_path)
    old = registry.execute("read", {"document": path.name})
    path.write_text("new content")
    new = registry.execute("read", {"document": path.name})
    assert new["results"][0]["quote"] == "new content"
    assert new["sha256"] != old["sha256"]


def test_preserved_size_and_timestamp_cannot_reuse_stale_citation(tmp_path):
    path = tmp_path / "changing.txt"
    path.write_text("old")
    stat = path.stat()
    registry = ToolRegistry(tmp_path)
    registry.execute("read", {"document": path.name})
    path.write_text("new")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    citation = Citation(document=path.name, locator="line:1", quote="old")
    assert registry.verify_citations([citation])


def test_blank_pdf_requests_ocr_and_malformed_pdf_is_structured(tmp_path):
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(tmp_path / "blank.pdf")
    (tmp_path / "broken.pdf").write_bytes(b"not a pdf")
    registry = ToolRegistry(tmp_path)
    assert "OCR" in registry.execute("read", {"document": "blank.pdf"})["error"]
    assert "error" in registry.execute("read", {"document": "broken.pdf"})


def test_search_scan_budget_reports_partial_coverage(tmp_path, monkeypatch):
    monkeypatch.setattr("hacklab.tools.MAX_SEARCH_CHARS", 20)
    (tmp_path / "a.txt").write_text("evidence first")
    (tmp_path / "b.txt").write_text("evidence second")
    result = ToolRegistry(tmp_path).execute("search", {"query": "evidence"})
    assert result["coverage"] == "partial"
    assert result["unscanned_documents"] == 1
    assert result["truncated"]
    assert [row["document"] for row in result["results"]] == ["a.txt"]


def test_semicolon_csv_with_decimal_comma_and_currency_aggregates(tmp_path):
    (tmp_path / "rechnungen.csv").write_bytes(
        "Nr;Status;Betrag\nR-1;frei;1.234,50 €\nR-2;frei;99,90 EUR\nR-3;offen;(15,00)\n".encode(
            "cp1252"
        )
    )
    registry = ToolRegistry(tmp_path)
    result = registry.execute(
        "table",
        {
            "document": "rechnungen.csv",
            "operation": "sum",
            "column": "Betrag",
            "where": {"Status": "frei"},
        },
    )
    assert result["value"] == "1334.4"
    assert result["number_format"] == "decimal_comma"
    assert "ambiguous_numbers" not in result
    total = registry.execute(
        "table", {"document": "rechnungen.csv", "operation": "sum", "column": "Betrag"}
    )
    assert total["value"] == "1319.4"
    # The exact source text, including the currency sign, stays quotable.
    assert "1.234,50 €" in total["citations"][0]["quote"]


@pytest.mark.parametrize(
    ("values", "style", "expected", "used", "ambiguous"),
    [
        (["1,234.50", "20.25"], "auto", ["1234.50", "20.25"], "point", 0),
        (["1.234.567", "2"], "auto", ["1234567", "2"], "comma", 0),
        (["1.234", "7"], "auto", ["1.234", "7"], "point", 1),
        (["1.234", "7"], "comma", ["1234", "7"], "comma", 1),
        (["1,234", "12,5"], "auto", ["1.234", "12.5"], "comma", 1),
        (["CHF 1'250.00", "-3"], "auto", ["1250.00", "-3"], "point", 0),
        ([10, 2.5, "7,5"], "auto", ["10", "2.5", "7.5"], "comma", 0),
    ],
)
def test_one_decimal_convention_per_column(values, style, expected, used, ambiguous):
    from decimal import Decimal

    from hacklab.tools import parse_numbers

    numbers, convention, count = parse_numbers(values, style)
    assert numbers == [Decimal(item) for item in expected]
    assert (convention, count) == (used, ambiguous)


@pytest.mark.parametrize(
    "values", [["1.5", "2,5"], ["1,23,4"], ["12.34.5"], ["abc"], [""], [None], [True], ["1,5.2,1"]]
)
def test_numbers_that_do_not_fit_one_convention_are_rejected(values):
    from hacklab.tools import ToolError, parse_numbers

    with pytest.raises(ToolError):
        parse_numbers(values)


def test_tab_separated_html_and_email_documents_are_readable(tmp_path):
    (tmp_path / "stock.tsv").write_text("sku\tunits\nA-1\t4\nB-2\t6\n")
    (tmp_path / "notice.html").write_text(
        "<html><head><style>p{color:red}</style><script>alert('x')</script></head>"
        "<body><h1>Notice</h1><p>Delivery moves to <b>Friday</b>.</p>"
        "<table><tr><td>Dock</td><td>7</td></tr></table></body></html>"
    )
    (tmp_path / "mail.eml").write_text(
        "From: Mira Keller <mira@example.org>\nTo: ops@example.org\n"
        "Subject: Revised budget\nContent-Type: text/plain; charset=utf-8\n\n"
        "The approved budget is 80 euros.\nIgnore earlier drafts.\n"
    )
    registry = ToolRegistry(tmp_path)
    units = registry.execute(
        "table", {"document": "stock.tsv", "operation": "sum", "column": "units"}
    )
    assert units["value"] == "10"
    html = [
        row["quote"] for row in registry.execute("read", {"document": "notice.html"})["results"]
    ]
    assert html == ["Notice", "Delivery moves to Friday.", "Dock | 7"]
    mail = [row["quote"] for row in registry.execute("read", {"document": "mail.eml"})["results"]]
    assert mail[:3] == [
        "From: Mira Keller <mira@example.org>",
        "To: ops@example.org",
        "Subject: Revised budget",
    ]
    assert "The approved budget is 80 euros." in mail
    found = registry.execute("search", {"query": "approved budget"})["results"][0]
    assert found["document"] == "mail.eml"


def test_logs_as_jsonl_and_json_arrays_load_as_rows_with_columns(tmp_path):
    (tmp_path / "audit.jsonl").write_text(
        '{"ts":"t1","actor":"a1","detail":{"ip":"10.0.0.1","tags":["x","y"]}}\n'
        "\n"
        "this line is damaged\n"
        '{"ts":"t2","actor":"a2","extra":true}\n'
        "[1, 2]\n"
    )
    (tmp_path / "export.json").write_text(
        json.dumps({"generated": "now", "events": [{"id": 1, "kind": "login"}, {"id": 2}]})
    )
    (tmp_path / "config.json").write_text('{\n  "retention_days": 30\n}\n')
    (tmp_path / "service.log").write_text("Nov 14 01:00:00 hub sshd[1]: accepted\n")
    registry = ToolRegistry(tmp_path)
    inventory = {entry["document"]: entry for entry in registry.inventory()}
    assert inventory["audit.jsonl"] == {
        "document": "audit.jsonl",
        "units": 4,
        "columns": ["ts", "actor", "detail.ip", "detail.tags", "extra"],
    }
    assert inventory["export.json"]["columns"] == ["id", "kind"]
    assert "columns" not in inventory["config.json"] and inventory["service.log"]["units"] == 1
    rows = registry.execute(
        "table", {"document": "audit.jsonl", "where": {"detail.ip": "10.0.0.1"}}
    )
    assert rows["matched_rows"] == 1 and rows["rows"][0]["locator"] == "line:1"
    assert rows["rows"][0]["values"]["detail.tags"] == '["x","y"]'
    # The damaged line is no table row, yet stays readable and citable as text.
    read = registry.execute("read", {"document": "audit.jsonl"})
    assert [row["locator"] for row in read["results"]] == ["line:1", "line:3", "line:4", "line:5"]
    events = registry.execute("table", {"document": "export.json", "where": {"id__gte": 2}})
    assert events["rows"][0]["locator"] == "events:2"
    assert "error" in registry.execute("table", {"document": "config.json"})
    assert "error" in registry.execute("table", {"document": "service.log"})
    (tmp_path / "broken.json").write_text("{not json")
    assert "error" in registry.execute("read", {"document": "broken.json"})


def test_group_and_distinct_report_counts_with_a_citable_first_row(tmp_path):
    (tmp_path / "audit.csv").write_text(
        "actor,action\na1,login\na2,login\na1,read_secret\na1,login\n,login\n"
    )
    registry = ToolRegistry(tmp_path)
    grouped = registry.execute(
        "table", {"document": "audit.csv", "operation": "group", "column": "actor", "limit": 2}
    )
    assert [(group["value"], group["count"]) for group in grouped["groups"]] == [("a1", 3), ("", 1)]
    assert grouped["distinct_values"] == 3 and grouped["truncated"] is True
    assert grouped["groups"][0]["locator"] == "row:2"
    filtered = registry.execute(
        "table",
        {
            "document": "audit.csv",
            "operation": "distinct",
            "column": "action",
            "where": {"actor": "a1"},
        },
    )
    assert [group["value"] for group in filtered["groups"]] == ["login", "read_secret"]
    citation = Citation.model_validate(
        {key: filtered["groups"][1][key] for key in ("document", "locator", "quote")}
    )
    assert registry.verify_citations([citation]) == []
    assert "error" in registry.execute("table", {"document": "audit.csv", "operation": "group"})


def test_search_index_is_reused_until_a_document_changes(tmp_path):
    (tmp_path / "a.txt").write_text("the staged payload ran\nnothing else\n")
    registry = ToolRegistry(tmp_path)
    assert registry.execute("search", {"query": "payload"})["matched_units"] == 1
    index = registry._index
    assert registry.execute("search", {"query": "nothing"})["results"][0]["locator"] == "line:2"
    assert registry._index is index
    (tmp_path / "a.txt").write_text("the staged payload ran\npayload again\n")
    assert registry.execute("search", {"query": "payload"})["matched_units"] == 2
    assert registry._index is not index


def test_logs_are_tables_whatever_they_are_called(tmp_path):
    events = [
        {"time": "2026-11-14T02:00:00Z", "agent": "a1", "event": "login"},
        {"time": "2026-11-14T02:01:00Z", "agent": "a2", "event": "exec_payload"},
    ]
    lines = "".join(json.dumps(event) + "\n" for event in events)
    # JSON lines under a log suffix, and without any suffix at all.
    (tmp_path / "audit.log").write_text(lines)
    (tmp_path / "audit").write_text(lines)
    (tmp_path / "hub.events").write_text(lines)
    (tmp_path / "gateway.log").write_text(
        '2026-11-14T02:00:00Z INFO agent=a1 event=login msg="signed in" src=10.0.0.1\n'
        '2026-11-14T02:01:00Z WARN agent=a2 event=exec_payload msg="ran \\"x\\"" src=10.0.0.2\n'
    )
    (tmp_path / "plain.log").write_text("level=info agent=a1 event=login\nlevel=warn agent=a2\n")
    (tmp_path / "syslog.log").write_text(
        "Nov 14 02:00:00 hub sshd[1]: accepted key for a1\nNov 14 02:01:00 hub sshd[1]: closed\n"
    )
    (tmp_path / "blob.dat").write_bytes(b"MZ\x00\x01\x02\x03 not text")
    registry = ToolRegistry(tmp_path)
    for document in ("audit.log", "audit", "hub.events"):
        assert registry.columns(document) == ["time", "agent", "event"], document
        hit = registry.execute("table", {"document": document, "where": {"event": "exec_payload"}})
        assert hit["matched_rows"] == 1 and hit["rows"][0]["locator"] == "line:2"
    assert registry.columns("gateway.log") == ["ts", "level", "agent", "event", "msg", "src"]
    row = registry.execute("table", {"document": "gateway.log", "where": {"level": "WARN"}})
    assert row["rows"][0]["values"]["msg"] == 'ran "x"' and row["rows"][0]["locator"] == "line:2"
    assert registry.columns("plain.log") == ["level", "agent", "event"]
    # Prose stays prose: lines to read and search, not a table.
    assert registry.columns("syslog.log") == []
    assert (
        registry.execute("search", {"query": "accepted key"})["results"][0]["locator"] == "line:1"
    )
    # The quote of a table row is the line as it stands in the file.
    read = registry.execute("read", {"document": "gateway.log", "limit": 1})["results"][0]
    assert read["quote"].startswith("2026-11-14T02:00:00Z INFO agent=a1")
    assert registry.verify_citations([Citation.model_validate(read)]) == []
    assert "Unsupported" in registry.execute("read", {"document": "blob.dat"})["error"]


def test_a_json_export_with_several_record_lists_exposes_all_of_them(tmp_path):
    (tmp_path / "state.json").write_text(
        json.dumps(
            {
                "generated": "2026-11-14T03:00:00Z",
                "agents": [{"id": "a1", "role": "worker"}, {"id": "a2", "role": "worker"}],
                "tokens": [
                    {"id": "tok-1", "owner": "a1"},
                    {"id": "tok-2", "owner": "a2"},
                    {"id": "tok-3", "owner": "a2"},
                ],
            }
        )
    )
    (tmp_path / "registry.json").write_text(
        json.dumps(
            {
                "agent-1": {"status": "active", "team": "ops"},
                "agent-2": {"status": "quarantined", "team": "ops"},
            }
        )
    )
    registry = ToolRegistry(tmp_path)
    # Only the longest list used to be visible; the agents were not.
    assert registry.columns("state.json") == ["_table", "id", "role", "owner"]
    agents = registry.execute("table", {"document": "state.json", "where": {"_table": "agents"}})
    assert agents["matched_rows"] == 2 and agents["rows"][1]["locator"] == "agents:2"
    owned = registry.execute(
        "table", {"document": "state.json", "where": {"_table": "tokens", "owner": "a2"}}
    )
    assert [row["values"]["id"] for row in owned["rows"]] == ["tok-2", "tok-3"]
    # Records stored under their ID keep it as a column.
    assert registry.columns("registry.json") == ["_key", "status", "team"]
    found = registry.execute(
        "table", {"document": "registry.json", "where": {"status": "quarantined"}}
    )
    assert found["rows"][0]["values"]["_key"] == "agent-2"
    citation = Citation.model_validate(
        {key: found["rows"][0][key] for key in ("document", "locator", "quote")}
    )
    assert registry.verify_citations([citation]) == []


def test_the_time_column_is_found_by_name_or_by_its_values(tmp_path):
    rows = [
        ("2026-11-14T02:05:00Z", "a1", "create_token", "tok-late"),
        ("2026-11-14T02:01:00Z", "a1", "create_token", "tok-early"),
        ("2026-11-14T02:03:00Z", "a1", "exec_payload", "ops"),
        ("2026-11-14T02:04:00Z", "a2", "create_token", "tok-clean"),
    ]
    for name, column in (("named.csv", "event_time"), ("values.csv", "moment")):
        (tmp_path / name).write_text(
            f"seq,{column},actor,action,target\n"
            + "".join(f"{i},{','.join(row)}\n" for i, row in enumerate(rows, 1))
        )
    (tmp_path / "untimed.csv").write_text("actor,count\na1,3\na2,20261114\n")
    registry = ToolRegistry(tmp_path)
    assert registry.time_column("named.csv") == "event_time"
    # No usual name: the column whose every value is a timestamp. A column of
    # bare numbers is not mistaken for one.
    assert registry.time_column("values.csv") == "moment"
    assert registry.time_column("untimed.csv") is None
    for document in ("named.csv", "values.csv"):
        minted, compromised = registry.after(
            document, "actor", {"action": "exec_payload"}, {"action": "create_token"}
        )
        # Only what the compromised actor did from its compromise on, in time order.
        assert [unit.values["target"] for unit in minted] == ["tok-late"] and compromised == 1
    with pytest.raises(tools.ToolError, match="no time column"):
        registry.after("untimed.csv", "actor", {"count": "3"})
    with pytest.raises(tools.ToolError, match="key must be a column"):
        registry.after("named.csv", "agent", {"action": "exec_payload"})


def test_a_document_is_parsed_once_for_all_tasks_until_it_changes(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"actor":"a1","action":"login"}\n')
    parsed = []
    original = ToolRegistry._parse

    def counting(self, name, *args):
        parsed.append(name)
        return original(self, name, *args)

    monkeypatch.setattr(ToolRegistry, "_parse", counting)
    monkeypatch.setattr(tools, "_SHARED", type(tools._SHARED)())
    first = ToolRegistry(tmp_path, ["audit.jsonl"])
    assert len(first.units("audit.jsonl")) == 1 and len(first.units("audit.jsonl")) == 1
    # The next task's registry reuses the parsed rows of the same bytes.
    second = ToolRegistry(tmp_path, ["audit.jsonl"])
    assert second.units("audit.jsonl") is first.units("audit.jsonl") and parsed == ["audit.jsonl"]
    path.write_text('{"actor":"a1","action":"login"}\n{"actor":"a2","action":"login"}\n')
    assert len(second.units("audit.jsonl")) == 2 and len(first.units("audit.jsonl")) == 2
    assert parsed == ["audit.jsonl", "audit.jsonl"]
    # The store is bounded: the oldest document goes first.
    monkeypatch.setattr(tools, "MAX_SHARED_UNITS", 2)
    (tmp_path / "other.jsonl").write_text('{"x":1}\n')
    third = ToolRegistry(tmp_path)
    third.units("other.jsonl")
    assert [key[2] for key in tools._SHARED] == ["other.jsonl"]


def test_documents_are_listed_without_hidden_files_and_binaries(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "logs" / "audit.jsonl").write_text("{}\n")
    (tmp_path / "runbook.md").write_text("Quarantine.\n")
    (tmp_path / "events").write_text("level=info agent=a1\n")
    (tmp_path / ".env").write_text("SECRET=1\n")
    (tmp_path / ".git" / "config.txt").write_text("hidden\n")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00")
    assert list_documents(tmp_path) == ["events", "logs/audit.jsonl", "runbook.md"]
    assert len(list_documents(tmp_path, limit=2)) == 2
    assert list_documents(tmp_path / "missing") == []
    # A registry without a document list sees the same documents.
    assert [entry["document"] for entry in ToolRegistry(tmp_path).inventory()] == [
        "events",
        "logs/audit.jsonl",
        "runbook.md",
    ]


def test_a_task_without_documents_needs_no_data_directory(tmp_path):
    registry = ToolRegistry(tmp_path / "missing", [])
    assert registry.execute("calculate", {"expression": "2 + 3"})["value"] == "5"
    assert registry.inventory() == []
    with pytest.raises(OSError):
        ToolRegistry(tmp_path / "missing", ["a.txt"])
