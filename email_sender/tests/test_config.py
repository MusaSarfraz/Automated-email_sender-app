"""Tests for settings load/save and path handling."""

from __future__ import annotations

import os

import pytest

from email_sender.core import config
from email_sender.core.config import Settings, load_settings, save_settings


def test_defaults():
    s = Settings()
    assert s.sender_type == "gmail"
    assert s.daily_cap == config.DEFAULT_DAILY_CAP
    assert s.send_gap_seconds == config.SEND_GAP_SECONDS == 80


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "settings.json"
    s = Settings(sender_type="outlook", outlook_address="a@outlook.com", daily_cap=200)
    save_settings(s, path)
    loaded = load_settings(path)
    assert loaded == s


def test_load_missing_returns_defaults(tmp_path):
    loaded = load_settings(tmp_path / "nope.json")
    assert loaded == Settings()


def test_load_corrupt_returns_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json", encoding="utf-8")
    assert load_settings(path) == Settings()


def test_unknown_keys_ignored(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text('{"sender_type": "gmail", "mystery": 1}', encoding="utf-8")
    assert load_settings(path) == Settings()


def test_invalid_sender_type_rejected():
    with pytest.raises(ValueError):
        Settings(sender_type="yahoo")


def test_invalid_daily_cap_rejected():
    with pytest.raises(ValueError):
        Settings(daily_cap=0)


def test_data_dir_honours_env_override():
    # conftest sets EMAILSENDER_DATA_DIR for the session.
    override = os.environ["EMAILSENDER_DATA_DIR"]
    assert str(config.data_dir()) == override
    assert config.settings_path().parent == config.data_dir()


def test_campaign_dir_created(tmp_path, monkeypatch):
    monkeypatch.setenv("EMAILSENDER_DATA_DIR", str(tmp_path))
    d = config.campaign_dir("c1")
    assert d.exists()
    assert d.name == "c1"
