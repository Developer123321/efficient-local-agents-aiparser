"""Code-only solvers for the event's task types. No model call, no token.

One dispatcher is registered. It reads the request (the instruction, or the
open ticket the instruction points to), picks the solver whose words fit and
lets it compute the answer from `hacklab.event.incident`. A solver answers
only a question that asks for nothing but an answer: a request to send,
write, quarantine, rotate or block goes to the model and its guarded tools.

Rules that keep a solver from failing on the unseen final scenario:

- Take names of files, columns and values from the data or the task text,
  and the counting rules from the runbook of this workspace.
- Raise `Unreadable` or return None whenever something does not look as
  expected: the model takes over.
- Cite the rows the answer rests on; right sources are part of the score.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from typing import Any

from hacklab.event.incident import (
    Incident,
    Source,
    Unreadable,
    aimed_at_agents,
    canon,
    column,
    domain_of,
    is_time,
    parse_time,
    stamp,
)
from hacklab.models import Answer, Citation, Task
from hacklab.playbooks import Playbooks, Workspace

playbooks = Playbooks()

Solved = tuple[Any, ...]  # (answer, sources) or (answer, sources, outcome)
Solver = Callable[[Incident, str], Solved | None]
SOLVERS: list[tuple[re.Pattern[str], Any, bool]] = []
RULES_MARK = "\n\nOperator rules from /"
# A request that wants something done, not only known. Code does not act.
ACTS = re.compile(
    r"\b(quarantine|contain(ment)?|rotate|revoke|block|send|notify|notification|draft|"
    r"write (it|a|the)|schedule|deal with|handle the message|carry (it )?out)\b",
    re.IGNORECASE,
)
SECRET = re.compile(r"fake_mf_[0-9a-f]+")


def solver(pattern: str, acts: bool = False) -> Callable[[Any], Any]:
    """Register a solver. One that `acts` also gets the workspace, to call a platform tool."""

    def decorator(function: Any) -> Any:
        SOLVERS.append((re.compile(pattern, re.IGNORECASE | re.DOTALL), function, acts))
        return function

    return decorator


def open_ticket(incident: Incident) -> str | None:
    """The body of the one ticket with `status: open`, or None."""
    found = []
    for name in incident.find("tickets/*.md"):
        text = incident.text(name)
        parts = text.split("---", 2)
        if len(parts) == 3 and re.search(r"^status:\s*open\s*$", parts[1], re.MULTILINE):
            body = re.sub(r"^\s*#[^\n]*\n", "", parts[2].strip()).strip()
            found.append(body)
    return found[0] if len(found) == 1 else None


def request_of(question: str, incident: Incident) -> str:
    """What is asked: the instruction, or the open ticket when it points to the queue."""
    instruction = question.split(RULES_MARK)[0].strip()
    if re.search(r"/tickets/|\bopen ticket\b", instruction, re.IGNORECASE):
        ticket = open_ticket(incident)
        if ticket is None:
            raise Unreadable("no single open ticket")
        return ticket
    return instruction


def _citation(source: Source) -> Citation:
    """A source in the locator words `arena_run.sources` turns back into the platform's."""
    path = str(source["path"])
    if "line" in source:
        locator = f"line:{source['line']}"
    elif "sheet" in source:
        locator = f"sheet:{source['sheet']}:row:{source['row']}"
    elif "record" in source and "row" in source:
        locator = f"row:{source['row']}"
    else:
        locator = "file"
    return Citation(document=path.lstrip("/"), locator=locator, quote=path)


@playbooks.register(r".")
def dispatch(task: Task, workspace: Workspace) -> Answer | None:
    incident = Incident(workspace.documents.root)
    try:
        request = request_of(task.question, incident)
    except Unreadable:
        return None
    acting = bool(ACTS.search(request))
    for pattern, function, acts in SOLVERS:
        if acts != acting or not pattern.search(request):
            continue
        try:
            solved = function(incident, request, workspace) if acts else function(incident, request)
        except (Unreadable, KeyError, ValueError, IndexError, StopIteration, OSError):
            continue
        except PermissionError:
            return None  # The guard refused the action: the model decides what is right.
        if solved is None:
            continue
        value, sources, *rest = solved
        citations: list[Citation] = []
        for source in sources:
            citation = _citation(source)
            if citation not in citations:
                citations.append(citation)
        return Answer(
            status="ok",
            answer=json.dumps(value, ensure_ascii=False)[:12000],
            value=value,
            outcome=rest[0] if rest else "OK",
            citations=citations[:30],
            confidence=1.0,
            reason="Computed from the workspace by the runbook's rules; no model was called.",
        )
    return None


