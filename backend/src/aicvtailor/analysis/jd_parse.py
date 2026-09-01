"""Deterministic job-description parsing.

Regex first, one model call only for what regex could not find. Most postings
state the role and location plainly, and a deterministic answer is faster,
free, reproducible and debuggable.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

SENIORITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("intern", re.compile(r"\b(intern|internship|placement year)\b", re.I)),
    ("graduate", re.compile(r"\b(graduate|entry[- ]level|junior|new grad)\b", re.I)),
    ("principal", re.compile(r"\b(principal|distinguished|fellow)\b", re.I)),
    ("staff", re.compile(r"\b(staff engineer|staff scientist)\b", re.I)),
    ("lead", re.compile(r"\b(lead|head of|manager|director)\b", re.I)),
    ("senior", re.compile(r"\b(senior|snr|sr\.?)\b", re.I)),
    ("mid", re.compile(r"\b(mid[- ]level|intermediate)\b", re.I)),
)

WORKPLACE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hybrid", re.compile(r"\bhybrid\b", re.I)),
    ("remote", re.compile(r"\b(fully )?remote\b|\bwork from home\b|\bwfh\b", re.I)),
    ("onsite", re.compile(r"\b(on[- ]site|in[- ]office|in person)\b", re.I)),
)

VISA_PATTERN = re.compile(
    r"\b(visa sponsorship|sponsorship|right to work|work authorisation|"
    r"work authorization|eligible to work|no sponsorship|cannot sponsor|"
    r"unable to sponsor|tier 2|skilled worker)\b",
    re.I,
)
CLEARANCE_PATTERN = re.compile(
    r"\b(security clearance|sc cleared|dv cleared|bpss|clearance required|"
    r"must be clearable|developed vetting)\b",
    re.I,
)

_ROLE_LABEL = re.compile(
    r"^\s*(?:job\s*title|position|role|title)\s*[:\-]\s*(.+)$", re.I | re.M
)
_COMPANY_LABEL = re.compile(
    r"^\s*(?:company|employer|organisation|organization)\s*[:\-]\s*(.+)$", re.I | re.M
)
_LOCATION_LABEL = re.compile(r"^\s*(?:location|based in|office)\s*[:\-]\s*(.+)$", re.I | re.M)
_AT_COMPANY = re.compile(r"\bat\s+([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})")


class JDFields(BaseModel):
    """Schema for the one extractor call that fills regex's gaps."""

    company: str | None = Field(default=None, description="Hiring company name")
    role: str | None = Field(default=None, description="Job title as advertised")
    location: str | None = Field(default=None, description="Location, or null")


@dataclass
class ParsedJD:
    company: str | None = None
    role: str | None = None
    location: str | None = None
    seniority: str | None = None
    workplace: str | None = None  # remote | hybrid | onsite
    visa_mentioned: bool = False
    visa_context: str = ""
    clearance_required: bool = False
    clearance_context: str = ""
    resolved_by: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _clean_role(role: str, company: str | None) -> str:
    """Strip the company and boilerplate off a headline used as a job title."""
    role = re.sub(r"\s*[-|\u2013\u2014]\s*(full|part)[- ]time\b.*$", "", role, flags=re.I)
    role = re.split(r"\s+\bat\b\s+", role, maxsplit=1)[0]
    if company:
        role = re.sub(rf"\s*[-|@,]?\s*{re.escape(company)}\s*$", "", role, flags=re.I)
    return role.strip(" -|,\u2013\u2014")


def _clean_location(location: str) -> str:
    """Keep the place, drop the working-pattern commentary after it."""
    location = re.split(r"\s+[-\u2013\u2014(]\s*", location, maxsplit=1)[0]
    return location.strip(" -,\u2013\u2014()")


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _context(text: str, pattern: re.Pattern[str]) -> str:
    """The sentence a flag was found in, so the UI can show why."""
    match = pattern.search(text)
    if not match:
        return ""
    start = max(0, text.rfind(".", 0, match.start()) + 1)
    end = text.find(".", match.end())
    return text[start : end if end != -1 else len(text)].strip()[:240]


def parse_regex(text: str) -> ParsedJD:
    """Everything obtainable without a model call."""
    parsed = ParsedJD()

    if m := _ROLE_LABEL.search(text):
        parsed.role, parsed.resolved_by["role"] = m.group(1).strip(), "regex:label"
    if m := _COMPANY_LABEL.search(text):
        parsed.company, parsed.resolved_by["company"] = m.group(1).strip(), "regex:label"
    if m := _LOCATION_LABEL.search(text):
        parsed.location = _clean_location(m.group(1).strip())
        parsed.resolved_by["location"] = "regex:label"

    headline = _first_line(text)
    if parsed.role is None and headline and len(headline) < 90:
        parsed.role, parsed.resolved_by["role"] = headline, "regex:first-line"
    if parsed.company is None and (m := _AT_COMPANY.search(headline)):
        parsed.company, parsed.resolved_by["company"] = m.group(1).strip(), "regex:at-company"

    if parsed.role:
        parsed.role = _clean_role(parsed.role, parsed.company)

    for label, pattern in SENIORITY_PATTERNS:
        if pattern.search(parsed.role or "") or pattern.search(headline):
            parsed.seniority, parsed.resolved_by["seniority"] = label, "regex"
            break

    for label, pattern in WORKPLACE_PATTERNS:
        if pattern.search(text):
            parsed.workplace, parsed.resolved_by["workplace"] = label, "regex"
            break

    if VISA_PATTERN.search(text):
        parsed.visa_mentioned = True
        parsed.visa_context = _context(text, VISA_PATTERN)
    if CLEARANCE_PATTERN.search(text):
        parsed.clearance_required = True
        parsed.clearance_context = _context(text, CLEARANCE_PATTERN)

    return parsed


def parse(text: str, provider=None, *, runlog=None) -> ParsedJD:
    """Regex, then one extractor call for whatever is still unknown.

    If the model call fails the regex result stands. A missing company name is
    a cosmetic problem; refusing to analyse the posting over it is not.
    """
    parsed = parse_regex(text)

    gaps = [f for f in ("company", "role", "location") if getattr(parsed, f) in (None, "")]
    weak = [f for f in ("role",) if parsed.resolved_by.get(f) == "regex:first-line"]
    wanted = gaps + [f for f in weak if f not in gaps]

    if not wanted or provider is None:
        return parsed

    from ..llm.base import Role

    try:
        result = provider.complete(
            system=(
                "You extract factual fields from job descriptions. Return only what "
                "the text states. Use null for anything absent. Do not guess."
            ),
            user=f"Extract the requested fields.\n\n---\n{text[:6000]}",
            schema=JDFields,
            role=Role.EXTRACTOR,
        )
    except Exception as exc:  # noqa: BLE001 -- regex result still stands
        log.warning("JD field extraction call failed (%s); keeping regex result", exc)
        return parsed

    for field_name in wanted:
        value = (result or {}).get(field_name)
        if value:
            setattr(parsed, field_name, str(value).strip())
            parsed.resolved_by[field_name] = "model"

    return parsed
