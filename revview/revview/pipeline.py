"""build(): manuscripts + comments -> matches -> PDF anchors -> static viewer."""
import json
import os
import sys

from . import comments as comments_mod
from . import match_agent, match_static, site
from .document import Document


def _label(paths):
    return os.path.splitext(os.path.basename(paths[0]))[0]


def build(commented, revised, comments=None, comment_source="auto", macros=None, matcher="static",
          matches=None, out="revview_site", workdir=None, labels=(None, None), title=None, dpi=150,
          image_format="webp", make_zip=False, model=None, effort="high", batch_size=10, jobs=4,
          chunk_size=12, build_cmd=None, log=sys.stderr):
    """Run the pipeline. `commented`/`revised` are lists of paths (a .tex and/or a .pdf each).

    matcher: 'static' (deterministic), 'api' (Claude API) or 'agent' (writes a task for a Claude
    Code agent and stops; rerun with matches=<results dir>). Returns a summary dict."""
    workdir = workdir or os.path.join(out, ".revview")
    os.makedirs(workdir, exist_ok=True)
    old = Document("old", labels[0] or _label(commented), commented, workdir)
    new = Document("new", labels[1] or _label(revised), revised, workdir)
    print(f"{old.label}: {len(old.passages)} passages, {len(old.pdf.pages)} pages; "
          f"{new.label}: {len(new.passages)} passages, {len(new.pdf.pages)} pages", file=log)
    items, how = comments_mod.load(old, comments, comment_source, macros)
    print(f"{len(items)} comments from {how}", file=log)

    static = match_static.match(old, new, items)
    _dump(os.path.join(workdir, "static_matches.json"), static)

    if matches:
        result, missing = match_agent.load(matches, old, new, items, fallback=static)
        used = f"results in {matches}"
        if missing:
            print(f"warning: no result for comment(s) {missing}; using the static match there", file=log)
    elif matcher == "static":
        result, used = static, "static matcher"
    elif matcher == "api":
        from . import match_api
        model = model or match_api.DEFAULT_MODEL
        print(f"Matching with the Claude API ({model}, effort {effort})...", file=log)
        result = match_api.match(old, new, items, hints=static, model=model, effort=effort,
                                 batch_size=batch_size, jobs=jobs, log=log)
        used = f"Claude API ({model})"
    elif matcher == "agent":
        root, n = match_agent.prepare(workdir, old, new, items, static,
                                      build_cmd or "revview build ... --matches " + os.path.join(workdir, "agent", "results"),
                                      chunk_size=chunk_size)
        print(f"Agent task written to {root} ({n} chunks). Have Claude follow {os.path.join(root, 'INSTRUCTIONS.md')},"
              f" then rerun with --matches {os.path.join(root, 'results')}", file=log)
        return {"state": "awaiting_agent", "task_dir": root, "chunks": n, "comments": len(items)}
    else:
        raise SystemExit(f"unknown matcher {matcher!r}")
    _dump(os.path.join(workdir, "matches.json"), result)

    by_id = {m["id"]: m for m in result}
    for c in items:
        m = by_id.get(c["id"]) or {}
        known = c.get("old") or {}
        if known.get("page") is not None and known.get("rect"):
            c["v2"] = old.anchor({"page": known["page"], "rect": known["rect"]})  # exact annotation box
        else:
            c["v2"] = old.anchor(m.get("old"))
        c["v3"] = new.anchor(m.get("new"))
        c["match_status"] = m.get("status", "")
        c["match"] = {k: m.get(k) for k in ("old", "new", "status", "confidence", "note")}
    _dump(os.path.join(workdir, "assembled.json"), items)

    title = title or f"{old.label} → {new.label} Reviewer Comments"
    zpath = site.write(out, old, new, items, title, dpi=dpi, fmt=image_format, make_zip=make_zip, log=log)
    located = (sum(1 for c in items if c["v2"] and c["v2"].get("page") is not None),
               sum(1 for c in items if c["v3"] and c["v3"].get("page") is not None))
    print(f"Matched with {used}: located {located[0]}/{len(items)} in {old.label}, "
          f"{located[1]}/{len(items)} in {new.label}", file=log)
    print(f"Viewer: {os.path.join(out, 'index.html')}" + (f"\nZip: {zpath}" if zpath else ""), file=log)
    return {"state": "done", "index": os.path.join(out, "index.html"), "zip": zpath, "comments": len(items),
            "located_old": located[0], "located_new": located[1], "matcher": used}


def _dump(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, default=str)