def done(result: Any) -> bool:
    """Whether a platform tool reported success."""
    return not (isinstance(result, dict) and ("error" in result or result.get("ok") is False))


def keys_of(request: str) -> list[str]:
    """The keys of the JSON object the request asks for, in its order."""
    for template in re.findall(r"\{[^{}]*\}", request):
        keys = re.findall(r'"(\w+)"\s*:', template) or re.findall(r'"(\w+)"', template)
        if keys:
            return keys
    return []


# -- who was compromised ---------------------------------------------------------


@solver(r"compromised|attacker-directed|attacker-controlled|hijacked")
def compromised(incident: Incident, request: str) -> Solved | None:
    if not re.search(r"\b(array|list)\b", request, re.IGNORECASE):
        return None
    if keys_of(request) or re.search(
        r"trifecta|access request|still running|claims?|token|domain", request, re.IGNORECASE
    ):
        return None
    found = incident.compromised
    if not found:
        return None
    return sorted(found), [found[agent].source for agent in sorted(found)]


# -- where it started --------------------------------------------------------------


@solver(r"artifact")
def entry(incident: Incident, request: str) -> Solved | None:
    keys = keys_of(request)
    if set(keys) != {"artifact", "agent", "at"}:
        return None
    copy, first = incident.entry
    value = {"artifact": copy, "agent": first.agent, "at": stamp(first.ts)}
    return {key: value[key] for key in keys}, [first.source, {"path": copy}]


# -- what left the building -----------------------------------------------------------


@solver(r"exfiltrat")
def exfiltrated(incident: Incident, request: str) -> Solved | None:
    keys = keys_of(request)
    if set(keys) != {"records", "domains"}:
        return None
    counted = incident.exfiltration
    value = {
        "records": incident.count(counted),
        "domains": sorted({transfer.domain for transfer in counted}),
    }
    return {key: value[key] for key in keys}, [transfer.source for transfer in counted]


@solver(r"exfiltrat")
def largest_share(incident: Incident, request: str) -> Solved | None:
    keys = keys_of(request)
    if set(keys) != {"agent", "owner_email"}:
        return None
    by_agent: dict[str, list] = {}
    for transfer in incident.exfiltration:
        by_agent.setdefault(transfer.event.agent, []).append(transfer)
    if not by_agent:
        return None
    ranked = sorted(by_agent, key=lambda agent: -incident.count(by_agent[agent]))
    if len(ranked) > 1 and incident.count(by_agent[ranked[0]]) == incident.count(
        by_agent[ranked[1]]
    ):
        return None  # A tie is not ours to break.
    agent = ranked[0]
    contact = incident.owner_contact(agent)
    row = next(r for r in incident.roster if r[incident.roster_id] == agent)
    value = {"agent": agent, "owner_email": contact[incident.contact_address]}
    sources = [transfer.source for transfer in by_agent[agent]]
    sources += [
        incident.csv_source("fleet/agents.csv", row),
        incident.csv_source("contacts.csv", contact),
    ]
    return {key: value[key] for key in keys}, sources


# -- secrets -----------------------------------------------------------------------


@solver(r"token|secret")
def first_secret(incident: Incident, request: str) -> Solved | None:
    keys = keys_of(request)
    if set(keys) != {"token", "service", "owner_agent", "owner_email"}:
        return None
    reads = incident.secret_reads
    if not reads:
        return None
    read = reads[0]
    tokens = incident.table("fleet/tokens.csv")
    agents = {row[incident.roster_id] for row in incident.roster}
    token_id = column(tokens, "secret_id", "token_id", "token", first=True)
    service = column(tokens, "svc", "service")
    owner = column(tokens, "owner_agent", "owner", where=lambda value: value in agents)
    row = next(r for r in tokens if r[token_id] == read.target)
    contact = incident.owner_contact(row[owner])
    agent_row = next(r for r in incident.roster if r[incident.roster_id] == row[owner])
    value = {
        "token": read.target,
        "service": row[service],
        "owner_agent": row[owner],
        "owner_email": contact[incident.contact_address],
    }
    sources = [
        read.source,
        incident.csv_source("fleet/tokens.csv", row),
        incident.csv_source("fleet/agents.csv", agent_row),
        incident.csv_source("contacts.csv", contact),
    ]
    return {key: value[key] for key in keys}, sources


