"""Tests for the mutation gate's one legal skip.

The bug these pin was a fail-OPEN: gate_ci.sh read `module-path` with grep+sed, which stripped
surrounding quotes but not a trailing inline comment, so the repo's OWN shipped cosmic-ray.toml
parsed as a path that can never exist and the gate skipped itself — under MUTATION_POLICY=enforce
too. It survived because no test ever fed the parser the real config, so these use the shipped file
as the fixture rather than a hand-written minimal one.

Invariant: SKIP is only ever reached from a cleanly parsed scalar that genuinely does not exist.
Every ambiguous shape must RUN_GATE.
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from mutation_target import (  # noqa: E402
    RUN_GATE,
    SKIP,
    decide,
    scalar_module_path,
)

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "scripts" / "mutation_target.py"
STOCK_CONFIG = REPO / "cosmic-ray.toml"

pytestmark = pytest.mark.subprocess(
    "pins the resolver's exit codes and argv handling, which only a process has"
)


def stock_text() -> str:
    return STOCK_CONFIG.read_text(encoding="utf-8")


class TestShippedConfig:
    def test_the_shipped_config_parses_to_a_bare_path(self):
        # The regression: the old parser produced `src" # your package/dir under test (string or
        # list)` from this exact line.
        assert scalar_module_path(stock_text()) == "src"

    def test_shipped_config_runs_the_gate_when_its_module_path_exists(self, tmp_path):
        (tmp_path / "src").mkdir()
        code, path = decide(stock_text(), tmp_path)
        assert (code, path) == (RUN_GATE, "src")

    def test_shipped_config_skips_only_when_its_module_path_is_absent(self, tmp_path):
        code, path = decide(stock_text(), tmp_path)
        assert (code, path) == (SKIP, "src")


class TestScalarParsing:
    def test_quoted_value_with_a_trailing_inline_comment(self):
        toml = '[cosmic-ray]\nmodule-path = "pkg"  # what to mutate\n'
        assert scalar_module_path(toml) == "pkg"

    def test_a_hash_inside_a_quoted_path_is_part_of_the_path(self):
        toml = '[cosmic-ray]\nmodule-path = "src/od#d"  # trailing comment\n'
        assert scalar_module_path(toml) == "src/od#d"

    def test_single_quoted_literal_string(self):
        toml = "[cosmic-ray]\nmodule-path = 'src/pkg'\n"
        assert scalar_module_path(toml) == "src/pkg"

    def test_indented_key_is_still_read(self):
        toml = '[cosmic-ray]\n  module-path = "pkg"\n'
        assert scalar_module_path(toml) == "pkg"

    def test_a_module_path_under_another_table_is_not_borrowed(self):
        # `module-path` outside [cosmic-ray] is not the gate's target; guessing at it would let an
        # unrelated key drive the skip decision.
        toml = '[cosmic-ray]\ntimeout = 30.0\n\n[other]\nmodule-path = "nope"\n'
        assert scalar_module_path(toml) is None


class TestEverythingAmbiguousRunsTheGate:
    def test_list_form_falls_through(self, tmp_path):
        toml = '[cosmic-ray]\nmodule-path = ["a", "b"]\n'
        assert scalar_module_path(toml) is None
        assert decide(toml, tmp_path)[0] == RUN_GATE

    def test_unquoted_value_is_invalid_toml_and_never_skips(self, tmp_path):
        # Bare `src` is not valid TOML at all — cosmic-ray would reject it too. The gate runs and
        # lets cosmic-ray fail closed on it; it must not be read as "nothing to mutate".
        toml = "[cosmic-ray]\nmodule-path = src  # unquoted\n"
        assert decide(toml, tmp_path)[0] == RUN_GATE

    def test_unparseable_toml_never_skips(self, tmp_path):
        assert decide("[cosmic-ray\nmodule-path =", tmp_path)[0] == RUN_GATE

    def test_missing_key_never_skips(self, tmp_path):
        assert decide("[cosmic-ray]\ntimeout = 30.0\n", tmp_path)[0] == RUN_GATE

    def test_missing_section_never_skips(self, tmp_path):
        assert decide("", tmp_path)[0] == RUN_GATE

    def test_non_string_value_never_skips(self, tmp_path):
        assert decide("[cosmic-ray]\nmodule-path = 3\n", tmp_path)[0] == RUN_GATE

    def test_empty_string_value_never_skips(self, tmp_path):
        # "" resolves to the repo root, which always exists — but it names no module either, so it
        # is ambiguous rather than skippable.
        assert decide('[cosmic-ray]\nmodule-path = ""\n', tmp_path)[0] == RUN_GATE

    def test_a_file_module_path_runs_the_gate(self, tmp_path):
        (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
        assert decide('[cosmic-ray]\nmodule-path = "mod.py"\n', tmp_path)[0] == RUN_GATE


class TestCli:
    """gate_ci.sh branches on the exit code alone, so pin the process contract."""

    def run(self, config: Path, root: Path):
        return subprocess.run(
            [sys.executable, str(TARGET), "--root", str(root), str(config)],
            capture_output=True,
            text=True,
        )

    def test_absent_path_exits_skip_and_prints_the_path(self, tmp_path):
        result = self.run(STOCK_CONFIG, tmp_path)
        assert result.returncode == SKIP
        assert result.stdout.strip() == "src"

    def test_present_path_exits_run_gate(self, tmp_path):
        (tmp_path / "src").mkdir()
        assert self.run(STOCK_CONFIG, tmp_path).returncode == RUN_GATE

    def test_unreadable_config_exits_run_gate(self, tmp_path):
        assert self.run(tmp_path / "nope.toml", tmp_path).returncode == RUN_GATE
