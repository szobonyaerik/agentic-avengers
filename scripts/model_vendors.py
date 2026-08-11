#!/usr/bin/env python3
"""Vendor family of a model id — the pipeline's only cross-family primitive.

Every gate in this pipeline claims independence on one fact: the model forming the judgement is a
different *vendor* than the model that authored the work. That claim is only as good as this table.

The old table knew seven vendors and returned the raw model id for everything else, so two models
from the SAME vendor read as two different families:

    model_family("glm-5.1") -> "glm-5.1"      # not a family, a model id
    model_family("glm-5.2") -> "glm-5.2"      # "different" from the line above

Against `AUTHOR_FAMILY=anthropic` both still fail safe, but the moment either side of a comparison is
an unlisted vendor the comparison is meaningless — and a *false independence* reads exactly like a
real one. A phase was gated on glm-5.2 while this was live, so it is not hypothetical.

So an unrecognised vendor is now a LOUD REFUSAL, never a silent pass. Add the vendor here, or say so
explicitly with `GATE_MODEL_FAMILY` / `--model-family` when you are gating a model this table has
never heard of. Both directions are deliberate; neither is silent.

Matching is longest-prefix on a token boundary, so `hy` matches `hy-large` but never `hyperion-x`,
and a new short alias cannot quietly swallow an unrelated vendor's models.

    python3 scripts/model_vendors.py family <model-id>     print the family, exit 1 if unknown

Stdlib only — this is imported by gate_runner.py, which ships vendored.
"""

from __future__ import annotations

import sys

#: Routing prefixes that carry no vendor information. A router in front of a model must never hide
#: the family behind it: `opencode-go/claude-x` is anthropic, not "opencode-go".
ROUTER_PREFIXES = ("openrouter", "opencode", "opencode-go", "opencode-anthropic")

#: token seen at the head of a model id -> vendor family.
#:
#: The family name is the vendor, not the model line, so every model a vendor ships collapses to one
#: value. The seven entries that predate this table keep the exact family strings they already
#: returned (`deepseek`, `google`, `anthropic`, `openai`, `grok`, `qwen`, `mistral`) — those strings
#: are what live deployments have in `AUTHOR_FAMILY`, and renaming them would turn a working
#: cross-family config into a fail-closed stop on upgrade. New entries use the vendor's own name.
VENDOR_FAMILIES = {
    # — the seven that predate this table; family strings pinned for config compatibility —
    "deepseek": "deepseek",
    "gemini": "google",
    "gemma": "google",
    "google": "google",
    "claude": "anthropic",
    "anthropic": "anthropic",
    "opus": "anthropic",
    "sonnet": "anthropic",
    "haiku": "anthropic",
    "gpt": "openai",
    "openai": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "grok": "grok",
    "xai": "grok",
    "qwen": "qwen",
    "qwq": "qwen",
    "alibaba": "qwen",
    "mistral": "mistral",
    "mistralai": "mistral",
    "ministral": "mistral",
    "magistral": "mistral",
    "codestral": "mistral",
    "devstral": "mistral",
    # — the vendors that used to fall through and return a model id as their "family" —
    "glm": "zhipu",
    "chatglm": "zhipu",
    "zai": "zhipu",
    "z-ai": "zhipu",
    "zhipu": "zhipu",
    "kimi": "moonshot",
    "moonshot": "moonshot",
    "moonshotai": "moonshot",
    "minimax": "minimax",
    "abab": "minimax",
    "mimo": "xiaomi",
    "xiaomi": "xiaomi",
    "hunyuan": "tencent",
    "hy": "tencent",
    "tencent": "tencent",
    # — the rest of the field, so the next unlisted vendor is rarer than the last —
    "llama": "meta",
    "codellama": "meta",
    "meta": "meta",
    "command": "cohere",
    "cohere": "cohere",
    "nova": "amazon",
    "amazon": "amazon",
    "phi": "microsoft",
    "microsoft": "microsoft",
    "mai": "microsoft",
    "ernie": "baidu",
    "baidu": "baidu",
    "doubao": "bytedance",
    "seed": "bytedance",
    "bytedance": "bytedance",
    "yi": "01ai",
    "01-ai": "01ai",
    "step": "stepfun",
    "stepfun": "stepfun",
    "nemotron": "nvidia",
    "nvidia": "nvidia",
    "sonar": "perplexity",
    "perplexity": "perplexity",
    "granite": "ibm",
    "ibm": "ibm",
    "jamba": "ai21",
    "ai21": "ai21",
    "olmo": "ai2",
    "molmo": "ai2",
    "falcon": "tii",
    "reka": "reka",
}

#: Characters that end a vendor token inside a model id (`glm-5.2`, `gpt_4o`, `claude.x`).
_BOUNDARY = "-_.:0123456789"


class UnknownVendor(Exception):
    """The model id names a vendor this table has never heard of.

    Raised rather than guessed. A guessed family is what makes two models from one vendor look
    independent, and an independence claim that is wrong is worse than one that is missing.
    """

    def __init__(self, model: str) -> None:
        super().__init__(
            f"unknown vendor for model {model!r}: scripts/model_vendors.py has no entry for it, so "
            "its family cannot be established and the cross-family guarantee cannot be checked. "
            "Add the vendor to VENDOR_FAMILIES, or declare it explicitly with GATE_MODEL_FAMILY="
            "<family> (or --model-family <family>) if you know which vendor it belongs to."
        )
        self.model = model


def strip_routers(model: str) -> list[str]:
    """The `/`-separated parts of a model id with every leading routing prefix removed."""
    parts = [p.lower() for p in str(model).split("/") if p]
    while parts and parts[0] in ROUTER_PREFIXES:
        parts = parts[1:]
    return parts


def vendor_of(token: str) -> str | None:
    """The family for one model token by longest boundary-respecting prefix, or None.

    Longest-first so `codestral` is mistral rather than cohere's `co…`, and boundary-checked so a
    two-letter alias like `hy` cannot claim `hyperion-9`.
    """
    for key in sorted(VENDOR_FAMILIES, key=len, reverse=True):
        if token == key:
            return VENDOR_FAMILIES[key]
        if token.startswith(key) and token[len(key)] in _BOUNDARY:
            return VENDOR_FAMILIES[key]
    return None


def model_family(model: str, override: str | None = None) -> str:
    """Vendor family of a model id, ignoring routing prefixes.

    deepseek/deepseek-chat            -> deepseek
    google/gemini-3.1-pro-preview     -> google
    openrouter/anthropic/claude-opus  -> anthropic
    opencode-go/deepseek-v4-pro       -> deepseek
    glm-5.1 / glm-5.2                 -> zhipu   (one vendor, one family)

    `override` (from GATE_MODEL_FAMILY / --model-family) declares the family of a model this table
    does not know. Raises UnknownVendor when neither the table nor an override can answer.
    """
    if override and override.strip():
        return override.strip().lower()
    parts = strip_routers(model)
    if not parts:
        raise UnknownVendor(str(model))
    # The vendor may be the path segment (anthropic/claude-x) or the model token itself
    # (opencode-go/claude-x), so both are candidates — nearest to the model wins ties by order.
    for token in (parts[0], parts[-1]):
        family = vendor_of(token)
        if family:
            return family
    raise UnknownVendor(str(model))


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] != "family":
        print("usage: model_vendors.py family <model-id>", file=sys.stderr)
        return 2
    try:
        print(model_family(args[1]))
    except UnknownVendor as exc:
        print(f"[model_vendors] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
