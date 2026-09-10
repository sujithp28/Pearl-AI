"""
The wire shape of a checkpoint, shared by both protocol adapters.

These keys are a contract, not an implementation detail. The VS Code
extension already reads them over JSON-RPC and the web UI will read the
same names over HTTP, so a rename here breaks a shipped client. The
tests assert the exact key set for that reason: a test that only checked
"some dict came back" would let a rename through silently, which is how
the two adapters drifted apart in the first place.
"""

from __future__ import annotations

from src.tools.checkpoint_serialize import (
    checkpoint_to_dict,
    restore_report_to_dict,
)
from src.tools.checkpoints import Checkpoint, RestoreReport


class TestCheckpointToDict:
    def test_keys_are_exactly_the_protocol_contract(self):
        cp = Checkpoint(
            id="0123456789abcdef0123456789abcdef01234567",
            label="Manual checkpoint",
            created_at="2026-09-10T12:00:00+00:00",
        )

        assert set(checkpoint_to_dict(cp)) == {"id", "shortId", "label", "createdAt"}

    def test_short_id_is_the_first_eight_characters(self):
        cp = Checkpoint(
            id="0123456789abcdef0123456789abcdef01234567",
            label="Manual checkpoint",
            created_at="2026-09-10T12:00:00+00:00",
        )

        assert checkpoint_to_dict(cp)["shortId"] == "01234567"

    def test_values_come_from_the_checkpoint(self):
        cp = Checkpoint(
            id="abc123",
            label="Before the risky edit",
            created_at="2026-09-10T12:00:00+00:00",
        )

        d = checkpoint_to_dict(cp)

        assert d["id"] == "abc123"
        assert d["label"] == "Before the risky edit"
        assert d["createdAt"] == "2026-09-10T12:00:00+00:00"


class TestRestoreReportToDict:
    def test_keys_are_exactly_the_protocol_contract(self):
        report = RestoreReport(checkpoint_id="abc123", restored=[], removed=[])

        assert set(restore_report_to_dict(report)) == {
            "checkpointId",
            "restored",
            "removed",
            "changedAnything",
        }

    def test_changed_anything_is_false_for_an_empty_restore(self):
        report = RestoreReport(checkpoint_id="abc123", restored=[], removed=[])

        assert restore_report_to_dict(report)["changedAnything"] is False

    def test_changed_anything_is_true_when_files_moved(self):
        """
        The browser gates its confirmation prompt on this flag, so a
        restore that removes files but restores none must still report
        true. Only checking `restored` would offer no confirmation for
        the most destructive case there is.
        """
        report = RestoreReport(
            checkpoint_id="abc123", restored=[], removed=["scratch.py"]
        )

        assert restore_report_to_dict(report)["changedAnything"] is True

    def test_file_lists_are_carried_through(self):
        report = RestoreReport(
            checkpoint_id="abc123",
            restored=["src/main.py"],
            removed=["scratch.py"],
        )

        d = restore_report_to_dict(report)

        assert d["restored"] == ["src/main.py"]
        assert d["removed"] == ["scratch.py"]
