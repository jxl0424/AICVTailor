"""LaTeX compilation.

Detects an engine at startup and degrades to no-PDF rather than failing: the
tailored .tex is still the deliverable, and a machine without TeX should still
be able to use the rest of the system.

The important behaviour is the compile gate in `compile_with_gate`. A tailored
file that does not compile is a bug, so a broken edit is reverted individually
and named, rather than failing the whole run.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from ..latex.regenerate import Edit, regenerate

log = logging.getLogger(__name__)

# tectonic first: it needs no TeX installation and fetches what it needs.
ENGINES = ("tectonic", "latexmk", "pdflatex")
TIMEOUT_SECONDS = 120

# `! LaTeX Error: ...` and plain `! Undefined control sequence.`
_ERROR_RE = re.compile(r"^! (.+)$", re.M)
_LINE_RE = re.compile(r"^l\.(\d+)", re.M)


@dataclass
class CompileResult:
    ok: bool
    engine: str = ""
    pdf_bytes: bytes | None = None
    pages: int = 0
    error: str = ""
    log_tail: str = ""
    skipped: bool = False

    @property
    def detail(self) -> str:
        if self.skipped:
            return "No LaTeX engine available; PDF generation skipped."
        return "compiled" if self.ok else self.error


def detect_engine() -> str | None:
    """The engine that will actually be used, honouring LATEX_ENGINE."""
    configured = (get_settings().latex_engine or "auto").strip().lower()
    if configured == "none":
        return None
    candidates = ENGINES if configured == "auto" else (configured,)
    return next((e for e in candidates if shutil.which(e)), None)


def _command(engine: str, source: Path, outdir: Path) -> list[str]:
    if engine == "tectonic":
        return [engine, "--outdir", str(outdir), "--keep-logs", str(source)]
    if engine == "latexmk":
        return [
            engine,
            "-pdf",
            "-interaction=nonstopmode",
            f"-outdir={outdir}",
            str(source),
        ]
    return [
        engine,
        "-interaction=nonstopmode",
        "-halt-on-error",
        f"-output-directory={outdir}",
        str(source),
    ]


def _first_error(log_text: str) -> str:
    """The first real error plus the source line, if TeX reported one.

    When TeX reports nothing recognisable the log itself is the only clue, so
    the tail is included rather than reporting an unactionable "no recognisable
    error message" -- which is what a MiKTeX install prompting for packages
    looks like from here.
    """
    errors = _ERROR_RE.findall(log_text)
    if errors:
        message = errors[0].strip()
        if line := _LINE_RE.search(log_text):
            return f"{message} (source line {line.group(1)})"
        return message

    tail = " ".join(log_text.split())[-300:]
    if tail:
        return f"compilation failed. End of the engine log: ...{tail}"
    return (
        "compilation failed and the engine produced no log at all. On MiKTeX "
        "this usually means it is waiting to install a missing package: open "
        "the MiKTeX Console and set package installation to 'Always'."
    )


def compile_tex(source: str, *, engine: str | None = None) -> CompileResult:
    """Compile LaTeX to a PDF in a temporary directory."""
    engine = engine or detect_engine()
    if engine is None:
        return CompileResult(ok=False, skipped=True)

    with tempfile.TemporaryDirectory(prefix="aicvtailor-") as tmp:
        workdir = Path(tmp)
        tex_path = workdir / "resume.tex"
        tex_path.write_text(source, encoding="utf-8")

        try:
            process = subprocess.run(
                _command(engine, tex_path, workdir),
                capture_output=True,
                text=True,
                timeout=TIMEOUT_SECONDS,
                cwd=workdir,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CompileResult(
                ok=False,
                engine=engine,
                error=f"{engine} timed out after {TIMEOUT_SECONDS}s",
            )
        except OSError as exc:
            return CompileResult(ok=False, engine=engine, error=f"{engine} failed to run: {exc}")

        log_path = workdir / "resume.log"
        log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
        combined = log_text or (process.stdout + process.stderr)

        pdf_path = workdir / "resume.pdf"
        if not pdf_path.exists():
            return CompileResult(
                ok=False,
                engine=engine,
                error=_first_error(combined),
                log_tail=combined[-2000:],
            )

        pdf_bytes = pdf_path.read_bytes()
        return CompileResult(
            ok=True,
            engine=engine,
            pdf_bytes=pdf_bytes,
            pages=_page_count(pdf_bytes),
            log_tail=combined[-1000:],
        )


def _page_count(pdf_bytes: bytes) -> int:
    try:
        import io

        from pypdf import PdfReader

        return len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    except Exception:  # noqa: BLE001 -- page count is informational
        return 0


@dataclass
class GatedCompile:
    """The result of compiling with edits, plus any that had to be reverted."""

    result: CompileResult
    applied: list[Edit] = field(default_factory=list)
    reverted: list[tuple[Edit, str]] = field(default_factory=list)
    tex: str = ""
    # Set when the master resume does not compile on its own. Every edit would
    # then look guilty, and none of them would be.
    baseline_error: str = ""

    @property
    def ok(self) -> bool:
        return self.result.ok or self.result.skipped


def compile_with_gate(source: str, edits: list[Edit]) -> GatedCompile:
    """Apply edits, and revert individually any that break the build.

    A tailored file that does not compile must never reach the user. Rather
    than failing the run, the offending edit is identified, dropped, and named
    so the bullet that broke it can be fixed by hand.

    With no engine available the edits are applied unchecked and the .tex is
    still produced, which is what the health check warns about.
    """
    engine = detect_engine()
    tex = regenerate(source, edits)

    if engine is None:
        return GatedCompile(
            result=CompileResult(ok=False, skipped=True), applied=list(edits), tex=tex
        )

    result = compile_tex(tex, engine=engine)
    if result.ok:
        return GatedCompile(result=result, applied=list(edits), tex=tex)

    # Before blaming any edit, check the document compiled to begin with.
    # Without this, a master that does not build makes every probe fail, and
    # every edit gets reverted for a breakage that was already there.
    baseline = compile_tex(source, engine=engine)
    if not baseline.ok:
        log.warning("the master resume does not compile on its own: %s", baseline.error)
        return GatedCompile(
            result=result,
            applied=list(edits),
            tex=tex,
            baseline_error=baseline.error,
        )

    log.warning("tailored .tex failed to compile (%s); isolating the bad edit", result.error)

    # Find the culprits by compiling each edit against the untouched source.
    applied: list[Edit] = []
    reverted: list[tuple[Edit, str]] = []
    for edit in edits:
        probe = compile_tex(regenerate(source, [edit]), engine=engine)
        if probe.ok:
            applied.append(edit)
        else:
            reverted.append((edit, probe.error))
            log.warning("reverting edit to %s: %s", edit.target_id, probe.error)

    tex = regenerate(source, applied)
    final = compile_tex(tex, engine=engine) if applied else compile_tex(source, engine=engine)

    if not final.ok and applied:
        # The edits are fine alone but not together. Ship the original rather
        # than guess which combination is at fault.
        reverted.extend((edit, "conflicts with another accepted edit") for edit in applied)
        applied = []
        tex = source
        final = compile_tex(source, engine=engine)

    return GatedCompile(result=final, applied=applied, reverted=reverted, tex=tex)
