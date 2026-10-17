# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""What a model may see (NFR-13): the `redact` patterns over every record the agent reads, and a
scrubber for secrets in every text it sends.

Redaction happens where the data comes in: `RedactingQuery` wraps the agent's query port, so the
windows, triage, the prompt and every tool see the same redacted events, faults, executions and
incidents. The scrubber is the last line, on the messages themselves right before each model call
(and on what the agent writes about a call: traces, progress, errors): keys, tokens, passwords and
private keys in any text, e-mail addresses unless told otherwise, and the values of the agent's own
secrets ($*_API_KEY, $*_TOKEN ...).

Patterns (agent.yaml `redact`), matched without regard to case:
- `field`: a field of any record, by glob: `message`, `payload`, `requester_id` ...
- `field.key`: keys of a mapping field, by glob after the first dot: `attrs.gps.*`, `source.host`.
  `attrs.` patterns also cover fault freeze frames, which copy the reporting event's attributes.
- `@identities`: who did what (operators, approvers, requesters) wherever Jarvis records it.
A redacted value reads "[redacted]"; a redacted mapping keeps its keys. What an analysis runs on
stays whatever the patterns say: the record's structure (ids, seq, kind, code, severity, component,
stamps, states, outcomes) and the attributes Jarvis itself writes (fault_id, state, phase, plan ...),
so `attrs.*` hides every attribute of the robot's and none of Jarvis'. Naming one of those outright
is refused, as a pattern that could only be a mistake.
"""

import copy
import fnmatch
import os
import re

REDACTED = "[redacted]"
STRUCTURAL = frozenset(
    {
        "id", "seq", "kind", "code", "severity", "component", "stamp", "recv_stamp", "ts", "correlation_id",
        "causation_id", "fault_id", "execution_id", "incident_id", "state", "prev_state", "outcome",
        "plan", "plan_id", "rule", "rule_id", "trigger", "first_seen", "last_seen",
    }
)  # fmt: skip
STRUCTURAL_ATTRS = frozenset(
    {
        "fault_id", "root_fault_id", "incident_id", "execution_id", "state", "prev_state", "phase",
        "plan", "dry_run", "occurrences", "muted_by", "requester",
    }
)  # fmt: skip
KEEP = {"attrs": STRUCTURAL_ATTRS, "freeze_frame": STRUCTURAL_ATTRS, "source": frozenset({"component"})}
MACROS = {
    "@identities": (
        "requester_id", "acknowledged_by", "approved_by", "decided_by", "operator",
        "attrs.user", "attrs.by", "attrs.requester_id", "attrs.approved_by", "attrs.operator",
    ),
}  # fmt: skip


def check_patterns(patterns):
    """The patterns, validated: a list of non-empty strings, macros known, structure not named."""
    if isinstance(patterns, str) or not isinstance(patterns, list | tuple):
        raise ValueError(f"redact: a list of patterns, not {type(patterns).__name__} ({patterns!r})")
    for p in patterns:
        if not isinstance(p, str) or not p.strip():
            raise ValueError(f"redact: {p!r} is not a pattern")
        if p.startswith("@"):
            if p not in MACROS:
                raise ValueError(f"redact: unknown {p} (known: {', '.join(MACROS)})")
            continue
        field, _, key = p.lower().partition(".")
        if (not key and field in STRUCTURAL) or key in KEEP.get(field, ()):
            raise ValueError(f"redact: {p} is what an analysis runs on, and is never redacted")
    return list(patterns)


class Redactor:
    def __init__(self, patterns=()):
        expanded = [x for p in check_patterns(patterns) for x in MACROS.get(p, (p,))]
        self.patterns = tuple(patterns)
        self.fields = [p.lower() for p in expanded if "." not in p]
        self.keys = []  # (mapping field, key glob)
        for p in expanded:
            if "." in p:
                field, key = p.lower().split(".", 1)
                self.keys.append((field, key))
                if field == "attrs":
                    self.keys.append(("freeze_frame", key))

    def __bool__(self):
        return bool(self.fields or self.keys)

    def records(self, records):
        return [self.record(r) for r in records] if self else list(records)

    def record(self, r):
        """A redacted copy of one record, and of the records in its lists (timeline, faults ...)."""
        if not self or not isinstance(r, dict):
            return r
        r = copy.deepcopy(r)
        self._redact(r)
        return r

    def _redact(self, r):
        for name in list(r):
            value, low = r[name], name.lower()
            keep = KEEP.get(low, ())
            if low not in STRUCTURAL and any(fnmatch.fnmatchcase(low, f) for f in self.fields):
                r[name] = _hidden(value, keep)
                continue
            if isinstance(value, dict):
                globs = [k for f, k in self.keys if fnmatch.fnmatchcase(low, f)]
                for key in list(value):
                    k = str(key).lower()
                    if k not in keep and any(fnmatch.fnmatchcase(k, g) for g in globs):
                        value[key] = _hidden(value[key])
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        self._redact(item)


def _hidden(value, keep=()):
    if isinstance(value, dict):
        return {k: v if str(k).lower() in keep else REDACTED for k, v in value.items()}
    if isinstance(value, list):
        return []
    return REDACTED


class RedactingQuery:
    """The agent's query port, redacted: everything read through it is what the model may see."""

    def __init__(self, query, redactor):
        self.inner = query
        self.redactor = redactor

    def __getattr__(self, name):  # plans, seq_before ... carry no robot data
        return getattr(self.inner, name)

    def incident(self, incident_id):
        return self.redactor.record(self.inner.incident(incident_id))

    def events(self, *args, **kwargs):
        return self.redactor.records(self.inner.events(*args, **kwargs))

    def event(self, event_id):
        return self.redactor.record(self.inner.event(event_id))

    def active_faults(self):
        return self.redactor.records(self.inner.active_faults())

    def fault_history(self, code, *args, **kwargs):
        return self.redactor.records(self.inner.fault_history(code, *args, **kwargs))

    def recoveries(self, *args, **kwargs):
        return self.redactor.records(self.inner.recoveries(*args, **kwargs))

    def rule(self, rule_id):
        return self.redactor.record(self.inner.rule(rule_id))


