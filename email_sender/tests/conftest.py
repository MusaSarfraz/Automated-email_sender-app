"""Shared pytest fixtures and isolation.

Point the app's data directory at a throwaway temp folder for the whole test
session so tests never read or write the real ``%APPDATA%\\EmailSender``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_data_dir() -> None:
    tmp = tempfile.mkdtemp(prefix="emailsender_tests_")
    os.environ["EMAILSENDER_DATA_DIR"] = tmp
    yield
    # Leave cleanup to the OS temp sweeper; removing here risks Windows file locks.


@pytest.fixture()
def xlsx_factory(tmp_path: Path):
    """Return a helper that writes a list-of-dicts (or rows) to an .xlsx file."""

    import pandas as pd

    def _make(name: str, columns: list[str], rows: list[list]) -> Path:
        df = pd.DataFrame(rows, columns=columns)
        path = tmp_path / name
        df.to_excel(path, index=False, engine="openpyxl")
        return path

    return _make
