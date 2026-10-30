# Tutorial: a one-page paper about stars

This folder holds a tiny manuscript in two versions and the reviewer's comments on it:

| File | What it is |
|---|---|
| `commented.tex` / `.pdf` | Draft 1, the version the reviewer read. It has a few deliberate mistakes ("Stars is", "8 seconds", "all stars collapse into black holes"). |
| `comments.md` | Nine reviewer comments in three sections, each with the author's reply in red. One is still open (`- [ ]`). |
| `revised.tex` / `.pdf` | Draft 2, the authors' revision. |

The PDFs are included, so you don't need LaTeX. If you delete them, revview compiles the `.tex`
files with `latexmk`.

## 1. Read the comments file

```markdown
Introduction

- [x] "about 8 seconds" -> it is about 8 minutes (1 AU / c ≈ 499 s).
  <span style="color:red">Fixed.</span>
```

`Introduction` on its own line starts a section. Each `- [x]` (done) or `- [ ]` (open) item is a
comment. Text inside `<span style="color:...">` is the authors' reply. Quoting the criticised
words, as in `"about 8 seconds"`, helps revview highlight exactly those words.

## 2. Build the viewer offline

Run from this folder:

```bash
revview build --commented commented.tex --revised revised.tex --comments comments.md -o stars_site
```

```
commented: 8 passages, 1 pages; revised: 9 passages, 1 pages
9 comments from file comments.md
Rendering pages (150 dpi, webp)...
Matched with static matcher: located 9/9 in commented, 9/9 in revised
Viewer: stars_site/index.html
```

Open `stars_site/index.html` in a browser. Click a comment on the left to see:

- **left page (commented):** the passage it refers to, with the quoted words highlighted;
- **right page (revised):** where the revision addressed it, e.g. "8 minutes" for the
  "8 seconds" comment, and the new temperature column for the Table 1 comment;
- the authors' reply under the comment, and its status (changed, unchanged, moved, removed).

Try the **Open/Done** filter: only the comment about the lifetime source is still open.

## 3. Let Claude do the matching

The static matcher is deterministic and works well on a short paper like this. On a real
manuscript, ask Claude in Claude Code from this folder:

> /revview Compare `commented.tex` with `revised.tex` using the comments in `comments.md`.

Claude writes a matching task, reads both versions, uses the replies to find each change, and
builds the same `stars_site/index.html`. Per-comment notes and confidence are in
`stars_site/.revview/matches.json`.

## 4. Share it

Add `--zip` to the build command to get `stars_site.zip`. It contains the viewer and page
images, but not `.revview/`, which holds the TeX-derived working files.

## Try your own changes

- Add a comment to `comments.md`, e.g. under `Stellar evolution`:
  `- [ ] "main sequence" should be defined on first use.` and rebuild.
- Edit `revised.tex`, delete `revised.pdf` and rebuild: revview recompiles it.
