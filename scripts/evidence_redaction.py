#!/usr/bin/env python3
"""What a recorded command's output is allowed to leave behind on disk - redacted, then capped.

## The defect

`verifier_evidence.record` wrote the child's combined stdout+stderr verbatim to
`<phase-dir>/evidence/NN-<kind>.log`, uncapped, and that path is deliberately committed (the gates
read it in CI). The kind most affected is `adversarial`, whose whole job is to *plant a value a real
deployment would produce and look at what came back* - which is how two plaintext-credential leaks
were found. A run that reproduces a leak therefore captured the credential, plus whatever the real
collaborator echoed, into git history, where **no later commit removes it**. Producing a secret is
this stage's EXPECTED case, not an edge case.

## The rule

**Nothing reaches the log file until it has been through here.** `prepare()` redacts, then caps, and
returns the bytes that are actually stored; `verifier_evidence` hashes *those*, so
`verifier_evidence.py check` still verifies the log on disk against the record. Proof of execution is
not traded away for tidiness.

1. **Redact, then cap - in that order.** Capping first would leave a half-matched secret in the kept
   region, where its pattern no longer fires and the surviving half still leaks.
2. **Every removal is VISIBLE.** A redacted span becomes `[REDACTED:<pattern>:<n> bytes]` and a
   truncation becomes an explicit marker naming how much was dropped. A log that reads as complete
   when it was cut is worse than no log, because a later reader draws a conclusion from an absence
   that was really a cut.
3. **FAIL CLOSED.** Any error here raises `RedactionError`, and the caller writes NO log and records
   NO run. There is deliberately no raw-log fallback: a fail-open writes a live credential into git
   permanently.

## The limits, stated plainly

**Redaction by pattern is a reduction of risk, not a guarantee.** A secret with no recognisable
shape - a bare word, a value the collaborator prints under a key this set does not know, a token
format invented after this list was written - passes straight through, and so does one split across
lines by the program that printed it. Nothing here should be read as "secrets cannot reach the log".
The honest claim is narrower: the shapes below are removed, the log is bounded, and both facts are
visible in the file.

**Extending it** is a new entry in `PATTERNS` - a `(name, regex, group)` triple, where `group` is the
span to replace (`0` = the whole match). For a one-off at a call site, `EVIDENCE_REDACT_EXTRA` holds
one additional regex whose whole match is redacted; an invalid one is a named failure, never a
silent skip.

## What bounds the phase directory

`EVIDENCE_LOG_MAX_BYTES` (default 256 KiB, floor `MIN_MAX_BYTES`) caps **each stored log**, and the
stored bytes never exceed it - a ceiling too small to hold the marker that would say it bit is
refused by name rather than quietly overshot. Nothing is ever pruned: every
entry is in the record's hash chain and its log is what `check` hashes, so deleting one - even a
superseded one - would turn a verifiable transcript into a missing-log failure with no remedy. The
growth rule is therefore stated rather than enforced by deletion: **one capped log per recorded
command**, and the number of commands is bounded by the 3-attempt verification cap
(`verifier_attempts.py`) times the commands one attempt runs.
"""

from __future__ import annotations

import os
import re

#: Bytes of one command's output that are stored. The head and the tail are kept and the middle is
#: dropped: a suite log's failure summary is at the end, and a head-only cut would throw away the
#: part a reader opens the log for.
DEFAULT_MAX_BYTES = 256 * 1024
MAX_BYTES_ENV = "EVIDENCE_LOG_MAX_BYTES"

#: The smallest ceiling that can MEAN anything. The truncation marker is ~120-160 bytes, so below
#: this a "cap" would be satisfied by the marker alone and the stored log would come out LARGER than
#: the number that was set - a config value that silently does not bind, which is the same class of
#: defect as the rest of this module. Refused by name instead, like a malformed value.
MIN_MAX_BYTES = 256

#: One extra regex, for a call site with a secret shape this set does not know. Whole match redacted.
EXTRA_ENV = "EVIDENCE_REDACT_EXTRA"

#: `(name, pattern, group)` - `group` is the span replaced by the marker, `0` being the whole match.
#: Ordered: the specific vendor shapes run before the generic key=value rule, so a matched token is
#: named by what it is rather than by the assignment that carried it.
PATTERNS: tuple[tuple[str, str, int], ...] = (
    ("private-key-block",
     r"-----BEGIN(?: [A-Z]+)* PRIVATE KEY-----.*?-----END(?: [A-Z]+)* PRIVATE KEY-----", 0),
    ("url-credentials", r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s/:@]+:([^\s/@]+)@", 1),
    ("jwt", r"\beyJ[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}\.[A-Za-z0-9_\-]{5,}", 0),
    ("aws-access-key-id", r"\b(?:AKIA|ASIA|ABIA|ACCA)[0-9A-Z]{16}\b", 0),
    ("github-token", r"\bgh[pousr]_[A-Za-z0-9]{20,}\b", 0),
    ("slack-token", r"\bxox[abposr]-[A-Za-z0-9\-]{10,}\b", 0),
    ("stripe-key", r"\b[sr]k_(?:live|test)_[A-Za-z0-9]{16,}\b", 0),
    ("google-api-key", r"\bAIza[0-9A-Za-z_\-]{35}\b", 0),
    ("openai-key", r"\bsk-[A-Za-z0-9_\-]{20,}\b", 0),
    ("authorization-header",
     r"(?i)\bauthorization\b\s*[:=]\s*(?:bearer|basic|token|digest)?\s*(\S+)", 1),
    ("bearer-token", r"(?i)\bbearer\s+([A-Za-z0-9._~+/=\-]{8,})", 1),
    # The generic rule, last: any key whose NAME says it holds a secret, and the value beside it.
    ("secret-assignment",
     r"(?i)\b[A-Za-z0-9_.\-]*"
     r"(?:passwd|password|secret|token|api[_\-]?key|apikey|access[_\-]?key|private[_\-]?key"
     r"|credential|passphrase)"
     r"[A-Za-z0-9_.\-]*\s*[:=]\s*[\"']?([^\s\"',;]{4,})", 1),
)