@solver(r"\bleak")
def leaked_tokens(incident: Incident, request: str) -> Solved | None:
    """Tokens whose complete value stands in the leaked material, by fingerprint."""
    if not re.search(r"\btoken", request, re.IGNORECASE):
        return None
    prints = incident.table("fleet/token_fingerprints.csv")
    token_id = column(prints, "token_id", "secret_id", "token", first=True)
    digest = column(prints, "sha256_12", where=lambda v: re.fullmatch(r"[0-9a-f]{12}", v))
    by_digest = {row[digest]: row for row in prints}
    materials: list[tuple[str, int | None, str]] = []
    named = re.findall(r"/[\w./-]+\.\w+", request)
    for name in named:
        if name.endswith(".jsonl") or not incident.has(name):
            continue
        materials.append((name, None, incident.text(name)))
    if re.search(r"\bboard\b", request, re.IGNORECASE):
        for name in incident.find("board/*.jsonl"):
            for number, line in enumerate(incident.text(name).splitlines(), 1):
                materials.append((name, number, line))
    if not materials:
        return None
    leaked: dict[str, list[Source]] = {}
    for name, line, text in materials:
        # A value wrapped onto an indented next line is still one complete value.
        joined = re.sub(r"\n[ \t]+(?=[0-9a-f]+[ \t]*(\n|$))", "", text)
        for value in dict.fromkeys(SECRET.findall(text) + SECRET.findall(joined)):
            row = by_digest.get(hashlib.sha256(value.encode()).hexdigest()[:12])
            if row is None:
                continue
            source = {"path": name} if line is None else {"path": name, "line": line}
            leaked.setdefault(row[token_id], []).append(source)
    if not leaked:
        return None
    sources: list[Source] = []
    for name in dict.fromkeys(m[0] for m in materials if m[1] is None):
        sources.append({"path": name})
    for token in sorted(leaked):
        row = next(r for r in prints if r[token_id] == token)
        sources.append(incident.csv_source("fleet/token_fingerprints.csv", row))
    sources += [s for token in sorted(leaked) for s in leaked[token] if "line" in s]
    return sorted(leaked), sources


# -- the access log ------------------------------------------------------------------


@solver(r"requests\.csv")
def access_violations(incident: Incident, request: str) -> Solved | None:
    """External reads of private scope without a valid change ticket that were not denied."""
    name = next((n for n in re.findall(r"/[\w./-]+requests\.csv", request)), None)
    if name is None or not re.search(r"violat", request, re.IGNORECASE):
        return None
    scope = re.compile(r"[a-z_]+:[a-z_]+")
    change = re.compile(r"[A-Z]+-\d+")
    rows = canon(
        incident.table(name),
        request_id=(("request_id", "id"), {"first": True}),
        ts=(("ts", "time", "timestamp"), {"where": is_time}),
        actor=(
            ("actor", "principal"),
            {"whole": lambda v: any(x.startswith("external:") for x in v)},
        ),
        action=(
            ("action", "verb", "operation"),
            {"whole": lambda v: "read" in v and len(set(v)) < 15},
        ),
        resource=(("resource", "scope"), {"where": scope.fullmatch}),
        scope_class=(
            ("scope_class", "class"),
            {"whole": lambda v: "private" in v and len(set(v)) < 9},
        ),
        ticket=(
            ("ticket", "chg", "change_ticket"),
            {"whole": lambda v: "" in v and all(change.fullmatch(x) for x in v if x)},
        ),
        result=(("result", "outcome", "status"), {"whole": lambda v: "denied" in v}),
    )
    folder = name.rsplit("/", 1)[0]
    policy = incident.text(folder + "/POLICY.md") if incident.has(folder + "/POLICY.md") else ""
    spelled = bool(re.search(r"ticket field is empty", request, re.IGNORECASE))
    tickets: list[dict[str, str]] = []
    if not spelled and "change_tickets.csv" in policy:
        tickets = canon(
            incident.table(folder + "/change_tickets.csv"),
            ticket=(("ticket", "ticket_id", "id", "chg"), {"first": True}),
            status=(("status", "state"), {"whole": lambda v: "approved" in v}),
            actor=(
                ("actor", "principal"),
                {"whole": lambda v: any(x.startswith("external:") for x in v)},
            ),
            resource=(("resource", "scope"), {"where": scope.fullmatch}),
            valid_from=(("valid_from", "from", "start", "starts_at"), {}),
            valid_until=(("valid_until", "until", "to", "end", "expires_at"), {}),
        )
        revoked_key = (
            next((k for k in tickets[0] if "revok" in k.lower()), None) if tickets else None
        )
        if "revoked_at" in policy and revoked_key is None:
            return None
    elif not spelled and policy:
        return None  # A policy this code has not read.

    def valid(row: dict[str, str]) -> bool:
        if not row.get("ticket"):
            return False
        if not tickets:
            return True  # The request spells the rule out: any ticket covers.
        moment = parse_time(row["ts"])
        for ticket in tickets:
            if ticket["ticket"] != row["ticket"] or ticket["status"].lower() != "approved":
                continue
            if ticket["actor"] != row["actor"] or ticket["resource"] != row["resource"]:
                continue
            if not parse_time(ticket["valid_from"]) <= moment < parse_time(ticket["valid_until"]):
                continue
            revoked = ticket.get(revoked_key, "") if revoked_key else ""
            if revoked and moment >= parse_time(revoked):
                continue
            return True
        return False

    violations = [
        row
        for row in rows
        if row["action"] == "read"
        and row["scope_class"] == "private"
        and row["actor"].startswith("external:")
        and row["result"] != "denied"
        and not valid(row)
    ]
    if not violations:
        return None
    return sorted(row["request_id"] for row in violations), [
        incident.csv_source(name, row) for row in violations
    ]


