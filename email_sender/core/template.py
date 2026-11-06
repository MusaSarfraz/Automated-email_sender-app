"""Jinja2 rendering of subject/body with strict missing-placeholder handling.

A row is only ever sent once every placeholder resolves to a real value. If a
template references ``{{ company }}`` but that cell is empty or the column is
missing, rendering raises :class:`MissingPlaceholderError` with a clear,
non-technical reason instead of silently sending a blank.
"""

from __future__ import annotations

from jinja2 import Environment, StrictUndefined, TemplateSyntaxError, meta
from jinja2.exceptions import UndefinedError

# autoescape is off: bodies are plain text, not HTML, and we must not turn
# "Tom & Jerry" into "Tom &amp; Jerry".
_env = Environment(undefined=StrictUndefined, autoescape=False)


class TemplateError(Exception):
    """Base class for template problems that should mark a row FAILED."""


class MissingPlaceholderError(TemplateError):
    """A placeholder had no value (missing column or empty cell)."""


class TemplateSyntaxErrorWrapped(TemplateError):
    """The template itself is malformed (e.g. unbalanced ``{{``)."""


def placeholders(template_str: str) -> set[str]:
    """Return the set of variable names referenced by a template.

    Raises :class:`TemplateSyntaxErrorWrapped` if the template cannot be parsed.
    """
    try:
        ast = _env.parse(template_str)
    except TemplateSyntaxError as exc:
        raise TemplateSyntaxErrorWrapped(_friendly_syntax(exc)) from exc
    return meta.find_undeclared_variables(ast)


def missing_placeholders(template_str: str, context: dict[str, object]) -> list[str]:
    """Return referenced placeholders that are absent or blank in ``context``.

    A value counts as missing when the key is absent, ``None``, or an
    empty/whitespace-only string.
    """
    missing: list[str] = []
    for name in sorted(placeholders(template_str)):
        value = context.get(name)
        if value is None or (isinstance(value, str) and value.strip() == ""):
            missing.append(name)
    return missing


def render(template_str: str, context: dict[str, object]) -> str:
    """Render a single template string, raising on any missing placeholder."""
    missing = missing_placeholders(template_str, context)
    if missing:
        raise MissingPlaceholderError(_missing_reason(missing))
    try:
        return _env.from_string(template_str).render(**context)
    except UndefinedError as exc:  # pragma: no cover - guarded by missing check
        raise MissingPlaceholderError(str(exc)) from exc
    except TemplateSyntaxError as exc:
        raise TemplateSyntaxErrorWrapped(_friendly_syntax(exc)) from exc


def render_email(subject_tpl: str, body_tpl: str, context: dict[str, object]) -> tuple[str, str]:
    """Render subject and body together.

    All missing placeholders across *both* templates are reported in one
    message so the user sees the full picture, not just the first problem.
    """
    missing = sorted(
        set(missing_placeholders(subject_tpl, context))
        | set(missing_placeholders(body_tpl, context))
    )
    if missing:
        raise MissingPlaceholderError(_missing_reason(missing))
    subject = render(subject_tpl, context)
    body = render(body_tpl, context)
    return subject, body


def _missing_reason(missing: list[str]) -> str:
    names = ", ".join(missing)
    if len(missing) == 1:
        return f"Missing value for placeholder: {names}"
    return f"Missing values for placeholders: {names}"


def _friendly_syntax(exc: TemplateSyntaxError) -> str:
    return f"Template has a formatting error: {exc.message}"