def redacting(query, patterns):
    """The query port behind the patterns (once: wrapping twice changes nothing)."""
    redactor = Redactor(patterns)
    if not redactor or (isinstance(query, RedactingQuery) and query.redactor.patterns == redactor.patterns):
        return query
    return RedactingQuery(query, redactor)


# -- the last line ---------------------------------------------------------------------------------

SECRETS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"\bsk-(?:ant-|proj-|or-)?[A-Za-z0-9_-]{16,}"),  # OpenAI, Anthropic, OpenRouter
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),  # AWS access key ids
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),  # GitHub
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # Slack
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),  # Google
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),  # JWT
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"),
    # password=..., "api_key": "...": the name and its separator stay, the value goes
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)([\"']?\s*[:=]\s*[\"']?)[^\s\"',;&}]+"),
]
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SECRET_ENV = re.compile(r"(_API_KEY|_TOKEN|_SECRET|_PASSWORD|_KEY)$")


class Scrubber:
    def __init__(self, emails=True, environ=None):
        self.emails = emails
        environ = os.environ if environ is None else environ
        # Longest first, so a secret that contains another is hidden whole.
        self.literals = sorted(
            {v for k, v in environ.items() if SECRET_ENV.search(k) and len(v) >= 8}, key=len, reverse=True
        )

    def text(self, text):
        if not isinstance(text, str) or not text:
            return text
        for literal in self.literals:
            text = text.replace(literal, "[secret]")
        for pattern in SECRETS:
            text = pattern.sub(_secret, text)
        if self.emails:
            text = EMAIL.sub("[email]", text)
        return text

    def messages(self, messages):
        """Copies of chat messages with every text scrubbed: contents, and the tool calls' arguments."""
        out = []
        for m in messages:
            m = dict(m)
            if isinstance(m.get("content"), str):
                m["content"] = self.text(m["content"])
            elif isinstance(m.get("content"), list):
                m["content"] = [
                    {**part, "text": self.text(part["text"])}
                    if isinstance(part, dict) and "text" in part
                    else part
                    for part in m["content"]
                ]
            if m.get("tool_calls"):
                m["tool_calls"] = [
                    {
                        **t,
                        "function": {**t["function"], "arguments": self.text(t["function"].get("arguments"))},
                    }
                    for t in m["tool_calls"]
                ]
            out.append(m)
        return out


def _secret(match):
    # Keep the name of a `password=...` and its separator: what it was stays readable.
    if match.lastindex == 2:
        return f"{match.group(1)}{match.group(2)}[secret]"
    if match.lastindex == 1:
        return f"{match.group(1)} [secret]"
    return "[secret]"
