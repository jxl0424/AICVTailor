"""Getting job description text in.

Paste is the reliable path. URL fetching is offered because it is convenient
when it works, but many boards render client-side or block non-browser
requests, so failure returns a clear "paste it instead" rather than retrying.

This is the one outbound request in the system that is not an LLM call. It
sends no personal data -- only a GET for a public posting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; AICVTailor/0.1; +local)"
MAX_BYTES = 2_000_000

# Boards known to render the posting client-side or to block automated GETs.
HOSTILE_HOSTS = (
    "linkedin.com",
    "indeed.com",
    "glassdoor.",
    "workday.com",
    "myworkdayjobs.com",
    "ziprecruiter.",
)


class IngestError(RuntimeError):
    """Fetching or extracting failed in a way the user must act on."""


@dataclass
class Ingested:
    text: str
    source_url: str | None = None
    method: str = "paste"
    warning: str = ""


def from_text(text: str) -> Ingested:
    cleaned = text.strip()
    if len(cleaned) < 40:
        raise IngestError(
            "That job description is too short to analyse. Paste the full posting."
        )
    return Ingested(text=cleaned, method="paste")


def from_url(url: str, *, client: httpx.Client | None = None) -> Ingested:
    hostile = next((h for h in HOSTILE_HOSTS if h in url.lower()), None)

    owns = client is None
    client = client or httpx.Client(
        timeout=15.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    try:
        response = client.get(url)
        response.raise_for_status()
        html = response.text[:MAX_BYTES]
    except Exception as exc:  # noqa: BLE001 -- one actionable message either way
        hint = (
            f" {hostile.rstrip('.')} renders postings in the browser and blocks "
            "automated requests."
            if hostile
            else ""
        )
        raise IngestError(
            f"Could not fetch that URL ({exc}).{hint} Copy the posting text and paste it instead."
        ) from exc
    finally:
        if owns:
            client.close()

    try:
        import trafilatura

        extracted = trafilatura.extract(html, include_comments=False, include_tables=True)
    except ImportError:
        extracted = None

    if not extracted or len(extracted.strip()) < 200:
        raise IngestError(
            "Fetched the page but could not find the posting text in it"
            f"{', which is normal for ' + hostile.rstrip('.') if hostile else ''}. "
            "Copy the posting and paste it instead."
        )

    warning = (
        f"Extracted from {hostile.rstrip('.')}, which often loses parts of the posting. "
        "Check the text below before relying on the analysis."
        if hostile
        else ""
    )
    return Ingested(
        text=extracted.strip(), source_url=url, method="url", warning=warning
    )
