"""Agent mode: revview writes the matching task to files, an agent (Claude Code, optionally with
parallel subagents, one per chunk) writes results/chunk_NN.json, and `revview build --matches`
assembles the site from them."""
import glob
import json
import os
import shutil

from . import tasks

GUIDE = """\
# revview matching task

{instructions}
## Files
- `commented_passages.txt` - the COMMENTED version ({old_label}), {n_old} passages.
- `revised_passages.txt`   - the REVISED version ({new_label}), {n_new} passages.
- `chunks/chunk_NN.json`   - the comments to match, in reviewer order ({n_comments} comments,
  {n_chunks} chunks). Each record: id, section, comment, optional author_replies,
  known_location, and a deterministic `hint` (often wrong - verify it).

Read the passage files with grep/search rather than end to end: look up quoted phrases,
section titles and key terms from the comment, then read the surrounding passages.

## Output
For every chunk write `results/chunk_NN.json` (same NN) containing exactly:

```json
{{"matches": [{{"id": 0, "old_passage": "P0012", "old_quote": "verbatim words from P0012",
               "new_passage": "P0015", "new_quote": "verbatim words from P0015",
               "status": "changed", "confidence": "high", "note": "one sentence"}}]}}
```

- one entry per comment id of the chunk; passage ids must exist in the listing;
- status is one of {statuses}; confidence one of {confidences};
- use "" when a passage/quote does not exist.

When all chunks are done, build the viewer with:

    {build_cmd}
"""


def prepare(workdir, old, new, comments, hints, build_cmd, chunk_size=12):
    root = os.path.join(workdir, "agent")
    if os.path.isdir(os.path.join(root, "chunks")):
        shutil.rmtree(os.path.join(root, "chunks"))
    os.makedirs(os.path.join(root, "chunks"), exist_ok=True)
    os.makedirs(os.path.join(root, "results"), exist_ok=True)
    with open(os.path.join(root, "commented_passages.txt"), "w") as f:
        f.write(tasks.passage_listing(old, "commented"))
    with open(os.path.join(root, "revised_passages.txt"), "w") as f:
        f.write(tasks.passage_listing(new, "revised"))
    records = tasks.task_records(comments, hints)
    chunks = [records[i:i + chunk_size] for i in range(0, len(records), chunk_size)]
    for k, ch in enumerate(chunks, 1):
        with open(os.path.join(root, "chunks", f"chunk_{k:02d}.json"), "w") as f:
            json.dump(ch, f, ensure_ascii=False, indent=1)
    with open(os.path.join(root, "INSTRUCTIONS.md"), "w") as f:
        f.write(GUIDE.format(instructions=tasks.INSTRUCTIONS, old_label=old.label, new_label=new.label,
                             n_old=len(old.passages), n_new=len(new.passages), n_comments=len(records),
                             n_chunks=len(chunks), statuses=" | ".join(tasks.STATUSES),
                             confidences=" | ".join(tasks.CONFIDENCES), build_cmd=build_cmd))
    with open(os.path.join(root, "result_schema.json"), "w") as f:
        json.dump(tasks.RESULT_SCHEMA, f, indent=1)
    return root, len(chunks)


def load(path, old, new, comments, fallback=None):
    """Read results from a directory of *.json (or one file); fill gaps from `fallback` matches."""
    files = sorted(glob.glob(os.path.join(path, "*.json"))) if os.path.isdir(path) else [path]
    rows = []
    for fn in files:
        data = json.load(open(fn, encoding="utf-8"))
        rows += data["matches"] if isinstance(data, dict) else data
    got = {}
    for m in tasks.to_matches(rows, old, new):
        got[m["id"]] = m
    fb = {m["id"]: m for m in (fallback or [])}
    out, missing = [], []
    for c in comments:
        if c["id"] in got:
            out.append(got[c["id"]])
        else:
            missing.append(c["id"])
            out.append(dict(fb[c["id"]], note="(static) " + fb[c["id"]]["note"]) if c["id"] in fb else
                       {"id": c["id"], "old": None, "new": None, "status": "unclear", "confidence": "low",
                        "note": "no result"})
    return out, missing
