---
name: revview
description: Build a side-by-side viewer that links reviewer comments to the commented version of a manuscript (TeX and/or PDF) and to the revised version. Use when the user wants to compare a commented/reviewed paper with its revision, map reviewer comments (a feedback file, PDF annotations, or \todo-style TeX notes) to locations, or prepare a response-to-reviewers viewer.
---

# revview: reviewer comments ↔ commented and revised manuscript

`revview` is a Python package; run it as `{{REVVIEW}}`.
Inputs: the commented version (`.tex` and/or `.pdf`), the revised version (`.tex` and/or `.pdf`) and,
optionally, a comments file (`.md`/`.txt`/`.json`). Without a comments file, comments are read from
the commented PDF's annotations, or else from `\todo`/`\comment`-style macros in its TeX.

## 1. Pick the mode

- **agent** (default inside Claude Code): you do the semantic matching, described below. Most accurate.
- **static**: the user wants deterministic/offline output, or something fast. Run
  `{{REVVIEW}} build --matcher static ...` and go to step 4.
- **api**: the user wants the matching done by the Claude API from the terminal (needs
  `ANTHROPIC_API_KEY`). Run `{{REVVIEW}} build --matcher api ...` and go to step 4.

If an input is ambiguous (which file is the commented version, where the comments are), ask.

## 2. Write the task

```bash
{{REVVIEW}} build --commented OLD.tex [OLD.pdf] --revised NEW.tex [NEW.pdf] \
    [--comments FEEDBACK.md] [--labels v1 v2] -o OUT --matcher agent
```

The output names the task directory (`OUT/.revview/agent/`) and the number of chunks. Read
`OUT/.revview/agent/INSTRUCTIONS.md`. It defines the result format and gives the exact build command for step 4.

## 3. Match the comments

Each `chunks/chunk_NN.json` must end up with a `results/chunk_NN.json`.

- 1–2 chunks: do them yourself.
- More: launch one subagent per chunk, all in a single message so they run in parallel. Give
  each subagent a self-contained prompt with the absolute paths of `INSTRUCTIONS.md`,
  `commented_passages.txt`, `revised_passages.txt`, its chunk file and the result file it must
  write. Tell it to follow INSTRUCTIONS.md, to search the passage files with Grep rather than
  reading them whole, and to verify each `hint` instead of copying it.

When matching:
- Quotes must be copied verbatim from the chosen passage. They narrow the highlight.
- You may add `"old_lines": [start, end]` / `"new_lines": [start, end]` (TeX line numbers from
  the passage headers) when a passage is long and the comment concerns only part of it.
- Use the authors' replies to find where the revision addressed the comment.

Check before building: every chunk has a results file, every comment id appears, and every
passage id exists. The build falls back to the static match for missing ids and names them.

## 4. Build and report

Run the build command from INSTRUCTIONS.md (it ends in `--matches OUT/.revview/agent/results`).
Report the viewer path (`OUT/index.html`), how many comments were located in each version, and
any comments matched with low confidence (`OUT/.revview/matches.json`). Add `--zip` to the
command when the user wants a shareable archive. The zip excludes `.revview/`, which holds the
TeX-derived task files.