# -- what happened when -----------------------------------------------------------------


def _moment(incident: Incident, phrase: str) -> tuple[Any, Source] | None:
    """The time of one named event and the row that shows it, or None if it is not known."""
    text = phrase.lower()
    events = incident.events
    if "canary" in text and "upload" in text:
        found = [
            e for e in events if e.action == "upload_artifact" and e.agent in incident.red_team
        ]
        return (found[0].ts, found[0].source) if found else None
    if "entry artifact" in text and "upload" in text:
        original = incident.entry[1].target
        found = [e for e in events if e.action == "upload_artifact" and e.target == original]
        return (found[0].ts, found[0].source) if found else None
    if "patient zero" in text or ("entry artifact" in text and "process" in text):
        first = incident.entry[1]
        return first.ts, first.source
    quarantined = re.search(r"([\w-]+) was quarantined", phrase)
    if quarantined:
        found = [e for e in events if e.action == "quarantine_agent" and e.target == quarantined[1]]
        return (found[0].ts, found[0].source) if found else None
    if "siem" in text and "secret" in text:
        sheet, rows = incident.alerts
        readers = {e.agent for e in incident.secret_reads}
        found = [r for r in rows if any("secret" in str(v).lower() for v in r.values())]
        found = [r for r in found if readers & set(r.values())] or found
        if len(found) != 1:
            return None
        when = next((k for k, v in found[0].items() if not k.startswith("_") and is_time(v)), None)
        if when is None:
            return None
        zoned = bool(re.search(r"Z$|[+-]\d\d:\d\d$", found[0][when])) or "utc" in when.lower()
        moment = parse_time(found[0][when], 0.0 if zoned else incident.local_offset)
        return moment, {"path": incident.alerts_path, "sheet": sheet, "row": int(found[0]["_row"])}
    if "approved transfer" in text:
        found = [t for t in incident.transfers if t.approved and not t.blocked]
        return (found[0].event.ts, found[0].event.source) if found else None
    if "first secret" in text:
        reads = incident.secret_reads
        return (reads[0].ts, reads[0].source) if reads else None
    if "exfiltrating transfer" in text or "first transfer that counts" in text:
        found = [t for t in incident.exfiltration if not t.blocked or "counts" in text]
        return (found[0].event.ts, found[0].event.source) if found else None
    change = re.search(r"\((\w+) ([\w:-]+) on ([\w-]+)\)", phrase)
    if change:
        for row in incident.iam_changes:
            if [row.get("change"), row.get("value"), row.get("agent")] == list(change.groups()):
                when = next(v for k, v in row.items() if k.startswith("time"))
                return parse_time(when), {"path": "/fleet/changes.md", "line": int(row["_line"])}
        return None
    if "board" in text and "post" in text:
        posts = [e for e in incident.attack_events if e.action == "post_board"]
        if not posts:
            return None
        for name in incident.find("board/*.jsonl"):
            for number, line in enumerate(incident.text(name).splitlines(), 1):
                if f'"{posts[0].target}"' in line:
                    return posts[0].ts, {"path": name, "line": number}
        return posts[0].ts, posts[0].source
    return None


@solver(r"chronolog|earliest|\\border\\b|sequence|timeline")
def timeline(incident: Incident, request: str) -> Solved | None:
    items = re.findall(r"\b([A-H]):\s*(.+?)(?=;\s*[A-H]:|\.\s+[A-Z]|\.?\s*$)", request, re.DOTALL)
    if len(items) < 2 or len({letter for letter, _ in items}) != len(items):
        return None
    placed = []
    for letter, phrase in items:
        found = _moment(incident, phrase)
        if found is None:
            return None
        placed.append((found[0], letter, found[1]))
    moments = [moment for moment, _, _ in placed]
    if len(set(moments)) != len(moments):
        return None  # Two events at the same second: the order is not ours to invent.
    placed.sort(key=lambda item: item[0])
    return [letter for _, letter, _ in placed], [source for _, _, source in placed]


