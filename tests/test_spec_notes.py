"""The known-open list: where a non-blocking observation goes instead of nowhere.

The gate this replaces had two states for anything it noticed — block, or say nothing — and with
"when unsure, choose NO-GO" as the tie-break everything drifted toward blocking. Notes are the
counterweight, and they are only a counterweight if they are actually written down and actually read.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from spec_notes import notes_path, write  # noqa: E402

SPEC = """---
feature: demo
phase: 1-core
spec: 1.1-a
spec_gate: pending
---

# Spec
"""


def spec_file(tmp_path: Path) -> Path:
    path = tmp_path / "spec.md"
    path.write_text(SPEC, encoding="utf-8")
    return path


def decision(notes: list[dict], blocking: list[dict] | None = None) -> dict:
    return {"verdict": "BLOCKED" if blocking else "APPROVED",
            "blocking": blocking or [], "notes": notes}


NOTE = {"id": "o1", "area": "binding", "spec_ref": "R1.1.2",
        "statement": "R1.1.2 gives no sentence saying why an e2e cannot see it.",
        "category": "note", "why": "the implementer can still build and trace it"}


def test_a_note_is_written_beside_its_spec(tmp_path: Path) -> None:
    spec = spec_file(tmp_path)
    written = write(spec, decision([NOTE]))
    assert written == notes_path(spec) == tmp_path / "spec-notes.md"
    text = written.read_text()
    assert "R1.1.2" in text
    assert "no sentence saying why an e2e cannot see it" in text
    assert "the implementer can still build and trace it" in text


def test_it_declares_its_reader_like_every_pipeline_document(tmp_path: Path) -> None:
    text = write(spec_file(tmp_path), decision([NOTE])).read_text()
    assert "readers: implementer @ once, before building this spec" in text


def test_it_carries_the_spec_s_own_identity(tmp_path: Path) -> None:
    text = write(spec_file(tmp_path), decision([NOTE])).read_text()
    assert "feature: demo" in text
    assert "phase: 1-core" in text
    assert "spec: 1.1-a" in text


def test_it_says_a_note_is_not_a_requirement(tmp_path: Path) -> None:
    """A note read as a requirement is the ratchet coming back through the side door."""
    text = write(spec_file(tmp_path), decision([NOTE])).read_text()
    assert "do not treat any of" in text
    assert "them as a requirement" in text


def test_no_notes_writes_no_file(tmp_path: Path) -> None:
    """A document no stage reads does not get written."""
    spec = spec_file(tmp_path)
    assert write(spec, decision([])) is None
    assert not notes_path(spec).exists()


def test_a_later_clean_run_removes_the_stale_file(tmp_path: Path) -> None:
    """A stale sidecar tells the implementer something the gate no longer says."""
    spec = spec_file(tmp_path)
    write(spec, decision([NOTE]))
    assert notes_path(spec).exists()
    write(spec, decision([]))
    assert not notes_path(spec).exists()


def test_it_is_regenerated_not_appended(tmp_path: Path) -> None:
    """It is the current gate's view, not a log — otherwise it grows without anyone deciding to."""
    spec = spec_file(tmp_path)
    write(spec, decision([NOTE]))
    other = {**NOTE, "id": "o2", "spec_ref": "R1.1.9", "statement": "something else entirely"}
    text = write(spec, decision([other])).read_text()
    assert "something else entirely" in text
    assert "R1.1.2" not in text


def test_a_blocked_spec_s_notes_say_the_blockers_are_elsewhere(tmp_path: Path) -> None:
    """Repeating them here would invite answering a blocker in the sidecar instead of the spec."""
    blocking = [{"id": "o9", "category": "contradiction", "spec_ref": "R1.1.1",
                 "statement": "two statements cannot both hold"}]
    text = write(spec_file(tmp_path), decision([NOTE], blocking)).read_text()
    assert "BLOCKED on 1 finding(s)" in text
    assert "two statements cannot both hold" not in text


def test_a_spec_with_no_frontmatter_still_gets_notes(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Spec with no frontmatter\n")
    text = write(spec, decision([NOTE])).read_text()
    assert "R1.1.2" in text


def test_the_decision_json_round_trips(tmp_path: Path) -> None:
    """The hook hands this file over as JSON on disk; the shape has to survive that."""
    payload = tmp_path / "decision.json"
    payload.write_text(json.dumps(decision([NOTE])))
    text = write(spec_file(tmp_path), json.loads(payload.read_text())).read_text()
    assert "R1.1.2" in text
