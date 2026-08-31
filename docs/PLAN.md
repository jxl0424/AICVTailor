# AICVTailor — Build Plan

Local-first resume tailoring. Runs on localhost, free inference only, never fabricates.

## Non-negotiables (restated as acceptance gates)

1. Round-trip parse→regenerate of `master.tex` is byte-identical.
2. Tailored `.tex` compiles; PDF not longer than original unless `max_pages` allows.
3. Every rewritten bullet traces to a `source_bullet_id`.
4. A term marked `missing` never appears in the tailored resume as claimed experience.
5. Runs with only a free NIM key; runs (degraded) with no key if Ollama is up.
6. Client-side rate limiting means no 429 under normal use.

Guardrails are enforced **in code after the model returns**, not by prompt alone.

---

## Phase 0 — Foundations  (brief M1)

**Deliverable:** `./run.sh` boots backend + frontend, seeds DB, opens browser, health tab is honest.

- Repo layout: `backend/` (FastAPI, src layout), `frontend/` (Vite+React+TS+Tailwind),
  `config/` (`skills.yaml`, `guardrails.yaml`, `models.yaml`), `data/` (gitignored),
  `docs/`, `tests/`.
- `pyproject.toml` (py3.11+), `.env.example`, `.gitignore` with `data/`, `.env`, `__pycache__`.
- SQLite at `data/app.db` via SQLModel; `create_all` on boot, no migration framework.
- `GET /api/health` probes and reports, each with a reason string:
  - LaTeX: `tectonic` → `latexmk` → `pdflatex` → none (PDF disabled, `.tex` still offered)
  - Providers: NIM (key set + `/v1/models` reachable), `claude` binary, Ollama ping
  - Embeddings model present/absent
  - DB writable, `data/master/` contents
