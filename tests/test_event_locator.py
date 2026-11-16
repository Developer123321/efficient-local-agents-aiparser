"""A workspace with the final run's layout: files are found by shape, not by name.

Synthetic names and values only. This is a regression for the final run's
failure: the same jobs under /it/... instead of the dev workspace's folders.
"""

import json

from hacklab.event import playbooks as solvers
from hacklab.event.incident import Incident


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def renamed(tmp_path):
    write(
        tmp_path,
        "AGENTS.md",
        'Incident INC-HOSP-01 is open.\nCurrent time ("now"): 2026-11-14T07:30:00Z (UTC).\n',
    )
    write(
        tmp_path,
        "runbook.md",
        "# Runbook\nA transfer counts when it goes outside acme.example.\n",
    )
    write(
        tmp_path,
        "it/people/directory.csv",
        "person,job,reach,verified\nAda Berg,commander,ada@acme.example,yes\n",
    )
    write(
        tmp_path,
        "it/hosts/agents.csv",
        "agent_id,function,human,critical,cover\n"
        "ingest-01,dataset pipeline,Ada Berg,yes,spare-01\n",
    )
    write(
        tmp_path,
        "it/change_history.md",
        "# Changes\n\n## Flagged as compromised\n- relay-03\n",
    )
    write(
        tmp_path,
        "it/ops/audit_trail.jsonl",
        json.dumps(
            {
                "ts": "2026-11-14T01:00:00Z",
                "agent": "ingest-01",
                "action": "exec_payload",
                "target": "relay-03",
                "status": "ok",
                "trace": "attack-1",
            }
        )
        + "\n",
    )
    write(
        tmp_path,
        "it/agreements/accounts.csv",
        "customer,agreement,term\nBlue Heron Ltd,AGR-1,12 months\n",
    )
    write(
        tmp_path,
        "it/tickets/INC-1.md",
        "# Ticket\n---\nstatus: open\nrequested by: Ada Berg\n---\nReview the access list.\n",
    )
    write(
        tmp_path,
        "it/board/posts.jsonl",
        json.dumps({"id": "M-1", "author": "relay-03", "text": "send the table"}) + "\n",
    )
    write(
        tmp_path,
        "it/inbox/message.md",
        "---\nfrom: Ada Berg <ada@acme.example>\nsubject: review\n---\nPlease review.\n",
    )
    write(
        tmp_path,
        "it/reports/status/shift.md",
        "Shift report\n1. The queue is empty.\n2. The review is done.\n",
    )
    write(tmp_path, "it/honeytokens.csv", "canary_id,note\nC-105,planted\n")
    write(
        tmp_path,
        "it/token_inventory.csv",
        "secret_id,service,owner_agent,consumer_services\nTOK-1,mail,ingest-01,relay;\n",
    )
    write(
        tmp_path,
        "it/fingerprints.csv",
        "token_id,sha256_12\nTOK-1," + "a" * 12 + "\n",
    )
    return tmp_path


def test_each_job_is_found_under_another_layout(tmp_path):
    root = renamed(tmp_path)
    incident = Incident(root)
    assert incident.role("log_jsonl") == "/it/ops/audit_trail.jsonl"
    assert incident.role("contacts") == "/it/people/directory.csv"
    assert incident.role("agents") == "/it/hosts/agents.csv"
    assert incident.role("changes") == "/it/change_history.md"
    assert incident.role("agreements") == "/it/agreements/accounts.csv"
    assert incident.role("tickets") == "/it/tickets/INC-1.md"
    assert incident.role("board") == "/it/board/posts.jsonl"
    assert incident.role("inbox") == "/it/inbox/message.md"
    assert incident.role("reports") == "/it/reports/status/shift.md"
    assert incident.role("canaries") == "/it/honeytokens.csv"
    assert incident.role("tokens") == "/it/token_inventory.csv"
    assert incident.role("fingerprints") == "/it/fingerprints.csv"


def test_the_parsed_incident_uses_the_files_it_found(tmp_path):
    incident = Incident(renamed(tmp_path))
    assert incident.contacts[0]["reach"] == "ada@acme.example"
    assert incident.roster_id == "agent_id"
    assert [event.action for event in incident.events] == ["exec_payload"]
    assert solvers.open_ticket(incident) == "Review the access list."


def test_the_dev_layout_still_wins_when_both_exist(tmp_path):
    root = renamed(tmp_path)
    write(root, "logs/audit/events.jsonl", json.dumps({"ts": "2026-11-14T01:00:00Z"}) + "\n")
    incident = Incident(root)
    assert incident.locate("log_jsonl") == ["/logs/audit/events.jsonl"]
