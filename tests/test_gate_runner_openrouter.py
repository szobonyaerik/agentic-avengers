"""A failure the gate did not anticipate must still name itself honestly.

`tests/test_gate_runner_failures.py` covers the failures the runner raises deliberately. These cover
the two that reached the catch-all instead, and were therefore reported as `cause=config` — "the gate
was invoked wrong or its configuration is incomplete". Both sent the operator to their `.env` for a
problem that was not there, which is the one shape the taxonomy exists to prevent:

  * a provider that answers HTTP 200 with a body that is not JSON (a proxy interstitial, an HTML
    error page, a captive portal). `json.loads` sat inside a `try` that caught only HTTPError,
    URLError and TimeoutError, so its JSONDecodeError propagated out of the whole call;
  * anything genuinely unforeseen, which is a defect in the gate and now says so.

No network and no model: `urlopen` is replaced.
"""

import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import gate_runner  # noqa: E402
from gate_errors import GateError  # noqa: E402


class _Response(io.BytesIO):
    """The bytes a 200 handed back, in the shape `urlopen`'s context manager returns."""

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


@pytest.fixture
def answered(monkeypatch: pytest.MonkeyPatch):
    """Make the next openrouter call return `body` verbatim, with a 200."""

    def respond(body: bytes) -> None:
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
        monkeypatch.setattr(
            gate_runner.urllib.request, "urlopen", lambda *a, **k: _Response(body)
        )

    return respond


PROXY_PAGE = b"<html><head><title>502 Bad Gateway</title></head><body>nginx</body></html>"


def test_a_200_that_is_not_json_is_the_provider_s_failure_not_the_operator_s(answered) -> None:
    """Reported as `cause=config` before this, which is a lie about whose problem it is."""
    answered(b"not json at all")
    with pytest.raises(GateError) as raised:
        gate_runner.call_openrouter("deepseek/deepseek-chat", "rubric", "artifact")
    assert raised.value.cause.startswith("provider-")
    assert raised.value.cause != "config"


def test_the_body_that_could_not_be_parsed_is_reproduced_verbatim(answered) -> None:
    """The classifier is a heuristic; the raw text is the appeal, and it is the only thing that
    tells the operator this was a proxy rather than a model."""
    answered(PROXY_PAGE)
    with pytest.raises(GateError) as raised:
        gate_runner.call_openrouter("deepseek/deepseek-chat", "rubric", "artifact")
    assert PROXY_PAGE.decode() in raised.value.provider_output
    assert PROXY_PAGE.decode() in raised.value.render()


def test_an_unparseable_body_that_names_a_gateway_is_classified_as_one(answered) -> None:
    answered(PROXY_PAGE)
    with pytest.raises(GateError) as raised:
        gate_runner.call_openrouter("deepseek/deepseek-chat", "rubric", "artifact")
    assert raised.value.cause == "provider-unreachable"


def test_undecodable_bytes_cannot_take_the_call_out_through_the_catch_all(answered) -> None:
    """A body that is not even valid UTF-8 used to raise UnicodeDecodeError from the same line."""
    answered(b"\xff\xfe not utf-8 and not json")
    with pytest.raises(GateError):
        gate_runner.call_openrouter("deepseek/deepseek-chat", "rubric", "artifact")


def test_a_well_formed_reply_is_unaffected(answered) -> None:
    answered(json.dumps({"choices": [{"message": {"content": '{"verdict":"GO"}'}}]}).encode())
    assert gate_runner.call_openrouter("deepseek/deepseek-chat", "rubric", "artifact") == (
        '{"verdict":"GO"}'
    )


def test_the_catch_all_calls_an_unforeseen_failure_what_it_is(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """`config` told the operator to go and check their configuration for a bug in the gate. The
    backstop stays — a gate must never fail open — but it now says the failure was unrecognised."""

    def explode(*args: object, **kwargs: object) -> None:
        raise ZeroDivisionError("a defect nothing above anticipated")

    monkeypatch.setattr(gate_runner, "assert_cross_family", explode)
    monkeypatch.setattr(sys, "argv", ["gate_runner.py", "--selftest"])

    with pytest.raises(SystemExit) as exit_code:
        gate_runner.main()

    stderr = capsys.readouterr().err
    assert exit_code.value.code == 2, "the backstop must still fail closed"
    assert "cause=internal" in stderr
    assert "cause=config" not in stderr
    assert "ZeroDivisionError" in stderr