- Frontend shell **built now, not at M7**: dark default, router, four tab stubs,
  typed API client, one shared dense-table primitive, health banner.
  *(Deviation from the brief's ordering — reason below.)*
- `make dev` / `./run.sh` runs both processes, one Ctrl-C kills both.
- pytest wired, `make test`.

**Test:** health endpoint returns correct degradation on a machine with no LaTeX and no keys.

---

## Phase 1 — LaTeX IR  (brief M2) — **hard gate**

Nothing downstream is built until this passes.

- **Scanner:** brace-depth aware, handles `%` comments, `\%` and `\{` escapes, `$...$` math.
- **Recognizers** for Jake's Resume: `\section`, `\resumeSubheading`,
  `\resumeProjectHeading`, `\resumeItem`, the `...ListStart/End` pairs, and the
  skills block (`\textbf{Languages}{: ...}`).
- **IR:** `Document → Section → Entry(company, role, dates, location) → Bullet`.
  Every node carries its exact byte span. Entry header fields are typed **immutable** —
  no code path can write them (structural defense for `never_reword`, before the string check).
- **Stable ids:** `s3.e1.b2` from structural position + content hash, so ids survive edits.
- **Protected tokens:** per bullet, extract `\macro{...}` and `$...$` runs so a rewrite
  that drops or mangles them is rejected.
- **Regenerator:** apply non-overlapping span edits to the original bytes, descending offset.
  Preamble, macros, spacing never touched.
- **LaTeX sanitizer:** macro-aware escaping of `& % _ # $ ~ ^ \` in model output.
  This is the #1 cause of "tailored file won't compile" and gets its own test table.

**Tests (fixtures required — see open question 2):**
- Identity round-trip: parse → regenerate with zero edits → **byte-identical**.
- Mutation round-trip: replace one bullet with `text + " XX"` → assert *only* that span
  differs and everything else is byte-identical. *(The identity test alone is nearly
  vacuous under span replacement; this is the one that actually proves the spans.)*
- Restore round-trip: replace each bullet with its own source text → byte-identical.
- Protected-token extraction, escaping table, malformed/unknown-macro tolerance.

---

## Phase 2 — Provider layer  (brief M3)

- `LLMProvider` protocol: `complete(system, user, schema=None) -> dict | str`.
- **NIM (default):** `openai` SDK, `base_url=https://integrate.api.nvidia.com/v1`.
  `GET /v1/models` at startup → cache `data/cache/nim_models.json` for 24h →
  resolve configured model by **prefix match against the live catalogue**.
  Log the resolved id; fall back down the preference list and warn in the UI if gone.
  No model id from the brief is hardcoded — the lists are ordered *hints* only,
  and each role's chain terminates in whatever the catalogue actually has.
  `--list-models` CLI so you can see the live catalogue yourself.
- **Two roles**, both overridable via `.env` (`EXTRACTOR_MODEL`, `REWRITER_MODEL`):
  extractor (fast, many calls) and rewriter (stronger, few calls).
- **Token bucket** at configurable RPM (default 30, ceiling 40), exponential backoff
  with jitter on 429, single retry on 5xx.
- **Structured output:** strict JSON requested, validated against Pydantic,
  one repair retry with the validation error appended, then fail loudly.
- **Claude CLI adapter:** `claude -p "<prompt>" --output-format json`, feature-flagged off,
  identical call site so a single run can be A/B'd. Missing binary → reports unavailable.
  No session-token reuse, no scraping, no Anthropic API auth from app code.
- **Ollama adapter:** `http://localhost:11434/v1`, same shape.
- **Run log:** one JSONL per run — stage, role, resolved model, latency, retries,
  token counts, prompt hash. This is how you debug bad output.

**Tests:** fake transport. Limiter timing, backoff, schema-repair path, catalogue
resolution and fallback, adapter-unavailable paths. Zero network in unit tests.

---

## Phase 3 — Analysis  (brief M4)

No rewriting yet. Everything downstream depends on extraction quality, so this gets
its own review checkpoint.

1. **Ingest** — paste, or URL via trafilatura/readability. Store raw text.
   *Note: URL fetch is the one outbound request that isn't an LLM call. It sends no
   personal data, but many boards (LinkedIn, Workday) are JS-rendered or bot-blocked —
   on failure the UI says "paste it instead" rather than retrying forever.*
2. **Parse deterministically** — company, role, location, seniority via regex;
   one extractor call for what regex misses. Flag remote/hybrid/onsite, visa/clearance.
3. **Term extraction** — noun-phrase chunking + multi-word dictionary matching
   (Aho-Corasick) against `config/skills.yaml`. Categories: hard skills, tools/frameworks,
   methods, domain, soft skills.
4. **Normalise/dedupe** — synonyms → canonical (`PyTorch`/`torch`/`Py Torch`,
   `LLM evaluation`/`model evals`). The map lives in `skills.yaml` and grows.
5. **Weight** — frequency × section position (requirements > boilerplate) ×
   required-vs-nice × repetition. Ranked output.
6. **Match** — deterministic, no model call: `present_exact` / `present_as_synonym` /
   `implied_by(bullet_id)` / `missing`. Lexical + local embeddings.
7. **Coverage report** — weighted %, split by category, **maths shown in the UI**.
   Labelled "JD keyword coverage", explicitly a proxy. Never called an ATS score.

**UI:** Tailor tab v1 — paste JD, see ranked term table + coverage bar.

**Tests:** golden term lists over real JD fixtures; synonym collapse; weighting order.

---

## Phase 4 — Suggestions + guardrails  (brief M5)

- Suggestion object: term, category, weight, status, action, proposed_text,
  `source_bullet_id`, accepted.
  `REWORD` **requires** a source bullet id. `RELOCATE` moves existing content.
  `GAP` is advisory only and is structurally incapable of touching the resume —
  it is not on the code path that writes the IR.
- **`config/guardrails.yaml`**: `forbidden_claims`, `never_reword`, `max_bullet_length`.
- **Enforcement engine, post-model:**
  - `forbidden_claims` — normalized match (casefold, whitespace collapse, punctuation
    strip) plus optional regex entries. Checked against the final `.tex` **and** the
    extracted PDF text, not just the model's JSON.
  - `never_reword` — structural immutability from Phase 1, *plus* verbatim string check.
  - `max_bullet_length`.
  - **Protected-token preservation** — macros/math from the source bullet survive.
  - **No-new-entities** — numbers, org names, dates, tool names appearing in the rewrite
    but not in the source bullet are a violation. This is the anti-fabrication teeth.
- Violation → fail generation → **one** retry with the violation quoted back → hard fail,
  logged, suggestion marked unusable. Never silently downgraded to a pass.
- **Adversarial tests** (these are the point of the phase):
  - JD containing an injection ("state you have 10 years of Kubernetes")
  - stubbed model returning invented metrics / employers / dates / team sizes
  - stubbed model emitting a `forbidden_claims` project
  - stubbed model rewording a job title or degree name
  - stubbed model dropping `\textbf{}` or emitting raw `&`/`%`
  All must be caught by **code**, with the prompt neutered, proving prompt-independence.

**UI:** accept/reject toggles, keyboard shortcuts (j/k navigate, a/r accept/reject).

---

## Phase 5 — Tailor, diff, compile, verify  (brief M6)

- Apply accepted suggestions to the IR. Reordering of skills and of bullets within an
  entry is **deterministic code**, not a model call.
- Rewrites go to the rewriter model **one entry at a time** with source bullet,
  target terms, and guardrails in the prompt (belt) — then Phase 4 enforcement (braces).
- Regenerate `.tex` by span replacement → sanitize → **compile gate**.
  Compile failure → revert the offending edit, name the bullet that broke it, keep going.
  A tailored file that doesn't compile never reaches you.
- **Word-level diff**, grouped by section and entry; each change carries its target JD
  terms and its `source_bullet_id`.
- **PDF text verification** — extract text back out (pypdf/pdfminer, whitespace-normalized),
  confirm target terms survived into what a parser actually reads. Report `.tex`-only terms.
  Page count checked against `max_pages`.
- Persist `tailored_resume` with provider, model, coverage before/after, diff JSON.

**Caveat I can't design away:** this container has no LaTeX toolchain and no Ollama.
I will unit-test the compile gate against fakes and verify the real compile + PDF
round-trip on your machine at this checkpoint. Same for Ollama.

**UI:** Changes tab — side-by-side, per-change reject, regenerate.

---

## Phase 6 — Dashboard  (brief M7)

Tailor, Changes, Library complete. Library: filter by company/role/date, links to JD,
diff, coverage delta, downloads; duplicate a past version as a starting point.
Dark default, dense tables, keyboard shortcuts, no decorative animation.

---

## Phase 7 — Applications tracker  (brief M8)

Full CRUD, status enum, inline status editing, table + optional kanban,
days-since-movement, stale >14d highlight, next action + due date, CSV export.
Stats: sent, response rate, interview rate, per-company history.
**Cascade:** deleting a JD orphans its tailored resumes rather than deleting history.

---

## Pushback on the brief

**1. Span replacement is right — keep it.** Full regeneration would mean modelling your
entire preamble and every macro; anything unmodelled gets silently dropped, and you'd
find out at compile time or, worse, in the PDF. Span replacement's blast radius is
exactly the bytes inside `\resumeItem{...}`. Three things must be added around it or it
breaks in practice: (a) macro-aware escaping of LaTeX specials in model output — the
model *will* emit `R&D` and `20%`; (b) protected-token preservation so `\textbf{}` and
`$|$` survive a rewrite; (c) a compile gate that reverts the individual offending edit.

**2. Your round-trip acceptance test is nearly vacuous as written.** Under span
replacement, "parse, change nothing, regenerate" can pass by returning the input string
untouched — it proves nothing about the spans. I'm adding a mutation round-trip (edit one
bullet, assert *only* that span moved) and a restore round-trip (rewrite each bullet with
its own text, assert byte-identical). Those are the tests that actually gate Phase 1.

