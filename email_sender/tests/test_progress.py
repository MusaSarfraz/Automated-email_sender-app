"""Tests for atomic progress persistence and reload."""

from __future__ import annotations

import json

import pytest

from email_sender.core import progress as P
from email_sender.core.progress import Progress, load_progress, progress_path, save_progress


def _sample(campaign_dir) -> Progress:
    return Progress(
        campaign_id="2026-11-04_dubai",
        source="Dubai_Leads.xlsx",
        source_type=P.SOURCE_EXCEL,
        state=P.STATE_RUNNING,
        last_sent_at="2026-11-04T14:46:12+05:00",
        sends_last_24h=["2026-11-04T14:44:52+05:00", "2026-11-04T14:46:12+05:00"],
        rows={"john@acme.com": {"status": "SENT", "sent_at": "2026-11-04T14:46:12+05:00"}},
    )


def test_save_and_reload_roundtrip(tmp_path):
    path = progress_path(tmp_path)
    prog = _sample(tmp_path)
    save_progress(path, prog)
    loaded = load_progress(path)
    assert loaded == prog


def test_save_is_atomic_no_leftover_temp(tmp_path):
    path = progress_path(tmp_path)
    save_progress(path, _sample(tmp_path))
    save_progress(path, _sample(tmp_path))  # overwrite
    leftovers = [p.name for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []
    assert path.exists()


def test_previous_file_intact_if_write_body_fails(tmp_path, monkeypatch):
    path = progress_path(tmp_path)
    good = _sample(tmp_path)
    save_progress(path, good)
    original_bytes = path.read_bytes()

    # Force json.dumps to blow up so no replace happens.
    monkeypatch.setattr(P.json, "dumps", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        save_progress(path, good)

    # Original file is untouched and no temp litter remains.
    assert path.read_bytes() == original_bytes
    assert [p for p in tmp_path.iterdir() if p.suffix == ".tmp"] == []


def test_unknown_keys_ignored_on_load(tmp_path):
    path = progress_path(tmp_path)
    payload = {
        "campaign_id": "c1",
        "source": "s.xlsx",
        "source_type": "excel",
        "state": "ready",
        "future_field": "ignored",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = load_progress(path)
    assert loaded.campaign_id == "c1"
    assert not hasattr(loaded, "future_field")


def test_row_helpers_and_counts():
    prog = Progress(campaign_id="c", source="s", source_type="excel")
    prog.set_row("a@x.com", status="SENT")
    prog.set_row("a@x.com", message_id="<1>")  # merge, not replace
    prog.set_row("b@x.com", status="PENDING")
    assert prog.row("a@x.com") == {"status": "SENT", "message_id": "<1>"}
    assert prog.count_by_status() == {"SENT": 1, "PENDING": 1}


def test_invalid_state_rejected():
    with pytest.raises(ValueError):
        Progress(campaign_id="c", source="s", source_type="excel", state="bogus")


def test_invalid_source_type_rejected():
    with pytest.raises(ValueError):
        Progress(campaign_id="c", source="s", source_type="mysql")


def test_name_defaults_to_campaign_id():
    prog = Progress(campaign_id="c1", source="s", source_type="excel")
    assert prog.name == "c1"
