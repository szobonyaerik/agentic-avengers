"""Three unrelated failures used to be one indistinguishable line.

A timeout kill, an HTTP 402 out-of-credit reply and an unreachable provider all surfaced as
`[gate_runner] error: …` and exit 2. The 402 was found only by probing the provider by hand, after
the run had already been read as the gate model rejecting the work. A gate that fails without
naming its failure sends the reader to the wrong place, and it did, for about a day.

These pin the classifier and the rendered block. The classifier is a heuristic over vendor error
strings and is allowed to answer `provider-error` when it cannot tell — what it is NOT allowed to do
is answer confidently and wrongly, which is why the payment markers are checked before the
reachability ones.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gate_errors import CAUSES, GateError, classify_provider_failure  # noqa: E402


# ── the three that used to look alike ────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "HTTP 402 Payment Required",
        "Error: insufficient credit for this request",
        'openrouter: {"error":{"code":402,"message":"Insufficient credits"}}',
        "You exceeded your current quota, please check your plan and billing details",
    ],
)
def test_a_billing_refusal_is_named_as_one(text: str) -> None:
    """This is the one that was mistaken for the model rejecting the spec."""
    assert classify_provider_failure(text) == "provider-payment-required"


@pytest.mark.parametrize(
    "text",
    [
        "curl: (7) Failed to connect to openrouter.ai port 443: Connection refused",
        "getaddrinfo ENOTFOUND openrouter.ai",
        "Temporary failure in name resolution",
        "503 Service Unavailable",
        "upstream overloaded, try again",
    ],
)
def test_an_unreachable_provider_is_named_as_one(text: str) -> None:
    assert classify_provider_failure(text) == "provider-unreachable"


@pytest.mark.parametrize(
    "text",
    [
        "opencode: request req-4021f7 failed after 14023ms",
        "usage: prompt_tokens=9402 completion_tokens=88",
        "model returned exit code 1 after 402193 bytes of output",
    ],
)
def test_a_digit_run_that_merely_contains_402_is_not_a_billing_refusal(text: str) -> None:
    """`402` as a bare substring matched token counts, request ids, latencies and byte counts — and
    payment is classified first, so an unrelated provider error sent the operator to their billing
    page to fix a problem their billing page does not have."""
    assert classify_provider_failure(text) == "provider-error"


@pytest.mark.parametrize(
    "text",
    [
        "opencode: finished in 15037ms with no output",
        "trace id 5041aa9c",
    ],
)
def test_a_digit_run_that_merely_contains_a_gateway_code_is_not_an_outage(text: str) -> None:
    """Same defect, one taxonomy entry over: a bare `503` named an outage that never happened."""
    assert classify_provider_failure(text) == "provider-error"


@pytest.mark.parametrize(
    "text",
    [
        "upstream returned HTTP 402",
        "openrouter status 402",
        "error 402 from the credits endpoint",
    ],
)
def test_a_status_code_that_says_it_is_one_is_still_read_as_billing(text: str) -> None:
    """Tightening must not cost the CLI-text path its detection — the HTTP path catches a real 402
    by status code, but a provider CLI only ever hands us prose."""
    assert classify_provider_failure(text) == "provider-payment-required"


def test_a_status_code_that_says_it_is_one_is_still_read_as_unreachable() -> None:
    assert classify_provider_failure("upstream returned HTTP 503") == "provider-unreachable"


def test_an_unrecognised_error_says_so_rather_than_guessing() -> None:
    """A wrong confident category sends the operator somewhere useless; `provider-error` sends them
    to the verbatim text, which is where the answer actually is."""
    assert classify_provider_failure("model refused to answer: internal disagreement") == (
        "provider-error"
    )


def test_billing_wins_over_reachability_when_both_could_match() -> None:
    """A 402 body that also mentions a gateway is still an operator action, not a retry."""
    assert classify_provider_failure(
        "402 Payment Required (via gateway 503 fallback)"
    ) == "provider-payment-required"


# ── the rendered failure ─────────────────────────────────────────────────────


def test_the_rendered_failure_names_its_cause_and_what_the_cause_means() -> None:
    rendered = GateError("timeout", "opencode blew its budget", "child killed").render()
    assert "cause=timeout" in rendered
    assert CAUSES["timeout"] in rendered


def test_the_provider_is_reproduced_verbatim_and_untruncated() -> None:
    """The old code cut the provider's stderr at 400 chars — long enough to lose the part that
    said 402, which is exactly what happened."""
    body = "x" * 4000 + "\n402 Payment Required"
    rendered = GateError("provider-payment-required", "refused", body).render()
    assert "402 Payment Required" in rendered
    assert rendered.count("x") == 4000


def test_a_cause_outside_the_taxonomy_is_refused_loudly() -> None:
    """An unnamed cause is the defect this module exists to remove; it must not be creatable."""
    with pytest.raises(ValueError, match="unknown gate failure cause"):
        GateError("something-went-wrong", "…")


def test_every_cause_carries_an_operator_facing_meaning() -> None:
    assert all(meaning.strip() for meaning in CAUSES.values())
