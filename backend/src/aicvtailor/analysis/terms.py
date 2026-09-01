"""Term extraction and normalisation.

Two passes. The dictionary pass finds canonical skills and their synonyms from
config/skills.yaml, collapsing surface forms so "PyTorch", "torch" and
"Py Torch" become one term. The discovery pass finds candidate phrases the
dictionary does not know about -- those never reach the resume, but they are
shown so the dictionary can be grown, which is how skills.yaml is meant to
improve over time.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from ..config import get_skills
from .sections import OPTIONAL_CUE, REQUIRED_CUE, Section, SectionKind

# Words that cannot start or end a candidate noun phrase. Kept deliberately
# small: an aggressive stoplist silently deletes real multi-word skills.
_BOUNDARY_STOPWORDS = frozenset(
    """
    a an the and or but if then than that this those these of in on at to for with
    from by as is are was were be been being have has had do does did will would
    can could should may might must our your their its you we they it he she
    who whom which what when where why how all any both each few more most other
    some such no nor not only own same so too very s t just don now
    experience experience. work working knowledge understanding ability strong
    excellent good great years year plus using use used across within into
    """.split()
)

# Ordinary English that appears in every posting. A discovered phrase made
# only of these is noise, not a skill the dictionary is missing.
_COMMON_WORDS = frozenset(
    """
    system systems team teams role roles product products project projects
    build building built design designing designed ship shipping develop
    developing development engineer engineers engineering company companies
    business customer customers user users client clients data time times
    day days week weeks month months year years people person new small large
    high low best better good great strong end ends including include includes
    across within into over under about like well also make makes making
    help helps helping need needs needed want wants looking look world real
    hand hands mind minds part parts way ways thing things lot lots
    opportunity opportunities environment environments culture office offices
    salary holiday budget round backed profitable applications application
    apply send short note welcome backgrounds sectors sector talk
    """.split()
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+#./\-']*")
# Things that look technical regardless of the dictionary: CamelCase, dotted
# names, C++, CI/CD, versioned tools.
_TECHY_RE = re.compile(
    r"\b(?:[A-Za-z]+\+\+|[A-Za-z]+#|[A-Z][a-z]+(?:[A-Z][a-z]+)+|"
    r"[A-Za-z]+\.[a-z]{2,4}|[A-Z]{2,}(?:/[A-Z]{2,})+|[A-Z]{2,})\b"
)


@dataclass(frozen=True, slots=True)
class Mention:
    surface: str
    section: SectionKind
    required: bool | None  # None when no inline cue applied
    start: int


@dataclass
class Term:
    canonical: str
    category: str
    in_dictionary: bool = True
    mentions: list[Mention] = field(default_factory=list)

    @property
    def frequency(self) -> int:
        return len(self.mentions)

    @property
    def sections(self) -> set[SectionKind]:
        return {m.section for m in self.mentions}

    @property
    def surfaces(self) -> set[str]:
        return {m.surface.lower() for m in self.mentions}


class SkillDictionary:
    """Canonical terms, their synonyms, and the stoplist, from skills.yaml."""

    def __init__(self, config: dict | None = None) -> None:
        config = config if config is not None else get_skills()
        self.categories: dict[str, str] = {}
        self.canonical_of: dict[str, str] = {}
        self.synonyms_of: dict[str, list[str]] = {}

        for entry in config.get("terms", []):
            canonical = entry["canonical"]
            self.categories[canonical] = entry.get("category", "hard_skill")
            forms = [canonical, *entry.get("synonyms", [])]
            self.synonyms_of[canonical] = list(entry.get("synonyms", []))
            for form in forms:
                self.canonical_of[form.lower()] = canonical

        self.stoplist = {s.lower() for s in config.get("stoplist", [])}

        # One alternation, longest first, so "LLM evaluation" wins over "LLM".
        forms = sorted(self.canonical_of, key=len, reverse=True)
        self._pattern = (
            re.compile(
                r"(?<![A-Za-z0-9])(" + "|".join(re.escape(f) for f in forms) + r")(?![A-Za-z0-9])",
                re.I,
            )
            if forms
            else None
        )

    def find(self, text: str):
        """Yield `(canonical, surface, start)` for every dictionary hit."""
        if self._pattern is None:
            return
        for match in self._pattern.finditer(text):
            surface = match.group(1)
            yield self.canonical_of[surface.lower()], surface, match.start()

    def normalise(self, phrase: str) -> str | None:
        return self.canonical_of.get(phrase.lower().strip())


def _cue_for(text: str, position: int) -> bool | None:
    """Whether the line around `position` marks its terms required or optional."""
    line_start = text.rfind("\n", 0, position) + 1
    line_end = text.find("\n", position)
    line = text[line_start : line_end if line_end != -1 else len(text)]

    if OPTIONAL_CUE.search(line):
        return False
    if REQUIRED_CUE.search(line):
        return True
    return None


def extract_dictionary_terms(
    sections: list[Section], dictionary: SkillDictionary
) -> dict[str, Term]:
    terms: dict[str, Term] = {}
    for section in sections:
        for canonical, surface, offset in dictionary.find(section.text):
            term = terms.setdefault(
                canonical,
                Term(canonical=canonical, category=dictionary.categories[canonical]),
            )
            term.mentions.append(
                Mention(
                    surface=surface,
                    section=section.kind,
                    required=_cue_for(section.text, offset),
                    start=section.start + offset,
                )
            )
    return terms


def _candidate_phrases(text: str) -> list[tuple[str, int]]:
    """Stopword-bounded runs of 1-3 tokens, plus anything that looks technical.

    A dependency-free stand-in for noun-phrase chunking. It over-generates,
    which is fine: these are only ever shown as dictionary suggestions, never
    written into a resume.
    """
    found: list[tuple[str, int]] = []

    for match in _TECHY_RE.finditer(text):
        found.append((match.group(), match.start()))

    tokens = [(m.group(), m.start()) for m in _TOKEN_RE.finditer(text)]
    run: list[tuple[str, int]] = []

    def flush() -> None:
        for size in (3, 2):
            for i in range(len(run) - size + 1):
                window = run[i : i + size]
                found.append((" ".join(w for w, _ in window), window[0][1]))
        for word, at in run:
            if len(word) > 2:
                found.append((word, at))
        run.clear()

    for word, at in tokens:
        if word.lower() in _BOUNDARY_STOPWORDS or word.isdigit():
            flush()
        else:
            run.append((word, at))
    flush()
    return found


def _looks_like_a_skill(surface: str, lowered: str) -> bool:
    """Filter discovery down to things worth adding to the dictionary.

    Either it looks technical (CamelCase, C++, CI/CD, dotted names), or it is a
    phrase carrying at least one word that is not generic posting filler.
    Without this the suggestions are dominated by "systems", "days" and
    "including", which buries the two or three real misses.
    """
    if _TECHY_RE.fullmatch(surface):
        return True

    words = lowered.split()
    informative = [w for w in words if w not in _COMMON_WORDS and len(w) > 2]
    if not informative:
        return False
    # A lone lowercase common-looking word is rarely a skill worth adding;
    # multi-word phrases and capitalised names are.
    if len(words) == 1:
        return surface[:1].isupper() or not surface.isalpha() or len(surface) <= 12
    return True


def discover_unknown_terms(
    sections: list[Section],
    dictionary: SkillDictionary,
    *,
    min_frequency: int = 2,
    limit: int = 25,
) -> list[Term]:
    """Frequent phrases the dictionary has no entry for.

    Surfaced as "terms your skills.yaml does not know", so the dictionary can
    be grown from real postings rather than guessed at up front.
    """
    counts: dict[str, list[Mention]] = defaultdict(list)

    for section in sections:
        known_spans = [
            (offset, offset + len(surface))
            for _, surface, offset in dictionary.find(section.text)
        ]
        for phrase, offset in _candidate_phrases(section.text):
            lowered = phrase.lower().strip(" .,:;")
            if (
                len(lowered) < 3
                or lowered in dictionary.canonical_of
                or lowered in dictionary.stoplist
                or any(lo <= offset < hi for lo, hi in known_spans)
                or not _looks_like_a_skill(phrase, lowered)
            ):
                continue
            counts[lowered].append(
                Mention(
                    surface=phrase,
                    section=section.kind,
                    required=_cue_for(section.text, offset),
                    start=section.start + offset,
                )
            )

    discovered = [
        Term(canonical=phrase, category="unknown", in_dictionary=False, mentions=mentions)
        for phrase, mentions in counts.items()
        if len(mentions) >= min_frequency
    ]
    discovered.sort(key=lambda t: (-t.frequency, t.canonical))

    # Drop phrases wholly contained in a longer, equally frequent phrase.
    kept: list[Term] = []
    for term in discovered:
        if any(
            term.canonical in other.canonical and other.frequency >= term.frequency
            for other in kept
        ):
            continue
        kept.append(term)
    return kept[:limit]