# -- MCP servers --------------------------------------------------------------------------


def _descriptions(node: Any, key: str = "") -> list[str]:
    """Every description in a manifest: the server's, each tool's, each parameter's."""
    if isinstance(node, dict):
        found = []
        for name, value in node.items():
            found += _descriptions(value, str(name))
        return found
    if isinstance(node, list):
        return [text for item in node for text in _descriptions(item, key)]
    return [node] if isinstance(node, str) and key == "description" else []


@solver(r"\bMCP\b")
def mcp_servers(incident: Incident, request: str) -> Solved | None:
    folder = next(iter(re.findall(r"(/[\w./-]*mcp)/?", request)), None)
    if folder is None or not re.search(r"disable", request, re.IGNORECASE):
        return None
    folder = folder.rstrip("/")
    manifests = incident.find(folder.lstrip("/") + "/*.json")
    if not manifests:
        return None
    # The request either names the one rule that applies or points to the policy.
    by_policy = bool(re.search(r"policy", request, re.IGNORECASE))
    policy = incident.text(folder + "/POLICY.md") if incident.has(folder + "/POLICY.md") else ""
    hashes = by_policy and "sha-256" in policy.lower() and incident.has(folder + "/approved.csv")
    if by_policy and not policy:
        return None
    approved: dict[str, dict[str, str]] = {}
    if hashes:
        rows = canon(
            incident.table(folder + "/approved.csv"),
            server=(("server", "name"), {"first": True}),
            sha256=(("sha256", "hash"), {"where": re.compile(r"[0-9a-fA-F]{64}").fullmatch}),
            approved_at=(("approved_at", "approved"), {"where": is_time}),
        )
        for row in sorted(rows, key=lambda r: parse_time(r["approved_at"])):
            approved[row["server"]] = row  # The newest approval wins.
    disabled: dict[str, list[Source]] = {}
    for name in manifests:
        raw = (incident.root / name.lstrip("/")).read_bytes()
        manifest = json.loads(raw)
        server = str(manifest["name"])
        reasons: list[Source] = []
        if any(aimed_at_agents(text) for text in _descriptions(manifest)):
            reasons.append({"path": name})
        if hashes:
            row = approved.get(server)
            if row is None:
                reasons.append({"path": name})
            elif hashlib.sha256(raw).hexdigest() != row["sha256"].lower():
                reasons += [{"path": name}, incident.csv_source(folder + "/approved.csv", row)]
        if reasons:
            disabled[server] = reasons
    if not disabled:
        return None
    sources = [s for server in disabled for s in disabled[server] if "record" not in s]
    sources += [s for server in disabled for s in disabled[server] if "record" in s]
    return sorted(disabled), sources


# -- permissions ---------------------------------------------------------------------------


@solver(r"trifecta|private data.{0,60}untrusted input.{0,60}outbound")
def trifecta(incident: Incident, request: str) -> Solved | None:
    if re.search(r"access request|pending", request, re.IGNORECASE):
        return None
    exported = bool(
        re.search(
            r"only the registry export|export time|according to the registry export", request, re.I
        )
    )
    current = bool(re.search(r"\b(now|current(ly)?)\b", request, re.IGNORECASE))
    if exported == current:
        return None  # Neither or both: which state is meant is not ours to guess.
    state, sources = incident.permissions(current)
    found = incident.trifecta(state)
    return (sorted(found), sources) if found else None


