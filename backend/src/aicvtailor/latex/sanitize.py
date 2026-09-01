"""Protecting macros and escaping model output for LaTeX.

Two jobs, both about the same failure: a rewritten bullet that breaks the
build. A model asked to mention "R&D" or "a 20% uplift" will emit bare `&` and
`%`, which are LaTeX control characters. And a model rewriting a bullet that
contained `\\textbf{...}` will sometimes drop it, silently losing formatting or
a hyperlink.
"""

from __future__ import annotations

import re

# Control symbols: a backslash plus one non-letter. `\%` is a literal percent,
# not a macro, so it must never be treated as a protected token.
_ESCAPE_RE = re.compile(r"\\[%&#_${}]")
_MATH_RE = re.compile(r"(?<!\\)\$.*?(?<!\\)\$", re.DOTALL)
_MACRO_RE = re.compile(r"\\[A-Za-z@]+\*?")

# Bare characters that must be escaped to survive compilation.
_SIMPLE_ESCAPES = {
    "&": r"\&",
    "%": r"\%",
    "#": r"\#",
    "_": r"\_",
    "$": r"\$",
}
_COMMAND_ESCAPES = {
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
}


def _read_group_from(text: str, i: int) -> int | None:
    """Return the index just past a braced group starting at `i`."""
    if i >= len(text) or text[i] != "{":
        return None
    depth = 0
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def protected_tokens(text: str) -> tuple[str, ...]:
    """Extract the constructs a rewrite must carry through verbatim.

    Math runs (`$|$`) and full macro calls with their arguments
    (`\\href{url}{label}`, `\\textbf{...}`). Escapes like `\\%` are excluded --
    they are ordinary characters that the escaper handles, and treating them as
    protected would force a rewrite to keep every original percentage.
    """
    tokens: list[str] = []
    claimed: list[tuple[int, int]] = []

    for match in _MATH_RE.finditer(text):
        tokens.append(match.group())
        claimed.append(match.span())

    for match in _MACRO_RE.finditer(text):
        start, end = match.span()
        if any(lo <= start < hi for lo, hi in claimed):
            continue
        # Swallow any braced arguments so `\href{a}{b}` is one token.
        cursor = end
        while cursor < len(text):
            nxt = _read_group_from(text, cursor)
            if nxt is None:
                break
            cursor = nxt
        tokens.append(text[start:cursor])
        claimed.append((start, cursor))

    # Longest first, so a caller checking preservation matches the full
    # `\href{...}{...}` before the bare `\href` prefix.
    return tuple(sorted(set(tokens), key=len, reverse=True))


def missing_tokens(original: str, rewritten: str) -> tuple[str, ...]:
    """Protected tokens present in `original` but absent from `rewritten`.

    Position is not checked -- a rewrite may move a macro, only not lose it.
    """
    return tuple(t for t in protected_tokens(original) if t not in rewritten)


def _immune_ranges(text: str, keep: tuple[str, ...]) -> list[tuple[int, int]]:
    """Byte ranges the escaper must leave exactly as they are."""
    ranges: list[tuple[int, int]] = []

    for token in keep:
        start = 0
        while (idx := text.find(token, start)) != -1:
            ranges.append((idx, idx + len(token)))
            start = idx + len(token)

    for pattern in (_MATH_RE, _ESCAPE_RE):
        ranges.extend(m.span() for m in pattern.finditer(text))

    if not ranges:
        return []

    ranges.sort()
    merged = [ranges[0]]
    for lo, hi in ranges[1:]:
        if lo <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return merged


def sanitize(text: str, keep: tuple[str, ...] = ()) -> str:
    """Make `text` safe to splice into a LaTeX argument.

    Everything outside an already-valid escape, a math run, or one of the
    `keep` tokens gets escaped. Passing the source bullet's protected tokens as
    `keep` is what makes this macro-aware instead of blindly escaping every
    backslash the model legitimately preserved.
    """
    immune = _immune_ranges(text, keep)
    out: list[str] = []
    i, n = 0, len(text)
    next_range = 0

    while i < n:
        while next_range < len(immune) and immune[next_range][1] <= i:
            next_range += 1
        if next_range < len(immune) and immune[next_range][0] <= i < immune[next_range][1]:
            lo, hi = immune[next_range]
            out.append(text[lo:hi])
            i = hi
            continue

        ch = text[i]
        out.append(_SIMPLE_ESCAPES.get(ch) or _COMMAND_ESCAPES.get(ch) or ch)
        i += 1

    return "".join(out)
