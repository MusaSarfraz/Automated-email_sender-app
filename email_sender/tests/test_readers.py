"""Tests for sheet reading, header matching, dedup, and email validation."""

from __future__ import annotations

from email_sender.core import readers
from email_sender.core.config import Status
from email_sender.core.readers import REQUIRED_COLUMNS, build_result, normalize_header

# A full header row (name/company are optional but commonly present).
FULL_HEADERS = ["name", "company", "email", "subject", "body"]


def test_normalize_header_ignores_case_and_spaces():
    assert normalize_header("  First Name ") == "firstname"
    assert normalize_header("EMAIL") == "email"
    assert normalize_header("Company ") == "company"


def test_header_matching_case_and_space_insensitive():
    headers = ["Name", " COMPANY ", "E mail", "Subject", "BODY"]
    rows = [["John", "Acme", "john@acme.com", "Hi {{ name }}", "Hello"]]
    result = build_result(headers, rows)
    # "E mail" -> "email" after removing spaces, so all required columns resolve.
    assert result.ok
    assert result.missing_columns == []
    assert result.rows[0].status == Status.PENDING
    assert result.rows[0].data["name"] == "John"


def test_missing_required_columns_reported():
    headers = ["name", "company", "subject", "body"]  # no email
    result = build_result(headers, [["a", "b", "c", "d"]])
    assert not result.ok
    assert result.missing_columns == ["email"]
    assert result.rows == []


def test_all_required_missing_lists_all():
    result = build_result(["foo", "bar"], [["1", "2"]])
    assert set(result.missing_columns) == set(REQUIRED_COLUMNS)


def test_email_column_alias_to():
    # "To" should be accepted as the email column; name/company optional.
    headers = ["Company", "To", "Subject", "Body"]
    result = build_result(headers, [["Acme", "john@acme.com", "s", "b"]])
    assert result.ok
    assert result.rows[0].email == "john@acme.com"
    assert result.rows[0].raw_email == "john@acme.com"


def test_name_and_company_are_optional():
    headers = ["email", "subject", "body"]
    result = build_result(headers, [["a@x.com", "s", "b"]])
    assert result.ok
    assert result.rows[0].status == Status.PENDING


def test_explicit_email_preferred_over_to_alias():
    headers = ["To", "email", "subject", "body"]
    result = build_result(headers, [["wrong@x.com", "right@x.com", "s", "b"]])
    assert result.rows[0].email == "right@x.com"


def test_duplicate_email_first_sent_rest_marked_duplicate():
    headers = list(FULL_HEADERS)
    rows = [
        ["John", "Acme", "john@acme.com", "s", "b"],
        ["Johnny", "Acme2", "JOHN@acme.com ", "s", "b"],  # same identity, diff case/space
        ["Sara", "Beta", "sara@beta.io", "s", "b"],
    ]
    result = build_result(headers, rows)
    assert result.rows[0].status == Status.PENDING
    assert result.rows[1].status == Status.DUPLICATE
    assert result.rows[1].error
    assert result.rows[2].status == Status.PENDING


def test_invalid_and_blank_emails():
    headers = list(FULL_HEADERS)
    rows = [
        ["A", "C", "not-an-email", "s", "b"],
        ["B", "C", "   ", "s", "b"],
        ["D", "C", "good@x.com", "s", "b"],
    ]
    result = build_result(headers, rows)
    assert result.rows[0].status == Status.INVALID
    assert result.rows[1].status == Status.INVALID
    assert result.rows[1].error == "Email address is blank"
    assert result.rows[2].status == Status.PENDING


def test_extra_columns_available_in_data():
    headers = [*FULL_HEADERS, "City"]
    rows = [["A", "C", "a@x.com", "s", "b", "Dubai"]]
    result = build_result(headers, rows)
    assert result.rows[0].data["city"] == "Dubai"


def test_blank_rows_skipped_and_row_numbers_sequential():
    headers = list(FULL_HEADERS)
    rows = [
        ["A", "C", "a@x.com", "s", "b"],
        ["", "", "", "", ""],  # fully blank -> skipped
        ["B", "C", "b@x.com", "s", "b"],
    ]
    result = build_result(headers, rows)
    assert [r.row_number for r in result.rows] == [1, 2]
    assert [r.email for r in result.rows] == ["a@x.com", "b@x.com"]


def test_counts_summary():
    headers = list(FULL_HEADERS)
    rows = [
        ["A", "C", "a@x.com", "s", "b"],
        ["B", "C", "a@x.com", "s", "b"],  # duplicate
        ["C", "C", "bad", "s", "b"],      # invalid
    ]
    counts = build_result(headers, rows).counts()
    assert counts[Status.PENDING] == 1
    assert counts[Status.DUPLICATE] == 1
    assert counts[Status.INVALID] == 1


def test_read_excel_roundtrip(xlsx_factory):
    path = xlsx_factory(
        "leads.xlsx",
        ["Name", "Company", "Email", "Subject", "Body"],
        [
            ["John", "Acme", "john@acme.com", "Hi {{ name }}", "Hello {{ name }}"],
            ["Sara", "Beta", "sara@beta.io", "Idea", "Hi"],
        ],
    )
    result = readers.read_excel(path)
    assert result.ok
    assert len(result.rows) == 2
    assert result.rows[0].email == "john@acme.com"
    assert result.rows[0].raw_email == "john@acme.com"