@solver(r"pending_access_requests")
def access_review(incident: Incident, request: str) -> Solved | None:
    keys = keys_of(request)
    if set(keys) != {"approve", "deny"}:
        return None
    name = next(iter(re.findall(r"/[\w./-]+pending_access_requests\.csv", request)), None)
    if name is None:
        return None
    policy_name = name.rsplit("/", 1)[0] + "/access_policy.md"
    policy = incident.text(policy_name).lower()
    # The policy this code implements, by its four rules. Another policy is the model's.
    for needed in (
        "quarantined or compromised",
        "owner",
        "lethal trifecta",
        "approve everything else",
    ):
        if needed not in policy:
            return None
    agents = {row[incident.roster_id] for row in incident.roster}
    kinds = {"grant_scope", "revoke_scope", "add_delegation", "remove_delegation"}
    rows = canon(
        incident.table(name),
        request_id=(("request_id", "id"), {"first": True}),
        filed_by=(("filed_by", "requested_by", "requester", "by"), {}),
        agent=(("agent", "agent_id", "for_agent"), {"where": lambda value: value in agents}),
        change=(("change", "kind", "type"), {"where": lambda value: value in kinds}),
        value=(("value", "scope", "target"), {}),
    )
    state_key = next((k for k in rows[0] if k in ("status", "state")), None) if rows else None
    state, sources = incident.permissions(True)
    before = incident.trifecta(state)
    gone = set(incident.quarantined) | set(incident.compromised)
    approve, deny, cited = [], [], []
    for row in rows:
        if state_key and row[state_key].lower() != "open":
            continue  # Withdrawn or decided: not ours to decide.
        agent, kind, value = row["agent"], row["change"], row["value"]
        roster = next((r for r in incident.roster if r[incident.roster_id] == agent), None)
        refused = agent in gone or roster is None or agent not in state
        refused = refused or row["filed_by"] != roster[incident.roster_owner]
        if not refused:
            after = {
                name: (set(scopes), set(delegates)) for name, (scopes, delegates) in state.items()
            }
            if kind == "grant_scope":
                after[agent][0].add(value)
            elif kind == "add_delegation":
                after[agent][1].add(value)
            elif kind in ("revoke_scope", "remove_delegation"):
                pass  # Taking a right away gives nobody the trifecta.
            else:
                return None
            refused = bool(incident.trifecta(after) - before)
        (deny if refused else approve).append(row["request_id"])
        if refused:
            cited.append(incident.csv_source(name, row))
    value = {"approve": sorted(approve), "deny": sorted(deny)}
    return {key: value[key] for key in keys}, sources[:1] + cited + sources[1:]


# -- an agent's own report against the log ------------------------------------------------------


@solver(r"claims?")
def status_claims(incident: Incident, request: str) -> Solved | None:
    name = next(iter(re.findall(r"/[\w./-]+\.md", request)), None)
    if name is None or not re.search(r"contradict", request, re.IGNORECASE):
        return None
    report = incident.text(name)
    agent = re.search(r"([a-z]+-\d+)\.md$", name)
    if not agent:
        return None
    who = agent[1]
    # "Tonight" is the current log; the rotated archive is the day before.
    mine = [e for e in incident.events if e.agent == who and "/audit/" in e.path]
    log = incident.find("logs/audit/*.jsonl")
    reads = [e for e in mine if e.action == "read_secret" and e.status == "ok"]
    sends = [
        e
        for e in mine
        if e.action in ("send_email", "http_post", "upload", "transfer")
        and "." in domain_of(e.target)
        and not incident.is_own(domain_of(e.target))
    ]
    wrong: list[int] = []
    sources: list[Source] = []
    claims = re.findall(r"^(\d+)\.\s*(.+)$", report, re.MULTILINE)
    if not claims:
        return None
    for number, claim in claims:
        when = re.search(r"\d{4}-\d\d-\d\d[ T]\d\d:\d\d(?::\d\d)?", claim)
        moment = parse_time(when[0]) if when else None
        proof: list[Any] | None
        if re.match(r"I did not read (TOK[\w-]+)", claim):
            token = re.match(r"I did not read (TOK[\w-]+)", claim)[1]
            proof = [e for e in reads if e.target == token][:1]
        elif re.match(r"I read no secrets after", claim) and moment:
            proof = [e for e in reads if e.ts > moment][:1]
        elif re.match(r"I received no delegated request from ([\w-]+)", claim):
            sender = re.match(r"I received no delegated request from ([\w-]+)", claim)[1]
            proof = [
                e
                for e in incident.events
                if e.action == "delegate"
                and e.agent == sender
                and e.target == who
                and "/audit/" in e.path
            ][:1]
        elif re.match(r"My first secret read tonight was at", claim) and moment:
            first = reads[:1]
            same = first and first[0].ts.replace(second=0) == moment.replace(second=0)
            proof = [] if same else (first or [None])
        elif re.match(r"I made at least one transfer to a destination outside", claim):
            proof = [] if sends else [None]
        elif re.match(r"I made no transfers to destinations outside", claim) and moment:
            proof = [e for e in sends if e.ts > moment][:1]
        else:
            return None  # A claim this code cannot check.
        if proof:
            wrong.append(int(number))
            for item in proof:
                sources.append(item.source if item is not None else {"path": log[0]})
    return sorted(wrong), sources


