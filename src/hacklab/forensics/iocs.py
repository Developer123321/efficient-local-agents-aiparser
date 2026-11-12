"""Indicators of compromise found by pattern, not by a model."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import re
from typing import Any

MAX_TEXT = 4_000_000

# Credential shapes seen in agent intrusions against a model hub: platform and
# source-control tokens, cloud keys, mesh-VPN keys, signed identity tokens.
SECRET_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key": re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
    ),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
    "hub_token": re.compile(r"\bhf_[A-Za-z0-9]{16,}\b"),
    "aws_access_key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "vpn_key": re.compile(r"\btskey-[a-z]+-[A-Za-z0-9-]{8,}\b"),
    "slack_token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "api_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
}
_PATTERNS: dict[str, re.Pattern[str]] = {
    "url": re.compile(r"\b(?:https?|ftp)://[^\s\"'<>\]\)]+", re.IGNORECASE),
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b"),
    "ipv4": re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])"),
    "sha256": re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])"),
    "sha1": re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{40}(?![0-9a-fA-F])"),
    "md5": re.compile(r"(?<![0-9a-fA-F])[0-9a-fA-F]{32}(?![0-9a-fA-F])"),
    "cve": re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE),
    "repo": re.compile(r"\b(?:datasets|models|spaces)/[\w.-]+/[\w.-]+"),
    "service_account": re.compile(r"\bsystem:serviceaccount:[\w.-]+:[\w.-]+"),
    "domain": re.compile(
        r"(?<![\w@/.-])(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
        r"(?:com|net|org|io|ai|co|dev|app|cloud|internal|local|eu|at|de|ru|cn|xyz|sh|me|info)\b",
        re.IGNORECASE,
    ),
}
_METADATA = ipaddress.ip_address("169.254.169.254")


def refang(text: str) -> str:
    """Undo the defanging analysts apply to indicators, such as hxxp:// and [.]."""
    text = re.sub(r"(?i)\bhxxp(s?)://", r"http\1://", text)
    return re.sub(r"\[\.\]|\(\.\)|\[dot\]", ".", text)


def classify_ip(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return "invalid"
    if address == _METADATA:
        return "cloud_metadata"
    for name in ("is_loopback", "is_link_local", "is_private", "is_multicast", "is_reserved"):
        if getattr(address, name):
            return name[3:]
    return "public"


def extract(text: str) -> dict[str, list[str]]:
    """Every indicator kind found in the text, each list sorted and without repeats."""
    text = refang(text[:MAX_TEXT])
    found: dict[str, set[str]] = {}
    for kind, pattern in SECRET_PATTERNS.items():
        if matches := set(pattern.findall(text)):
            found[kind] = matches
    secrets = "".join(value for values in found.values() for value in values)
    for kind, pattern in _PATTERNS.items():
        matches = {match.rstrip(".,;:") for match in pattern.findall(text)}
        if kind == "ipv4":
            matches = {match for match in matches if classify_ip(match) != "invalid"}
        elif kind in {"sha256", "sha1", "md5"}:
            # A digest-shaped run inside a longer token is not a digest.
            matches = {match for match in matches if match not in secrets}
        elif kind == "domain":
            urls = " ".join(found.get("url", ())) + " ".join(found.get("email", ()))
            matches = {match.lower() for match in matches if match.lower() not in urls.lower()}
        if matches:
            found[kind] = matches
    return {kind: sorted(values) for kind, values in found.items()}


def jwt_claims(token: str) -> dict[str, Any] | None:
    """Header and payload of a signed token, read without verifying the signature."""
    parts = token.strip().split(".")
    if len(parts) != 3:
        return None
    decoded = []
    for part in parts[:2]:
        try:
            raw = base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
            value = json.loads(raw)
        except (binascii.Error, ValueError):
            return None
        if not isinstance(value, dict):
            return None
        decoded.append(value)
    return {"header": decoded[0], "claims": decoded[1]}
