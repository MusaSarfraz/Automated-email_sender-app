"""Tests for Jinja2 rendering and strict missing-placeholder handling."""

from __future__ import annotations

import pytest

from email_sender.core import template
from email_sender.core.template import (
    MissingPlaceholderError,
    TemplateSyntaxErrorWrapped,
)


def test_render_basic_substitution():
    out = template.render("Hi {{ name }} at {{ company }}", {"name": "John", "company": "Acme"})
    assert out == "Hi John at Acme"


def test_render_preserves_line_breaks():
    body = "Hi {{ name }},\n\nLine two."
    out = template.render(body, {"name": "John"})
    assert out == "Hi John,\n\nLine two."


def test_no_html_escaping():
    out = template.render("{{ company }}", {"company": "Tom & Jerry <x>"})
    assert out == "Tom & Jerry <x>"


def test_missing_column_raises():
    with pytest.raises(MissingPlaceholderError) as exc:
        template.render("Hi {{ name }}", {})
    assert "name" in str(exc.value)


def test_empty_cell_counts_as_missing():
    with pytest.raises(MissingPlaceholderError):
        template.render("Hi {{ name }}", {"name": "   "})


def test_placeholders_detected():
    assert template.placeholders("{{ a }} {{ b }} {{ a }}") == {"a", "b"}


def test_missing_placeholders_lists_each():
    missing = template.missing_placeholders("{{ a }}-{{ b }}-{{ c }}", {"b": "x"})
    assert missing == ["a", "c"]


def test_render_email_reports_missing_across_subject_and_body():
    with pytest.raises(MissingPlaceholderError) as exc:
        template.render_email("Hi {{ name }}", "From {{ company }}", {"name": "J"})
    # company (from body) is reported even though subject was fine.
    assert "company" in str(exc.value)


def test_render_email_success():
    subject, body = template.render_email(
        "Hi {{ name }}", "From {{ company }}", {"name": "John", "company": "Acme"}
    )
    assert subject == "Hi John"
    assert body == "From Acme"


def test_template_syntax_error_wrapped():
    with pytest.raises(TemplateSyntaxErrorWrapped):
        template.render("Hi {{ name ", {"name": "John"})