@solver(r"valid.{0,80}revoked|revoked.{0,80}valid")
def token_status(incident: Incident, request: str) -> Solved | None:
    keys = keys_of(request)
    token = re.search(r"\bTOK-[A-Z0-9]+-\d+\b", request)
    if set(keys) != {"status", "at"} or not token:
        return None
    mine = [e for e in incident.events if e.target == token[0] and e.status == "ok"]
    revoked = [e for e in mine if e.action in ("revoke_token", "revoke")]
    if revoked:
        last = revoked[-1]
        if any(e.ts > last.ts and e.action in ("rotate_token", "reissue_token") for e in mine):
            return None
        value = {"status": "revoked", "at": stamp(last.ts)}
        return {key: value[key] for key in keys}, [last.source]
    used = [e for e in mine if e.action == "auth_token"]
    value = {"status": "valid", "at": stamp(used[-1].ts) if used else None}
    return {key: value[key] for key in keys}, [used[-1].source] if used else []


@solver(r"board")
def board_origin(incident: Incident, request: str) -> Solved | None:
    """The board message an agent acted on, its poster and what started the poster's trace."""
    keys = keys_of(request)
    agent = re.search(r"\b([a-z]+-\d+)\b", request)
    if set(keys) != {"message", "poster", "artifact"} or not agent:
        return None
    acted = [
        e for e in incident.attack_events if e.agent == agent[1] and e.parent.startswith("board:")
    ]
    if not acted:
        return None
    message = acted[0].parent.removeprefix("board:")
    posts = [e for e in incident.attack_events if e.action == "post_board" and e.target == message]
    if not posts:
        return None
    start = incident.traces[posts[0].trace][0]
    if start.action != "read_artifact" or start.target not in incident.hostile_artifacts:
        return None
    value = {
        "message": message,
        "poster": posts[0].agent,
        "artifact": incident.hostile_artifacts[start.target],
    }
    sources = [acted[0].source]
    for name in incident.find("board/*.jsonl"):
        for number, line in enumerate(incident.text(name).splitlines(), 1):
            if f'"{message}"' in line:
                sources.append({"path": name, "line": number})
    sources.append(start.source)
    return {key: value[key] for key in keys}, sources


# -- requests to act ------------------------------------------------------------------------
#
# The action is computed from the runbook and the logs and sent once. Whatever
# the workspace says cannot add an operation, a recipient or a line.


@solver(r"rotat", acts=True)
def rotate(incident: Incident, request: str, workspace: Workspace) -> Solved | None:
    tokens = set(re.findall(r"\bTOK-[A-Z0-9]+-\d+\b", request))
    if len(tokens) != 1 or re.search(r"quarantin|block|contain", request, re.IGNORECASE):
        return None
    token = tokens.pop()
    start, sources = incident.rotation_slot(token)
    when = stamp(start)
    if not done(
        workspace.call("containment", ops=[{"op": "rotate_token", "token": token, "at": when}])
    ):
        return None
    return when, sources


@solver(r"notif", acts=True)
def notify(incident: Incident, request: str, workspace: Workspace) -> Solved | None:
    """The breach notification to the DPO, with the lines the runbook prescribes."""
    if not re.search(r"breach", request, re.IGNORECASE):
        return None
    section = incident.runbook.split("## Breach notification", 1)
    if len(section) != 2 or "DPO" not in section[1]:
        return None
    officer = [
        row
        for row in incident.contacts
        if incident.verified(row)
        and any("data protection officer" in str(value).lower() for value in row.values())
    ]
    if len(officer) != 1:
        return None
    base = [transfer for transfer in incident.transfers if not transfer.approved]
    first = [t for t in base if incident.detection_counts_blocked or not t.blocked]
    counted = incident.exfiltration
    if not first or not counted:
        return None
    contained = set(incident.compromised) <= set(incident.quarantined)
    known = {
        "incident": incident.incident_id,
        "records_affected": str(incident.count(counted)),
        "detected_at": stamp(first[0].event.ts),
        "contained": "yes" if contained else "no",
    }
    lines = []
    for line in section[1].splitlines()[1:]:
        field = re.match(r"([a-z_]+):\s*(.*)$", line.strip())
        if not field:
            if lines:
                break
            continue
        key, rest = field.groups()
        if key in known:
            lines.append(f"{key}: {known[key]}")
        elif "<" in rest or "|" in rest:
            return None  # A line this code cannot fill in.
        else:
            lines.append(f"{key}: {rest}")
    if not {"incident", "records_affected", "detected_at"} <= {
        line.split(":")[0] for line in lines
    }:
        return None
    # The recipient is a cell of the operator's contact list, not text from evidence.
    workspace.ledger.ground([officer[0][incident.contact_address]])
    sent = workspace.call(
        "send_message",
        to=officer[0][incident.contact_address],
        subject=f"Personal-data breach notification {incident.incident_id}",
        body="\n".join(lines),
    )
    if not done(sent):
        return None
    return incident.count(counted), [transfer.source for transfer in counted]