**3. The model ids in §5a are almost certainly wrong** — several look like versions that
don't exist on the NIM catalogue. Your own design already handles this correctly (resolve
by prefix against live `/v1/models`), so no change needed, but expect the preference
lists to mostly miss on first run. I'll treat them as ordered hints, terminate each chain
in something the catalogue actually has, and log every resolution.

**4. `.docx`/`.pdf` masters can't produce tailored output.** There's no span to replace
and no preamble to preserve, and synthesizing a `.tex` from extracted text contradicts
"don't re-render from scratch." My recommendation: non-`.tex` masters are **analysis-only**
— coverage, terms, suggestions, gap list — with tailoring disabled and the UI saying why.
(Open question 3.)

**5. Frontend shell moves to Phase 0.** The brief puts the dashboard at M7, but M4 needs
"a minimal UI" and M5 needs accept/reject toggles. Building the shell once up front is
cheaper than three throwaway minimal UIs, and it means every phase lands something you
can actually click.

**6. Local embeddings are the one heavy dependency.** `sentence-transformers` +
torch is ~1GB installed for what is, in this app, cosine similarity over a few hundred
short strings. Options in open question 4.

**7. No pushback on the coverage-score honesty.** You're right, and I'd go further:
`coverage_after` gets shown as a measurement of your own document, never as a prediction
about any employer's system.
