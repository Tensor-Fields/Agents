"""What an LLM (Claude API or a Claude Code agent) is asked to do: the task text, the numbered
passage listings of both versions, per-comment task records, and the result schema."""
import json
import re

STATUSES = ["unchanged", "changed", "moved", "removed", "added", "unclear"]
CONFIDENCES = ["high", "medium", "low"]

INSTRUCTIONS = """\
You match reviewer comments to a manuscript in two versions:
  - the COMMENTED version: what the reviewer read and commented on;
  - the REVISED version: the authors' revision.
Both versions are given as numbered passages (paragraphs, headings, floats). Each passage header
looks like `[P0123 | lines 156-170 | p.4 | Section title]`; TeX sources keep their markup.

For each comment, find:
  1. old_passage: the passage of the COMMENTED version the comment is about, and old_quote: a short
     verbatim excerpt (about 3-25 words, copied exactly from that passage; shorter is fine for code
     or algorithm lines) pinpointing the criticised text.
     For a comment on a whole section/figure/algorithm, pick its heading or float passage and leave
     old_quote empty.
  2. new_passage + new_quote: the same for the REVISED version - the place where the comment was
     (or should have been) addressed. Use the authors' replies, if any, to find what changed.
  3. status: unchanged | changed | moved | removed | added | unclear (what happened to the
     commented text in the revision), confidence: high | medium | low, and a one-sentence note.
Use "" for a passage or quote that does not exist. When the commented text was removed but the
comment was addressed elsewhere, set status "removed" and give that other place as new_passage;
use new_passage "" only when nothing in the revision relates to the comment.
Comments are listed in the order the reviewer wrote them, which usually follows the manuscript.
A `hint` from a deterministic matcher may be given; it is often wrong - verify it.
"""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "matches": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "old_passage": {"type": "string"},
                    "old_quote": {"type": "string"},
                    "new_passage": {"type": "string"},
                    "new_quote": {"type": "string"},
                    "status": {"type": "string", "enum": STATUSES},
                    "confidence": {"type": "string", "enum": CONFIDENCES},
                    "note": {"type": "string"},
                },
                "required": ["id", "old_passage", "old_quote", "new_passage", "new_quote", "status",
                             "confidence", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["matches"],
    "additionalProperties": False,
}


def passage_listing(doc, role):
    out = [f'<document version="{role}" label="{doc.label}">']
    for p in doc.passages:
        head = [p["id"]]
        if p["start"]:
            head.append(f"lines {p['start']}-{p['end']}" if p["end"] != p["start"] else f"line {p['start']}")
        if p["page"] is not None:
            head.append(f"p.{p['page'] + 1}")
        if p["section"]:
            head.append(p["section"][:80])
        text = re.sub(r"[ \t]+", " ", p["text"]).strip()
        out.append(f"[{' | '.join(head)}]\n{text}\n")
    out.append("</document>")
    return "\n".join(out)


def task_records(comments, hints=None):
    hints = {h["id"]: h for h in (hints or [])}
    out = []
    for c in comments:
        rec = {"id": c["id"], "section": c.get("section", ""), "comment": c["reviewer_text"]}
        if c.get("responses"):
            rec["author_replies"] = [r["text"] for r in c["responses"]]
        known = c.get("old") or {}
        if known.get("passage") or known.get("quote"):
            rec["known_location"] = {k: known[k] for k in ("passage", "quote") if known.get(k)}
        h = hints.get(c["id"])
        if h:
            rec["hint"] = {"old_passage": (h["old"] or {}).get("passage", ""),
                           "new_passage": (h["new"] or {}).get("passage", ""), "status": h["status"]}
        out.append(rec)
    return out


def batch_prompt(records):
    return ("Match these comments. Return one entry per comment id.\n\n"
            + json.dumps(records, ensure_ascii=False, indent=1))


def to_matches(rows, old, new):
    """Schema rows (old_passage/old_quote/..., optionally old_lines/new_lines) -> internal match
    records; unknown passage ids are dropped."""
    out = []
    for r in rows:
        op = r.get("old_passage") or ""
        np_ = r.get("new_passage") or ""
        locs = []
        for side, doc, pid in (("old", old, op), ("new", new, np_)):
            loc = {"passage": pid, "quote": r.get(f"{side}_quote", "")} if pid in doc.by_id else None
            lines = r.get(f"{side}_lines")  # optional, agents may give exact TeX line ranges
            if lines and doc.lines:
                loc = dict(loc or {}, lines=[int(lines[0]), int(lines[-1])], quote=r.get(f"{side}_quote", ""))
            locs.append(loc)
        out.append({
            "id": int(r["id"]),
            "old": locs[0],
            "new": locs[1],
            "status": r.get("status", "unclear"),
            "confidence": r.get("confidence", "low"),
            "note": r.get("note", ""),
        })
    return out
