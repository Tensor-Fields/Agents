# revview — full reference

Side-by-side viewer for reviewer comments. Every comment is linked to the passage it criticises
in the **commented** manuscript and to the corresponding passage in the **revised** one, and
both are highlighted on the rendered PDF pages.

```
revview build --commented v2.tex v2.pdf --revised v3.tex --comments feedback.md -o site
# open site/index.html
```

## Inputs

| Input | Accepted |
|---|---|
| `--commented` | a `.tex` and/or a `.pdf`. A `.tex` on its own uses the PDF next to it, or is compiled with `latexmk`. A `.pdf` on its own works without sources. |
| `--revised` | same as `--commented` |
| comments | `--comments FILE`: Markdown/text (checkbox lists `- [x]`, numbered or bulleted items, or paragraphs; `#` or stand-alone headers become sections; `<span style="color:..">` blocks and `Response:` paragraphs become author replies) or JSON (`[{"text", "section", "status", "responses"}]`).<br>Without `--comments`: annotations in the commented PDF (highlights, notes, replies), otherwise `\todo`, `\comment`, `\reviewer`, … macros in its TeX (add your own with `--macro`). |

`\input`/`\include` files are inlined, and simple argument-free `\newcommand` macros are expanded.

## Three ways to match

| Mode | Command | What it needs | Notes |
|---|---|---|---|
| **static** (default) | `--matcher static` | nothing (offline) | Deterministic: same inputs → byte-identical output. Uses quoted text, explicit references (Fig./Table/Eq./Section/Algorithm/page), section headers, BM25 similarity and the manuscript order of the comments. Revised-side passages are paired by text similarity and shared `\label`s. |
| **api** | `--matcher api` | `pip install anthropic` + `ANTHROPIC_API_KEY` (or `ant auth login`) | Claude (default `claude-opus-5-5`, `--model`, `--effort`) reads both documents once (prompt-cached) and matches `--batch-size` comments per request, with `--jobs` requests in parallel. Prints token use and an estimated cost. |
| **agent** | `--matcher agent`, then `--matches DIR` | Claude Code | Writes a task (`OUT/.revview/agent/INSTRUCTIONS.md`, passage listings, comment chunks). Claude, or one subagent per chunk, writes `results/chunk_NN.json`. Rerun with the printed `--matches` command to build. Any comment without a result falls back to the static match. |

Install the Claude Code skill so that Claude runs the whole agent workflow when asked
(“compare my commented v2 with v3”, or `/revview`):

```
revview install-skill        # writes ~/.claude/skills/revview/SKILL.md
```

On the CMAME paper (75 comments), the static matcher finds the same commented-version
passage as the agent for 63% of comments (exact or neighbouring passage). On the revised side it
agrees for 49%, where many differences are judgement calls about where a change was made. Use
static for offline or reproducible runs and as hints, and api/agent for the final viewer.

## Output

`OUT/index.html` + `data.js` + `old_pages/`, `new_pages/` (WebP or `--format png`) is a static
site that works from `file://`. Features: comment list grouped by section with a search filter, Open/Done filter (when the
comments have checkboxes), side-by-side or stacked layout, linked zoom (Ctrl+scroll, `+ − 0`),
a draggable splitter, and links such as `index.html#c=12&layout=stacked&zoom=1.5`.

`--zip` writes `OUT.zip` for sharing. It excludes `OUT/.revview/`, the working directory with
TeX-derived files: `matches.json` (passage ids, quotes, status, confidence, note per comment),
`static_matches.json`, `assembled.json` and the agent task.

## Python API

```python
from revview import build
build(["v2.tex", "v2.pdf"], ["v3.tex"], comments="feedback.md", matcher="static", out="site")
```

## Requirements

Python ≥ 3.9 and poppler-utils (`pdftotext`, `pdftoppm`). Optional: Pillow (WebP pages), pypdf
(PDF annotations), pandoc (Markdown and `$math$` in comments), latexmk (TeX-only inputs), anthropic
(api mode). Install with `pip install -e .[all]`, or run from the checkout as `python3 -m revview`.

Tests: `python3 tests/test_revview.py`.
