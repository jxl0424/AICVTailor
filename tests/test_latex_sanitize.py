"""Escaping model output, and preserving the macros a rewrite must not lose.

A model asked to work "R&D" or "a 20% uplift" into a bullet will emit bare `&`
and `%`. Both are LaTeX control characters, and either one turns a tailored
resume into a file that does not compile. This is the layer that stops that.
"""

from __future__ import annotations

import pytest

from aicvtailor.latex.sanitize import missing_tokens, protected_tokens, sanitize


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("R&D team", r"R\&D team"),
        ("improved by 20%", r"improved by 20\%"),
        ("channel #general", r"channel \#general"),
        ("snake_case naming", r"snake\_case naming"),
        ("cost $5 per run", r"cost \$5 per run"),
        ("A & B, 10% of #3", r"A \& B, 10\% of \#3"),
    ],
)
def test_bare_specials_are_escaped(raw: str, expected: str):
    assert sanitize(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (r"already escaped \& fine", r"already escaped \& fine"),
        (r"20\% stays as one escape", r"20\% stays as one escape"),
        (r"mixed \& and & together", r"mixed \& and \& together"),
    ],
)
def test_existing_escapes_are_not_doubled(raw: str, expected: str):
    """Double-escaping renders a literal backslash into the PDF."""
    assert sanitize(raw) == expected


def test_sanitize_is_idempotent():
    once = sanitize("R&D at 20% on #3")
    assert sanitize(once) == once


def test_dangerous_characters_become_commands():
    assert sanitize("a ~ b") == r"a \textasciitilde{} b"
    assert sanitize("2^10") == r"2\textasciicircum{}10"
    assert sanitize("a\\b") == r"a\textbackslash{}b"


def test_stray_braces_are_escaped():
    """Unbalanced braces from a model would swallow the rest of the document."""
    assert sanitize("value {unclosed") == r"value \{unclosed"


def test_math_runs_survive_untouched():
    assert sanitize("split $|$ here") == "split $|$ here"


def test_protected_macros_are_preserved_when_declared():
    keep = protected_tokens(r"Built \textbf{the thing} fast")
    out = sanitize(r"Rebuilt \textbf{the thing} at 20% speed", keep)
    assert r"\textbf{the thing}" in out
    assert r"20\%" in out


def test_undeclared_backslash_is_still_neutralised():
    """A macro the source never had is not trusted -- the model may have
    invented it, and an unknown command is a compile failure."""
    out = sanitize(r"uses \nonexistentmacro here")
    assert "\\nonexistentmacro" not in out
    assert r"\textbackslash{}" in out


class TestProtectedTokens:
    def test_extracts_math_and_full_macro_calls(self):
        tokens = protected_tokens(r"\textbf{Name} $|$ \emph{Tools}")
        assert r"\textbf{Name}" in tokens
        assert r"\emph{Tools}" in tokens
        assert "$|$" in tokens

    def test_multi_argument_macros_are_one_token(self):
        tokens = protected_tokens(r"see \href{https://x.test}{\underline{Link}}")
        assert r"\href{https://x.test}{\underline{Link}}" in tokens

    def test_escapes_are_not_protected(self):
        """`\\%` is a percent sign. Protecting it would force every rewrite to
        keep the original's exact numbers, which is the opposite of the goal."""
        assert protected_tokens(r"gained 26\% recall") == ()

    def test_plain_text_has_no_protected_tokens(self):
        assert protected_tokens("just an ordinary sentence") == ()


class TestMissingTokens:
    def test_reports_a_dropped_macro(self):
        original = r"Built \textbf{X} with $|$ separators"
        assert missing_tokens(original, "Built X with separators") == (
            r"\textbf{X}",
            "$|$",
        )

    def test_a_reordered_macro_is_not_missing(self):
        original = r"\textbf{X} came first"
        assert missing_tokens(original, r"Later came \textbf{X}") == ()

    def test_a_faithful_rewrite_reports_nothing(self):
        original = r"Used \emph{tool} on data"
        assert missing_tokens(original, r"Applied \emph{tool} to the dataset") == ()
