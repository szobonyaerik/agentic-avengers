"""Tests for gate_runner's cross-family guard.

The one property that must hold: a routing prefix (openrouter/, opencode/, opencode-go/) never
hides a model's real family. Before this was pinned, `opencode-go/claude-x` resolved to family
'opencode-go' and sailed past the anthropic-vs-anthropic check — a fail-open hole in the
pipeline's only independence mechanism.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from gate_runner import assert_cross_family, model_family  # noqa: E402


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
