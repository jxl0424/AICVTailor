# AICVTailor

Local-first resume tailoring. Paste a job description, see which of its terms
your CV already covers, get a tailored `.tex` that cannot fabricate, review a
traceable diff, download it, and track the application.

Runs entirely on your machine. Nothing leaves it except resume text and JD text
sent to whichever LLM provider you configure.

---

## Setup

### 1. Clone and add your CV

```bash
git clone https://github.com/jxl0424/AICVTailor.git
cd AICVTailor
mkdir -p data/master
cp /path/to/your/main.tex data/master/master.tex
```

`data/` is gitignored. Your CV and application history never get committed.

### 2. Install a LaTeX engine

Needed for PDF output and for the verification step that checks your terms
survive into the text a parser actually reads. Without one the app still runs
and still produces a tailored `.tex`, but says so in the health check.

| Platform | Command |
|---|---|
| macOS | `brew install tectonic` (smallest) or `brew install --cask mactex` |
| Debian/Ubuntu | `sudo apt-get install texlive-latex-base texlive-latex-extra texlive-fonts-recommended texlive-latex-recommended` |
| Any | [tectonic-typesetting.github.io](https://tectonic-typesetting.github.io) — single binary, fetches packages on demand |

Jake's Resume template needs `fullpage`, `titlesec`, `marvosym`, `enumitem`,
`fancyhdr`, `tabularx` and `glyphtounicode`. `texlive-latex-base` alone is not
enough; the list above is.

### 3. Get a free NVIDIA NIM key

Sign up at [build.nvidia.com](https://build.nvidia.com) and create an API key.
The free tier allows roughly 40 requests per minute; the app paces itself below
that. No card required.

```bash
cp .env.example .env
# then edit .env and set NVIDIA_API_KEY=nvapi-...
```

### 4. Optional: better semantic matching

```bash
pip install -e ".[embeddings]"
```

About 30MB, no PyTorch. Downloads the model once on first use. Without it,
matching is lexical only, and skills your CV covers in meaning but not in
words will read as missing.

### 5. Run

```bash
./run.sh
```

Creates the virtualenv, installs both halves, seeds the database, prints a
component check, starts the backend and frontend, and opens the browser.
Ctrl-C stops both.

**First run takes a couple of minutes** while pip and npm install. It prints
what it is doing, but there are still long pauses; that is normal.

**`run.sh` is a bash script.** It needs a POSIX shell:

| Where you are | What to use |
|---|---|
| macOS / Linux | any terminal, including PyCharm's Terminal tab |
| Windows | WSL, or Git Bash. **Not** PowerShell or `cmd` — `./run.sh` does nothing useful there |
| PyCharm | use the **Terminal** tab, not the green Run button. The Run button has no runner for shell scripts unless you configure one |

If you would rather not use the script at all, run the two halves yourself:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"     # Windows: .venv\Scripts\pip
.venv/bin/aicvtailor init-db
.venv/bin/uvicorn aicvtailor.main:app --port 8000 --reload &
cd frontend && npm install && npm run dev
```

Then open http://localhost:5173.

---

## Verifying the install

```bash
aicvtailor doctor            # component report: LaTeX, providers, embeddings, DB
aicvtailor models --refresh  # live model catalogue and which model each role resolved to
aicvtailor parse --check     # round-trip gate: proves the parser can edit your CV safely
make test                    # 443 tests, no network
```

`parse --check` is the important one. It parses your `master.tex`, rewrites
every editable span with its own text, and asserts the file comes back
byte-identical — plus that editing a single bullet moves only that bullet's
bytes. If that fails on your CV, stop and report it: everything downstream
depends on it.

---

## Using it

**Tailor** — paste a JD, pick your master, Analyse. You get a ranked term table
and a keyword coverage figure. Click any term to see the arithmetic behind its
weight. Then Generate suggestions, accept the ones you want (`j`/`k` to move,
`a` accept, `r` reject), and Tailor.

**Changes** — word-level diff grouped by section. Every change names the JD
terms it targeted and the source bullet it came from. Reject any single change
to regenerate without it.

**Library** — every version, filterable by company, role and date. Each row
links to its diff and downloads, can be reused as the starting point for a
similar role, or tracked as an application.

**Applications** — table or kanban, inline editing, CSV export. Anything live
and untouched for more than 14 days gets flagged.

---

## What it will not do

The tailoring step may reword, reorder, re-emphasise and re-scope what is
already true in your CV. It will not invent employers, dates, tools, metrics,
team sizes or outcomes.

This is enforced in code after the model returns, not by asking the model
nicely:

- **Entry headers have no editable span.** There is no id that addresses an
  employer, role, degree, institution or date, so no code path can rewrite one.
- **A term marked `missing` becomes a GAP**, which carries no target and no
  proposed text. There is nothing to apply. The API refuses to accept one.
- **New entities are rejected.** A figure, date, acronym or name in a rewrite
  that is not in the source bullet fails the generation, retries once with the
  violation quoted back, then fails hard.
- **The compiled PDF is checked**, not just the `.tex`, since that is the text
  that actually leaves your machine.

The adversarial test suite runs these checks with the guardrail text stripped
out of the prompt entirely, so a pass cannot be credited to the model behaving.

**"JD keyword coverage" is not an ATS score.** Real applicant tracking systems
are proprietary and vary by vendor. It measures one auditable thing: how much
of a posting's weighted terminology appears in your resume. The UI shows the
arithmetic.

---

## Configuration

| File | Holds |
|---|---|
| `.env` | Provider choice, API key, rate limit, ports. Gitignored. |
| `config/skills.yaml` | Canonical skill terms, synonyms, categories. Grows as you use it — the Tailor tab lists repeated phrases it did not recognise. |
| `config/guardrails.yaml` | `forbidden_claims`, `never_reword`, `max_bullet_length`, `max_pages`. |
| `config/guardrails.local.yaml` | Personal or NDA'd entries. Gitignored — put real `forbidden_claims` here, not in the committed file. |
| `config/models.yaml` | Per-role model preferences, resolved against the live catalogue. |

You do **not** need to list job titles, employers, degrees or dates in
`never_reword`. Those live in entry headers and are already unreachable.

---

## Providers

Set `LLM_PROVIDER` in `.env`:

- **`nim`** (default) — NVIDIA NIM, free tier. Model ids are resolved by prefix
  against the live `/v1/models` catalogue, so a stale preference degrades to a
  warning and a working fallback. Override per role with `EXTRACTOR_MODEL` and
  `REWRITER_MODEL`.
- **`ollama`** — fully offline. `ollama serve` plus `ollama pull llama3.1:8b`.
  Lower quality, and the provider is recorded on every tailored version.
- **`claude_cli`** — shells out to a local `claude` binary, behind
  `ENABLE_CLAUDE_CLI=true`. For A/B-ing a run against the hosted path.

Analysis and coverage are fully deterministic and need no provider at all.
Only bullet rewriting does.

---

## Troubleshooting

**Health says `unavailable`.** No usable provider. Set `NVIDIA_API_KEY`, or
start Ollama. Missing LaTeX only degrades to `.tex`-only; it is not fatal.

**Every suggestion is a GAP.** Either no provider is available (the reason is
in the suggestion text), or genuinely nothing in your CV supports those terms.
The second case is the system working.

**Port already in use.** An earlier `./run.sh` is still running. `run.sh`
checks both ports and says so.

**Model resolution warns about a fallback.** Expected. The preference lists are
hints; run `aicvtailor models --all` to see what is actually live, then pin one
with `EXTRACTOR_MODEL` / `REWRITER_MODEL`.
