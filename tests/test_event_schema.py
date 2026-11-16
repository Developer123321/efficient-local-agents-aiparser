"""Columns are found by their values, not only by the dev workspace's headers.

Synthetic clinical data: different file names, different column names, and two
tables that both look like a roster. No real patient record is used and no
model is called.
"""

from hacklab.event import schema
from hacklab.event.incident import Incident


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


ENCOUNTERS = """patient_id,encounter_ts,clinician,event_code,resource,disposition
P-1001,2026-11-14T01:00:00Z,dr-7,review_chart,ward-a,ok
P-1002,2026-11-14T02:00:00Z,dr-9,review_chart,ward-b,ok
P-1003,2026-11-14T03:00:00Z,dr-7,order_lab,ward-a,blocked
P-1004,2026-11-14T04:00:00Z,dr-9,order_lab,ward-b,ok
"""


def clinical(tmp_path):
    write(
        tmp_path,
        "AGENTS.md",
        'Incident INC-CLIN-01 is open.\nCurrent time ("now"): 2026-11-14T07:30:00Z (UTC).\n',
    )
    write(tmp_path, "runbook.md", "# Runbook\nA transfer counts outside acme.example.\n")
    write(tmp_path, "clinical/encounters.csv", ENCOUNTERS)
    write(
        tmp_path,
        "clinical/clinicians.csv",
        "clinician_id,clinician_name,department\ndr-7,Rao,ward-a\ndr-9,Ito,ward-b\n",
    )
    write(
        tmp_path,
        "clinical/patients.csv",
        "patient_id,patient_name,birth_year\nP-1001,Ada Example,1970\nP-1002,Bo Example,1981\n",
    )
    write(
        tmp_path,
        "clinical/contacts.csv",
        "person,job,reach,verified\nAda Berg,commander,ada@acme.example,yes\n",
    )
    write(
        tmp_path,
        "clinical/notes/shift.md",
        "Shift report\n1. The queue is empty.\n2. The review is done.\n",
    )
    return tmp_path


def test_roles_come_from_values_not_headers():
    rows = [
        {
            "patient_id": "P-1001",
            "encounter_ts": "2026-11-14T01:00:00Z",
            "clinician": "dr-7",
            "event_code": "review_chart",
            "resource": "ward-a",
            "disposition": "ok",
        },
        {
            "patient_id": "P-1002",
            "encounter_ts": "2026-11-14T02:00:00Z",
            "clinician": "dr-9",
            "event_code": "order_lab",
            "resource": "ward-b",
            "disposition": "blocked",
        },
    ]
    roles = schema.infer_roles(rows)
    assert roles["time"] == "encounter_ts"
    assert roles["actor"] == "clinician"
    assert roles["action"] == "event_code"
    assert roles["target"] == "resource"
    assert roles["status"] == "disposition"
    assert roles["id"] == "patient_id"
    assert "email" not in roles


def test_csv_events_read_with_unknown_headers(tmp_path):
    incident = Incident(clinical(tmp_path))
    events = incident.events
    assert [(event.agent, event.action, event.target, event.status) for event in events] == [
        ("dr-7", "review_chart", "ward-a", "ok"),
        ("dr-9", "review_chart", "ward-b", "ok"),
        ("dr-7", "order_lab", "ward-a", "blocked"),
        ("dr-9", "order_lab", "ward-b", "ok"),
    ]


def test_roster_is_the_table_the_log_points_at(tmp_path):
    incident = Incident(clinical(tmp_path))
    # Two CSVs look like a roster; the clinicians' ids are the log's actors.
    assert incident.role("agents") == "/clinical/clinicians.csv"
    assert incident.roster_id == "clinician_id"
    assert incident.roster[0]["clinician_name"] == "Rao"


def test_a_table_without_a_time_or_act_is_not_an_event_log():
    rows = [
        {"ward_id": "ward-a", "lead": "Rao", "beds": "12"},
        {"ward_id": "ward-b", "lead": "Ito", "beds": "8"},
    ]
    roles = schema.infer_roles(rows)
    assert "time" not in roles
    assert "action" not in roles


def test_status_needs_its_vocabulary_not_only_a_short_column():
    rows = [
        {"code": "alpha", "place": "north"},
        {"code": "beta", "place": "south"},
    ]
    roles = schema.infer_roles(rows)
    assert "status" not in roles