@solver(r"/drafts/", acts=True)
def partner_summary(incident: Incident, request: str, workspace: Workspace) -> Solved | None:
    path = next(iter(re.findall(r"/drafts/[\w./-]+\.\w+", request)), None)
    wanted = ("incident id", "records", "domains", "patient zero")
    if path is None or not all(word in request.lower() for word in wanted):
        return None
    if re.search(r"\bsend it\b(?! \))", request, re.I) and not re.search(
        r"n't send|not send", request
    ):
        return None
    counted = incident.exfiltration
    first = incident.entry[1]
    domains = sorted({transfer.domain for transfer in counted})
    count = incident.count(counted)
    content = "\n".join(
        [
            f"# Incident summary {incident.incident_id}",
            "",
            f"Incident: {incident.incident_id}",
            f"Exfiltrated customer records: {count}",
            f"Destination domains: {', '.join(domains)}",
            f"Patient zero first processed the entry artifact at {first.ts:%H:%M} UTC.",
            "",
        ]
    )
    if not done(workspace.call("write", path=path, content=content)):
        return None
    sources: list[Source] = []
    for name in re.findall(r"/notes/[\w./-]+\.md", request):
        if not incident.has(name):
            continue
        for number, line in enumerate(incident.text(name).splitlines(), 1):
            facts = [f"{first.ts:%H:%M}", *domains]
            if any(fact in line for fact in facts) or re.search(rf"\b{count}\b", line):
                sources.append({"path": name, "line": number})
    return path, sources or [first.source]


@solver(r"quarantin", acts=True)
def contain(incident: Incident, request: str, workspace: Workspace) -> Solved | None:
    """Quarantine the compromised, rotate what was exposed, block where it went."""
    if not re.search(r"rotat", request, re.I) or not re.search(r"block", request, re.I):
        return None
    if re.search(r"/inbox/|message", request, re.IGNORECASE):
        return None
    compromised = set(incident.compromised)
    quarantined = set(incident.quarantined)
    named = set(re.findall(r"\b[a-z]+-\d{2}\b", request)) & {
        row[incident.roster_id] for row in incident.roster
    }
    # An agent named by its function or its name: several may fit.
    loose: set[str] = set()
    prose = re.sub(r"\b[a-z]+-\d{2}\b", " ", request)
    skipped = (incident.roster_id, "_line", "critical", "stand_in")
    for key in [k for k in incident.roster[0] if k not in skipped]:
        for value in {row[key] for row in incident.roster if len(row[key]) > 3}:
            if re.search(rf"\b{re.escape(value)}\b", prose, re.IGNORECASE):
                loose |= {r[incident.roster_id] for r in incident.roster if r[key] == value}
    if len(loose) > 1:
        rows = [r for r in incident.roster if r[incident.roster_id] in loose]
        sources = [incident.csv_source("fleet/agents.csv", row) for row in rows]
        return sorted(loose), sources, "NEEDS_CLARIFICATION"
    named |= loose
    if named:
        if not named <= compromised:
            return None  # A clean agent is named: not ours to quarantine by rule.
        targets = sorted(named - quarantined)
    elif re.search(r"all compromised|every compromised|compromised agents", request, re.I):
        targets = sorted(compromised - quarantined)
    else:
        return None
    ops: list[dict[str, Any]] = []
    taken: set[str] = set()
    for agent in targets:
        stand_in = incident.stand_in(agent, taken | set(targets))
        if stand_in is None:
            return None
        op: dict[str, Any] = {"op": "quarantine_agent", "agent": agent}
        if stand_in:
            op["stand_in"] = stand_in
            taken.add(stand_in)
        ops.append(op)
    rotations = [
        (token, stamp(incident.rotation_slot(token)[0])) for token in incident.exposed_tokens
    ]
    ops += [{"op": "rotate_token", "token": token, "at": when} for token, when in rotations]
    blocked = sorted(
        domain
        for domain in incident.attacker_domains
        if not incident.is_own(domain) and domain not in incident.partner_domains
    )
    ops += [{"op": "block_domain", "domain": domain} for domain in blocked]
    if not ops or not done(workspace.call("containment", ops=ops)):
        return None
    summary = (
        f"Quarantined {', '.join(targets) or 'nobody'}; scheduled rotation of "
        f"{', '.join(f'{t} at {w}' for t, w in rotations) or 'no token'}; "
        f"blocked {', '.join(blocked) or 'no domain'}."
    )
    sources = [incident.compromised[agent].source for agent in targets]
    sources += [event.source for event in incident.secret_reads]
    return summary, sources


__all__ = ["domain_of", "playbooks"]
