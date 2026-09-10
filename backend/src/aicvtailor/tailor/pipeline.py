"""The tailoring run.

Apply accepted suggestions to the IR, regenerate by span replacement, gate on
compilation, diff, verify against the extracted PDF text, and persist.

Reordering of skills and bullets is deterministic code. The only model calls
happened earlier, in suggestion generation; nothing here asks a model anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..analysis.coverage import CREDIT
from ..analysis.match import MatchStatus
from ..config import get_guardrails
from ..guardrails import enforce_document
from ..latex.ir import Document
from ..latex.regenerate import Edit
from ..llm.runlog import RunLog
from . import diff as diff_mod
from .compile import GatedCompile, compile_tex, compile_with_gate
from .verify import Verification, verify

log = logging.getLogger(__name__)


@dataclass
class TailorResult:
    tex: str
    pdf_bytes: bytes | None
    compiled: bool
    compile_error: str
    baseline_error: str
    engine: str
    changes: list[diff_mod.ChangedSpan]
    reverted: list[dict[str, str]]
    verification: Verification
    coverage_before: float
    coverage_after: float
    document_guardrails: dict[str, Any]
    run_id: str
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "compiled": self.compiled,
            "compile_error": self.compile_error,
            "baseline_error": self.baseline_error,
            "engine": self.engine,
            "changes": [c.as_dict() for c in self.changes],
            "reverted": self.reverted,
            "verification": self.verification.as_dict(),
            "coverage_before": self.coverage_before,
            "coverage_after": self.coverage_after,
            "document_guardrails": self.document_guardrails,
            "warnings": self.warnings,
            "has_pdf": self.pdf_bytes is not None,
            "tex_chars": len(self.tex),
        }


def recompute_coverage(
    stored_terms: list[dict[str, Any]], tailored_text: str
) -> tuple[float, float]:
    """Coverage before and after, using the same credit scheme as the report.

    `after` re-checks each term against the tailored text rather than assuming
    an accepted suggestion worked. A rewrite that was reverted by the compile
    gate must not improve the number.
    """
    import re

    def present(term: str) -> bool:
        return (
            re.search(
                r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])",
                tailored_text,
                re.I,
            )
            is not None
        )

    total = before = after = 0.0
    for row in stored_terms:
        weight = float(row.get("weight", 0.0))
        if weight <= 0:
            continue
        total += weight
        before += weight * CREDIT[MatchStatus(row["status"])]

        if present(row["term"]):
            after += weight
        else:
            # No literal hit: keep whatever partial credit it already had.
            after += weight * CREDIT[MatchStatus(row["status"])]

    if not total:
        return 0.0, 0.0
    return round(100 * before / total, 1), round(100 * after / total, 1)


def tailor(
    document: Document,
    accepted: list[dict[str, Any]],
    *,
    stored_terms: list[dict[str, Any]] | None = None,
    rails: dict[str, Any] | None = None,
    runlog: RunLog | None = None,
) -> TailorResult:
    """Apply accepted suggestions and produce a verified tailored resume.

    `accepted` rows carry target_id, proposed_text, term and source_bullet_id.
    """
    rails = rails if rails is not None else get_guardrails()
    runlog = runlog or RunLog()
    warnings: list[str] = []

    editable = document.editable_spans()
    edits: list[Edit] = []
    intent: dict[str, tuple[str, list[str], str | None]] = {}

    for row in accepted:
        target_id = row.get("target_id")
        proposed = row.get("proposed_text")
        if not target_id or proposed is None:
            # A GAP has neither. It cannot reach here through the API, and if
            # it somehow does, it is dropped rather than applied.
            warnings.append(f"skipped '{row.get('term')}': nothing to apply")
            continue
        if target_id not in editable:
            warnings.append(
                f"skipped '{row.get('term')}': {target_id} is not an editable span"
            )
            continue

        if target_id in intent:
            # Two accepted suggestions writing the same span would produce
            # overlapping edits. Keep the first, which is the higher weighted,
            # and say what was dropped rather than failing the run.
            warnings.append(
                f"skipped '{row.get('term')}': another accepted suggestion already "
                f"rewrites {target_id}, and one span can only be written once."
            )
            continue

        span = editable[target_id]
        before = span.text(document.source)
        edits.append(document.edit(target_id, proposed))
        intent[target_id] = (before, [row.get("term", "")], row.get("source_bullet_id"))

    runlog.write("apply", accepted=len(accepted), edits=len(edits), skipped=len(warnings))

    baseline = compile_tex(document.source)
    gated: GatedCompile = compile_with_gate(document.source, edits)

    if gated.baseline_error:
        warnings.append(
            "Your master resume does not compile on its own, before any tailoring: "
            f"{gated.baseline_error} The edits were applied anyway and the .tex is "
            "downloadable, but no PDF could be produced. Fix the master first -- "
            "nothing here is caused by the tailoring."
        )

    reverted = [
        {"target_id": edit.target_id, "reason": reason} for edit, reason in gated.reverted
    ]
    for entry in reverted:
        warnings.append(
            f"reverted the edit to {entry['target_id']}: {entry['reason']}. "
            "The original bullet was kept so the file still compiles."
        )

    changes = diff_mod.build(
        document,
        [
            (
                edit.target_id,
                intent[edit.target_id][0],
                edit.new_text,
                intent[edit.target_id][1],
                intent[edit.target_id][2],
            )
            for edit in gated.applied
        ],
    )

    verification = verify(
        gated.result.pdf_bytes,
        target_terms=[
            term
            for edit in gated.applied
            for term in intent[edit.target_id][1]
            if term
        ],
        max_pages=rails.get("max_pages"),
        baseline_pages=baseline.pages,
        forbidden_claims=rails.get("forbidden_claims"),
    )
    warnings.extend(verification.notes)

    document_report = enforce_document(gated.tex, rails)
    if not document_report.ok:
        warnings.append(
            "The tailored .tex contains a forbidden claim: "
            + "; ".join(v.detail for v in document_report.violations)
        )

    coverage_before, coverage_after = (
        recompute_coverage(stored_terms, gated.tex) if stored_terms else (0.0, 0.0)
    )

    if gated.result.skipped:
        warnings.append(
            "No LaTeX engine is installed, so the .tex could not be compiled or "
            "verified. Download it and check it compiles before you send it."
        )

    runlog.write(
        "tailor",
        applied=len(gated.applied),
        reverted=len(reverted),
        compiled=gated.result.ok,
        pages=verification.pages,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
    )

    return TailorResult(
        tex=gated.tex,
        pdf_bytes=gated.result.pdf_bytes,
        compiled=gated.result.ok,
        compile_error=gated.result.error,
        baseline_error=gated.baseline_error,
        engine=gated.result.engine,
        changes=changes,
        reverted=reverted,
        verification=verification,
        coverage_before=coverage_before,
        coverage_after=coverage_after,
        document_guardrails=document_report.as_dict(),
        run_id=runlog.run_id,
        warnings=warnings,
    )
