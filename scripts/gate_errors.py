#!/usr/bin/env python3
"""The gate's failure taxonomy — so a gate failure names its own cause.

Three unrelated failures used to look identical at the gate output: a timeout kill, an HTTP 402
out-of-credit reply, and an unreachable provider all surfaced as one `[gate_runner] error: …` line
and exit 2. The 402 was found only by probing the provider by hand, and a day was spent reading
infrastructure failures as model failures.

Every failure now carries a `cause` from CAUSES and prints the provider's own words VERBATIM. The
verbatim part is load-bearing: the classifier below is a heuristic over vendor error strings and
will not recognise every one of them: `provider-error` is the honest answer when it cannot tell, and
the raw text is what lets a human do in ten seconds what the classifier could not.

Nothing here decides pass or fail. Every cause is a hard stop; the taxonomy only says *which* stop.
"""

from __future__ import annotations

#: Every distinct way a gate can fail, with what each one means for the operator.
CAUSES = {
    "config": "the gate was invoked wrong or its configuration is incomplete",
    "cross-family": "the gate model shares the author's vendor family — no independence",
    "unknown-vendor": "the gate model's vendor is unknown, so family cannot be established",
    "runner-untrusted": "the gate runner did not identify itself as the shipped runner",
    "timeout": "the provider call exceeded its budget and was killed",
    "provider-not-found": "the provider CLI is not on PATH",
    "provider-unreachable": "the provider could not be reached (network, DNS, outage)",
    "provider-payment-required": "the provider refused for billing reasons (402, credit, quota)",
    "provider-locked": "concurrent gate calls contended on the provider CLI's LOCAL state lock — nothing remote failed; run gate calls serially, or give each concurrent call its own state directory",
    "provider-error": "the provider returned an error this classifier could not categorise",
    "no-verdict": "the provider replied, but the reply contained no JSON verdict",
    "implausible-latency": "a verdict came back faster than the call itself can be made, so it is presumed not to have run (scripts/gate_plausibility.py)",
    "io": "a file the gate needs could not be read or written",
    "internal": "the gate itself failed in a way it does not recognise — a defect in the gate",
}

#: How a provider's text actually names an HTTP status. A status code is only a status code when
#: something says so: a BARE `402` matched any digit run containing it — a token count, a request
#: id, a latency, an exit code — and since payment is classified first, an unrelated provider error
#: sent the operator to their billing page. The real 402 arrives by status code on the HTTPError
#: path (gate_runner.call_openrouter), so these forms only have to serve provider CLI text.
_STATUS_PREFIXES = ("http ", "http/1.1 ", "http/2 ", "status ", "status code ", "code ", "error ")


def _status_markers(*codes: int) -> tuple[str, ...]:
    """Every spelling of `<code>` that carries its own evidence of being an HTTP status."""
    return tuple(f"{prefix}{code}" for code in codes for prefix in _STATUS_PREFIXES)


#: Billing refusals. A 402 is the canonical one; vendors also phrase it as credit or quota.
#: `quota` sits here rather than under unreachable because every vendor that uses the word means
#: "you have run out of something you pay for", which is an operator action, not a retry.
_PAYMENT_MARKERS = _status_markers(402) + (
    "payment required",
    "insufficient credit",
    "insufficient_quota",
    "insufficient funds",
    "insufficient balance",
    "out of credit",
    "no credit",
    "credit balance",
    "billing",
    "quota exceeded",
    "exceeded your current quota",
)

#: LOCAL state-lock contention, checked FIRST (issue #50). Three parallel gate calls through
#: opencode produced ZERO verdicts in 900s while the same three serially produced all three in 68s,
#: and the mechanism was a SQLite lock on the operator's own disk. Reported as a provider failure it
#: sends the reader to a status page for a file lock, and it was diagnosed wrongly the first time on
#: both occasions it appeared: once as a provider-wide outage (on which "gate serially" was adopted,
#: the right rule for the wrong reason), once as provider drain. It is first because a lock message
#: legitimately arrives alongside the CLI's own "service unavailable" noise, and the lock is the
#: actionable half. Deliberately tight: an unrecognised error stays `provider-error`, which is the
#: honest default, rather than being guessed into a remedy that would not help.
_LOCK_MARKERS = (
    "database is locked",
    "database table is locked",
    "sqlite_busy",
    "sqlite3_busy",
)

#: Reachability failures. Checked AFTER payment, because a 402 body can mention a URL that also
#: matches nothing here, and because "billing" is actionable while "retry later" is not. The gateway
#: codes carry their own status evidence for the same reason 402 does — a bare `503` in a token
#: count would name an outage that never happened.
_UNREACHABLE_MARKERS = _status_markers(502, 503, 504) + (
    "connection refused",
    "connection reset",
    "could not resolve",
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname",
    "network is unreachable",
    "no route to host",
    "dns",
    "getaddrinfo",
    "ssl",
    "certificate verify failed",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "overloaded",
)


class GateError(RuntimeError):
    """A gate failure that knows what kind of failure it is.

    `detail` is the one-line summary; `provider_output` is the provider's own text, reproduced
    without truncation because the classifier is a heuristic and the raw text is the appeal.
    """

    def __init__(self, cause: str, detail: str, provider_output: str = "") -> None:
        super().__init__(detail)
        if cause not in CAUSES:
            # A cause outside the taxonomy is itself a defect of the kind this module exists to
            # prevent, so it is loud rather than tolerated.
            raise ValueError(f"unknown gate failure cause {cause!r}; add it to CAUSES")
        self.cause = cause
        self.detail = detail
        self.provider_output = provider_output

    def render(self) -> str:
        """The operator-facing block: cause, meaning, summary, then the provider verbatim."""
        lines = [
            f"[gate_runner] FAIL cause={self.cause}: {self.detail}",
            f"  meaning: {CAUSES[self.cause]}",
        ]
        if self.provider_output.strip():
            lines.append("  --- provider output (verbatim) ---")
            lines.extend(f"  {line}" for line in self.provider_output.rstrip().splitlines())
            lines.append("  --- end provider output ---")
        return "\n".join(lines)


def classify_provider_failure(text: str) -> str:
    """Which `provider-*` cause the provider's own error text points at.

    Order matters: a LOCAL state lock is not a provider failure at all and is named before anything
    else, and a billing refusal is an operator action that must not be reported as a transient
    outage. Anything unrecognised is `provider-error` — deliberately not guessed into a category
    that would tell the operator to do the wrong thing.
    """
    lowered = (text or "").lower()
    if any(marker in lowered for marker in _LOCK_MARKERS):
        return "provider-locked"
    if any(marker in lowered for marker in _PAYMENT_MARKERS):
        return "provider-payment-required"
    if any(marker in lowered for marker in _UNREACHABLE_MARKERS):
        return "provider-unreachable"
    return "provider-error"
