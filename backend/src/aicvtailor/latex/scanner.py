"""Low-level LaTeX scanning.

Just enough TeX lexing to find macro calls and their arguments reliably:
comments, escapes and brace nesting. It deliberately does not try to be a TeX
interpreter -- the parser above it only needs to locate byte spans, and
anything it fails to recognise is left untouched rather than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

# `\%` is a literal percent sign, `\{` a literal brace. A backslash followed by
# a single non-letter is a "control symbol" -- an escape, never a macro call.
LETTERS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


@dataclass(frozen=True, slots=True)
class MacroCall:
    """A located `\\name{...}{...}` with the byte span of each argument.

    `start`/`end` cover the whole call including the macro name and braces.
    Each entry of `args` is the span of one argument's *contents*, excluding
    its surrounding braces, so replacing that slice leaves the braces intact.
    """

    name: str
    start: int
    end: int
    args: tuple[tuple[int, int], ...]

    def arg_text(self, source: str, index: int) -> str:
        lo, hi = self.args[index]
        return source[lo:hi]


def comment_mask(source: str) -> bytearray:
    """Mark every character that sits inside a `%` comment.

    An unescaped `%` comments out the rest of the line. The mask lets the rest
    of the scanner ignore commented-out braces and macro names without having
    to strip them from the source, which would ruin the byte offsets.
    """
    mask = bytearray(len(source))
    i, n = 0, len(source)
    while i < n:
        ch = source[i]
        if ch == "\\":
            # Skip the escaped character so `\%` never starts a comment.
            i += 2
            continue
        if ch == "%":
            j = source.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                mask[k] = 1
            i = j
            continue
        i += 1
    return mask


def read_group(source: str, open_index: int, mask: bytearray) -> tuple[int, int, int] | None:
    """Read a braced group starting at `open_index`.

    Returns `(inner_start, inner_end, index_after_close)`, or None if the
    braces are unbalanced. Escaped braces and braces inside comments do not
    affect nesting depth.
    """
    if open_index >= len(source) or source[open_index] != "{":
        return None

    depth = 0
    i, n = open_index, len(source)
    while i < n:
        ch = source[i]
        if ch == "\\":
            i += 2  # `\{` and `\}` are literal, never structural
            continue
        if mask[i]:
            i += 1
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return open_index + 1, i, i + 1
        i += 1
    return None


def _skip_blanks(source: str, i: int, mask: bytearray) -> int:
    """Advance over whitespace and comments between a macro and its argument.

    LaTeX tolerates a newline between `\\resumeProjectHeading` and its `{`,
    and this CV uses that formatting throughout.
    """
    n = len(source)
    while i < n and (source[i].isspace() or mask[i]):
        i += 1
    return i


def find_calls(
    source: str,
    name: str,
    arity: int,
    mask: bytearray | None = None,
) -> list[MacroCall]:
    """Locate every `\\name` call taking `arity` braced arguments.

    A call whose arguments are unbalanced, or which is commented out, is
    skipped rather than raising: an unparseable region is left verbatim.
    """
    if mask is None:
        mask = comment_mask(source)

    calls: list[MacroCall] = []
    token = "\\" + name
    start = 0
    while True:
        idx = source.find(token, start)
        if idx == -1:
            return calls
        start = idx + len(token)

        if mask[idx]:
            continue
        # Reject a prefix match: `\resumeItem` must not match `\resumeItemListStart`.
        after = idx + len(token)
        if after < len(source) and source[after] in LETTERS:
            continue
        # Reject `\\resumeItem`, where the macro name is itself escaped text.
        backslashes = 0
        probe = idx - 1
        while probe >= 0 and source[probe] == "\\":
            backslashes += 1
            probe -= 1
        if backslashes % 2 == 1:
            continue

        args: list[tuple[int, int]] = []
        cursor = after
        ok = True
        for _ in range(arity):
            cursor = _skip_blanks(source, cursor, mask)
            group = read_group(source, cursor, mask)
            if group is None:
                ok = False
                break
            inner_start, inner_end, cursor = group
            args.append((inner_start, inner_end))

        if ok:
            calls.append(MacroCall(name=name, start=idx, end=cursor, args=tuple(args)))
    return calls


def find_environment(
    source: str,
    marker_start: str,
    marker_end: str,
    mask: bytearray | None = None,
) -> list[tuple[int, int]]:
    """Find regions delimited by two zero-argument marker macros.

    Jake's template brackets its lists with `\\resumeItemListStart` and
    `\\resumeItemListEnd` rather than a real environment, so this pairs them
    by position. Returns `(content_start, content_end)` spans.
    """
    if mask is None:
        mask = comment_mask(source)

    def positions(token: str) -> list[int]:
        found, start = [], 0
        while True:
            idx = source.find(token, start)
            if idx == -1:
                return found
            start = idx + len(token)
            after = idx + len(token)
            if mask[idx] or (after < len(source) and source[after] in LETTERS):
                continue
            found.append(idx)

    starts = positions("\\" + marker_start)
    ends = positions("\\" + marker_end)

    spans: list[tuple[int, int]] = []
    for s in starts:
        following = [e for e in ends if e > s]
        if following:
            spans.append((s + len(marker_start) + 1, following[0]))
    return spans
