"""Post-compile verification.

The .tex is not what an employer's parser reads -- the extracted PDF text is.
A term can make it into the source and still be lost in extraction, so this
stage pulls the text back out and checks the terms actually survived.
"""

from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass, field


@dataclass
class Verification:
    ok: bool
    pages: int = 0
    max_pages: int | None = None
    page_limit_ok: bool = True
    grew: bool = False
    extracted_chars: int = 0
    surviving_terms: list[str] = field(default_factory=list)
    lost_terms: list[str] = field(default_factory=list)
    forbidden_hits: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    skipped: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def extract_text(pdf_bytes: bytes) -> str:
    """Text as a parser would read it, whitespace normalised.

    Extraction breaks lines wherever the layout does, so a term that spans a
    line break would otherwise read as missing. Collapsing whitespace is what
    makes the term check meaningful rather than a test of typesetting.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        raw = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:  # noqa: BLE001 -- a broken PDF is reported, not raised
        return ""
    return re.sub(r"\s+", " ", raw)


def _contains(haystack: str, phrase: str) -> bool:
    return (
        re.search(
            r"(?<![A-Za-z0-9])" + re.escape(" ".join(phrase.split())) + r"(?![A-Za-z0-9])",
            haystack,
            re.I,
        )
        is not None
    )


def verify(
    pdf_bytes: bytes | None,
    *,
    target_terms: list[str] | None = None,
    max_pages: int | None = None,
    baseline_pages: int = 0,
    forbidden_claims: list[str] | None = None,
) -> Verification:
    """Check the compiled PDF against what the tailoring claimed to do."""
    if not pdf_bytes:
        return Verification(
            ok=True,
            skipped=True,
            notes=["No PDF was produced, so extraction checks were skipped."],
        )

    text = extract_text(pdf_bytes)
    pages = 0
    try:
        from pypdf import PdfReader

        pages = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:  # noqa: BLE001
        pass

    surviving: list[str] = []
    lost: list[str] = []
    for term in target_terms or []:
        (surviving if _contains(text, term) else lost).append(term)

    # Forbidden claims are re-checked here because this is the text that
    # actually leaves the machine.
    forbidden_hits = [
        claim
        for claim in (forbidden_claims or [])
        if not str(claim).startswith("re:") and _contains(text, str(claim))
    ]

    page_limit_ok = max_pages is None or pages <= max_pages
    grew = bool(baseline_pages) and pages > baseline_pages

    notes: list[str] = []
    if lost:
        notes.append(
            f"{len(lost)} term(s) are in the .tex but not in the extracted PDF text: "
            + ", ".join(lost)
            + ". A parser reads the extracted text, so these will not count."
        )
    if not page_limit_ok:
        notes.append(f"The PDF is {pages} pages, over the configured limit of {max_pages}.")
    if grew:
        notes.append(f"The PDF grew from {baseline_pages} to {pages} pages.")
    if forbidden_hits:
        notes.append(
            "Forbidden claims found in the final PDF text: " + ", ".join(forbidden_hits)
        )

    return Verification(
        ok=not lost and page_limit_ok and not grew and not forbidden_hits,
        pages=pages,
        max_pages=max_pages,
        page_limit_ok=page_limit_ok,
        grew=grew,
        extracted_chars=len(text),
        surviving_terms=surviving,
        lost_terms=lost,
        forbidden_hits=forbidden_hits,
        notes=notes,
    )
