"""Tests for gate_runner's cross-family guard.

Two properties, each one a way the pipeline's only independence mechanism can fail open.

A routing prefix (openrouter/, opencode/, opencode-go/) must never hide a model's real family.
Before this was pinned, `opencode-go/claude-x` resolved to family 'opencode-go' and sailed past the
anthropic-vs-anthropic check.

And an unrecognised vendor must be refused, not guessed. The table knew seven vendors and returned
the raw model id for everything else, so `glm-5.1` and `glm-5.2` — one vendor, two models — read as
two DIFFERENT families. A phase was gated on glm-5.2 while that was live. False independence looks
exactly like real independence, which is what makes it worse than a missing check.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gate_runner import assert_cross_family, model_family  # noqa: E402
from gate_errors import GateError  # noqa: E402
from model_vendors import UnknownVendor  # noqa: E402


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("deepseek/deepseek-chat", "deepseek"),
        ("google/gemini-3.1-pro-preview", "google"),
        ("openrouter/anthropic/claude-opus", "anthropic"),
        ("anthropic/claude-sonnet-5", "anthropic"),
        ("opencode-go/deepseek-v4-pro", "deepseek"),
        ("opencode-go/grok-4.5", "grok"),
        ("opencode/anthropic/claude-x", "anthropic"),
        ("opencode-go/claude-x", "anthropic"),
        ("gemini-3.1-pro", "google"),
    ],
)
def test_model_family_sees_through_router_prefixes(model, family):
    assert model_family(model) == family


def test_routed_same_family_model_still_fails_closed():
    with pytest.raises(RuntimeError, match="cross-family violation"):
        assert_cross_family("opencode-go/claude-x", "anthropic")


def test_routed_cross_family_model_passes():
    assert_cross_family("opencode-go/deepseek-v4-pro", "anthropic")


# ── one vendor is one family ─────────────────────────────────────────────────


def test_two_models_from_one_vendor_are_one_family():
    """The live case: phase 8 gated on glm-5.2, and the table had no entry for GLM at all."""
    assert model_family("glm-5.1") == model_family("glm-5.2")


@pytest.mark.parametrize(
    ("model", "family"),
    [
        ("glm-5.2", "zhipu"),
        ("zai/glm-4.6", "zhipu"),
        ("chatglm-3", "zhipu"),
        ("kimi-k2", "moonshot"),
        ("moonshotai/kimi-k2", "moonshot"),
        ("minimax-m2", "minimax"),
        ("mimo-7b", "xiaomi"),
        ("hunyuan-large", "tencent"),
        ("hy-t1", "tencent"),
        ("meta-llama/llama-4-scout", "meta"),
        ("mistralai/mistral-large", "mistral"),
    ],
)
def test_the_vendors_that_used_to_fall_through_now_resolve(model, family):
    assert model_family(model) == family


def test_a_short_alias_cannot_swallow_an_unrelated_vendor():
    """`hy` is Tencent's; matching is boundary-respecting so it cannot claim `hyperion-9`."""
    with pytest.raises(UnknownVendor):
        model_family("hyperion-9")


# ── an unknown vendor refuses, loudly ────────────────────────────────────────


def test_an_unknown_vendor_is_refused_rather_than_guessed():
    with pytest.raises(UnknownVendor, match="unknown vendor"):
        model_family("brandnewthing-v1")


def test_the_refusal_says_how_to_resolve_it():
    """A loud refusal that does not say what to do is a wall, not a gate."""
    with pytest.raises(UnknownVendor) as exc:
        model_family("brandnewthing-v1")
    assert "VENDOR_FAMILIES" in str(exc.value)
    assert "GATE_MODEL_FAMILY" in str(exc.value)


def test_an_unknown_vendor_stops_the_gate_with_its_own_cause():
    with pytest.raises(GateError) as exc:
        assert_cross_family("brandnewthing-v1", "anthropic")
    assert exc.value.cause == "unknown-vendor"


def test_an_explicitly_declared_family_is_honoured_and_still_compared():
    """The escape hatch is deliberate and still subject to the cross-family rule."""
    assert assert_cross_family("brandnewthing-v1", "anthropic", "brandnew") == "brandnew"
    with pytest.raises(GateError, match="cross-family violation"):
        assert_cross_family("brandnewthing-v1", "anthropic", "anthropic")