#: The shape a dropped span leaves behind. Both markers name the rule that removed the bytes, so a
#: reader can tell a redaction from a truncation and either from output the command never produced.
REDACTED_MARKER = "[REDACTED:{name}:{n} bytes]"
TRUNCATED_MARKER = (
    "\n[TRUNCATED: {n} bytes dropped from the middle of {total} by verifier_evidence "
    "(cap {limit} bytes, {env})]\n"
)


class RedactionError(Exception):
    """Redaction could not run. The caller writes no log and records no run - never the raw bytes."""


def max_bytes() -> int:
    """The per-log cap. A malformed value is a named config failure, never a silent default."""
    raw = (os.environ.get(MAX_BYTES_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        value = int(raw)
    except ValueError as exc:
        raise RedactionError(f"{MAX_BYTES_ENV}={raw!r} is not an integer number of bytes") from exc
    if value <= 0:
        raise RedactionError(f"{MAX_BYTES_ENV}={raw!r} must be a positive number of bytes")
    return _checked_ceiling(value, MAX_BYTES_ENV)


def _checked_ceiling(value: int, source: str) -> int:
    """A ceiling that is big enough to hold the marker that says it bit. Refused by name if not."""
    if value < MIN_MAX_BYTES:
        raise RedactionError(
            f"{source}={value} is below the {MIN_MAX_BYTES}-byte floor: the truncation marker alone "
            f"is longer than that, so the stored log would exceed the very ceiling being set. Set "
            f"it to {MIN_MAX_BYTES} or more."
        )
    return value


def _compiled() -> list[tuple[str, re.Pattern[str], int]]:
    """The pattern set, compiled. A pattern that will not compile names itself and stops the run."""
    out: list[tuple[str, re.Pattern[str], int]] = []
    for name, pattern, group in PATTERNS:
        try:
            out.append((name, re.compile(pattern, re.DOTALL), group))
        except re.error as exc:
            raise RedactionError(f"built-in pattern {name!r} does not compile ({exc})") from exc
    extra = os.environ.get(EXTRA_ENV)
    if extra:
        try:
            out.append((EXTRA_ENV.lower(), re.compile(extra, re.DOTALL), 0))
        except re.error as exc:
            raise RedactionError(f"{EXTRA_ENV}={extra!r} does not compile ({exc})") from exc
    return out


def _replace(match: re.Match[str], name: str, group: int) -> str:
    whole = match.group(0)
    if group == 0:
        return REDACTED_MARKER.format(name=name, n=len(whole.encode("utf-8")))
    try:
        secret = match.group(group)
    except (IndexError, re.error) as exc:
        raise RedactionError(f"pattern {name!r} has no group {group}") from exc
    if secret is None:
        return whole
    start, end = match.span(group)
    offset = match.start()
    marker = REDACTED_MARKER.format(name=name, n=len(secret.encode("utf-8")))
    return whole[: start - offset] + marker + whole[end - offset :]


def redact(text: str) -> str:
    """Every known secret shape replaced by a marker naming the pattern and the bytes removed."""
    if not isinstance(text, str):
        raise RedactionError(f"expected text to redact, got {type(text).__name__}")
    result = text
    for name, pattern, group in _compiled():
        try:
            result = pattern.sub(lambda m, n=name, g=group: _replace(m, n, g), result)
        except RedactionError:
            raise
        except Exception as exc:  # noqa: BLE001 - a redactor that failed must never fall through
            raise RedactionError(f"pattern {name!r} failed while redacting ({exc!r})") from exc
    return result


def cap(text: str, limit: int | None = None) -> str:
    """The stored log, bounded - head and tail kept, the middle dropped behind a visible marker."""
    ceiling = max_bytes() if limit is None else _checked_ceiling(limit, "limit")
    data = text.encode("utf-8")
    total = len(data)
    if total <= ceiling:
        return text
    reserve = len(TRUNCATED_MARKER.format(n=total, total=total, limit=ceiling,
                                          env=MAX_BYTES_ENV).encode("utf-8"))
    keep = ceiling - reserve
    if keep <= 0:
        raise RedactionError(
            f"a {ceiling}-byte ceiling cannot hold this log's {reserve}-byte truncation marker, so "
            f"the stored log would exceed it. Raise {MAX_BYTES_ENV} above {reserve}."
        )
    head, tail = keep // 2, keep - keep // 2
    dropped = total - head - tail
    marker = TRUNCATED_MARKER.format(n=dropped, total=total, limit=ceiling, env=MAX_BYTES_ENV)
    kept_head = data[:head].decode("utf-8", "ignore")
    kept_tail = data[total - tail:].decode("utf-8", "ignore") if tail else ""
    return kept_head + marker + kept_tail


def prepare(text: str) -> str:
    """What actually gets written: redacted first, then capped. The caller hashes THIS."""
    return cap(redact(text))
