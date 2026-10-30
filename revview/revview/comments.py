"""Reviewer comments from (a) a Markdown/text/JSON file, (b) annotations in the commented PDF,
or (c) annotation macros (\\todo, \\comment, ...) in the commented TeX source.

Every comment is a dict:
  id, section, status ('open'|'done'|''), reviewer_text, responses [{color, text}],
  old (optional known location in the commented version: {passage, quote} / {page, rect})
"""
import html
import json
import os
import re
import shutil
import subprocess
import textwrap

from .document import brace_arg, strip_comment

# ---------------------------------------------------------------- Markdown / text
SPAN_RE = re.compile(r'<span[^>]*style="color:\s*(\w+)"[^>]*>(.*?)</span>', re.S | re.I)
UL_RE = re.compile(r'<ul[^>]*style="color:\s*(\w+)"[^>]*>(.*?)</ul>', re.S | re.I)
LI_RE = re.compile(r'<li[^>]*>(.*?)</li>', re.S | re.I)
TAG_RE = re.compile(r'<[^>]+>')
CHECKBOX_RE = re.compile(r'^(?:\s*-\s*)+\[([ xX])\]\s?(.*)$')
ITEM_RE = re.compile(r'^(?:\s{0,3}(?:[-*+]|\(?\d+[.)]|\(?[a-z][.)]|[A-Z]\d+(?:\.\d+)*[.:)]?|'
                     r'(?:Comment|Point|Remark|Issue)\s+\d+(?:\.\d+)*\s*[.:)])\s+)(.*)$')
MD_HEADER_RE = re.compile(r'^(#{1,6})\s*(.+?)\s*#*\s*$')
REPLY_RE = re.compile(r'^\s*(?:>+\s*)?(?:\*\*)?(?:Response|Reply|Answer|Author(?:s)?(?: response)?)\s*(?:\*\*)?\s*:\s*(.*)$', re.I)


def _strip_tags(s):
    return TAG_RE.sub('', s).strip()


def _md_block(s):
    """Keep line structure, drop the indentation inherited from a surrounding list item."""
    lines = s.split("\n")
    first = lines[0].strip()
    rest = textwrap.dedent("\n".join(lines[1:]))
    t = (first + "\n" + rest) if first else rest
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _is_plain_header(lines, i):
    """A short stand-alone line (blank line before and after) that is not a sentence."""
    s = lines[i]
    if not s.strip() or s[0].isspace() or "<" in s or CHECKBOX_RE.match(s) or ITEM_RE.match(s):
        return False
    st = s.strip()
    if len(st) > 60 or re.search(r"[.;,?!)\"\u201d]$|->|[\u201c\u201d\"]", st):
        return False
    before = i == 0 or not lines[i - 1].strip()
    after = (i + 1 >= len(lines) or not lines[i + 1].strip()
             or bool(CHECKBOX_RE.match(lines[i + 1]) or ITEM_RE.match(lines[i + 1])))
    return before and after


def _split_responses(raw):
    """Separate coloured author replies (<span style="color:..">, <ul style=..>) and
    'Response:' paragraphs from the reviewer's text."""
    responses = []

    def ul_sub(m):
        inner = m.group(2)
        lis = LI_RE.findall(inner)
        if lis:
            lead = _md_block(_strip_tags(inner[:inner.lower().find('<li')]))
            items = "\n".join("- " + _md_block(_strip_tags(x)).replace("\n", "\n  ") for x in lis)
            text = (lead + "\n\n" if lead else "") + items
        else:
            text = _md_block(_strip_tags(inner))
        responses.append({'color': m.group(1).lower(), 'text': text})
        return ''

    def span_sub(m):
        text = _md_block(_strip_tags(m.group(2)))
        if text:
            responses.append({'color': m.group(1).lower(), 'text': text})
        return ''
    rest = SPAN_RE.sub(span_sub, UL_RE.sub(ul_sub, raw))
    keep, reply = [], None
    for ln in rest.split("\n"):
        m = REPLY_RE.match(ln)
        if m:
            reply = [m.group(1)]
            responses.append({'color': 'green', 'text': ''})
            continue
        if reply is not None:
            if not ln.strip() and reply and reply[-1] == "":
                responses[-1]['text'] = _md_block("\n".join(reply))
                reply = None
                keep.append(ln)
                continue
            reply.append(ln)
            continue
        keep.append(ln)
    if reply is not None:
        responses[-1]['text'] = _md_block("\n".join(reply))
    responses = [r for r in responses if r['text']]
    keep = [ln for ln in keep if not re.fullmatch(r"\s*[-*+]\s*", ln)]  # bullets whose reply was moved out
    reviewer = _md_block(_strip_tags("\n".join(keep)))
    if not reviewer and responses and all(r['color'] == 'green' for r in responses):
        reviewer = "\n\n".join(r['text'] for r in responses)
        responses = []
    return reviewer, responses


def from_text(path):
    raw = open(path, encoding="utf-8", errors="replace").read()
    raw = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    lines = raw.split("\n")
    checkbox_mode = any(CHECKBOX_RE.match(ln) for ln in lines)
    items, section, lead = [], "", []
    i, n = 0, len(lines)

    def header_at(k):
        m = MD_HEADER_RE.match(lines[k])
        if m:
            return m.group(2).strip()
        if _is_plain_header(lines, k):
            return lines[k].strip()
        return None
    while i < n:
        h = header_at(i)
        if h is not None:
            section, lead = h, []
            i += 1
            continue
        if checkbox_mode:
            m = CHECKBOX_RE.match(lines[i])
            start = m and (m.group(2), 'done' if m.group(1).lower() == 'x' else 'open')
        else:
            m = ITEM_RE.match(lines[i]) if lines[i].strip() else None
            start = m and (m.group(1), '')
            if not m and lines[i].strip() and (i == 0 or not lines[i - 1].strip()):
                start = (lines[i], '')  # plain paragraph
        if not start:
            if checkbox_mode and lines[i].strip() and not lines[i][:1].isspace() and "<" not in lines[i]:
                lead.append(lines[i].strip())
            i += 1
            continue
        block = [start[0]]
        if lead:
            block = [" ".join(lead), *block]
            lead = []
        i += 1
        while i < n:
            if header_at(i) is not None:
                break
            if checkbox_mode and CHECKBOX_RE.match(lines[i]):
                break
            if not checkbox_mode and lines[i].strip() and (
                    (ITEM_RE.match(lines[i]) and not lines[i][:1].isspace())
                    or (not ITEM_RE.match(lines[i]) and not lines[i - 1].strip() and not lines[i][:1].isspace()
                        and not REPLY_RE.match(lines[i]) and not lines[i].lstrip().startswith("<"))):
                break
            block.append(lines[i])
            i += 1
        reviewer, responses = _split_responses("\n".join(block))
        if reviewer or responses:
            items.append({'section': section, 'status': start[1], 'reviewer_text': reviewer, 'responses': responses})
    return items


def from_json(path):
    data = json.load(open(path, encoding="utf-8"))
    out = []
    for d in data:
        if isinstance(d, str):
            d = {"text": d}
        out.append({'section': d.get('section', ''), 'status': d.get('status', ''),
                    'reviewer_text': d.get('reviewer_text') or d.get('text', ''),
                    'responses': d.get('responses', []), 'old': d.get('old')})
    return out


# ---------------------------------------------------------------- PDF annotations
MARKUP = {"/Text", "/FreeText", "/Highlight", "/Underline", "/StrikeOut", "/Squiggly", "/Caret", "/Square",
          "/Circle", "/Ink", "/Polygon", "/PolyLine"}


def from_pdf_annotations(doc):
    """Markup annotations of doc.pdf_path; highlighted words become the quote, the annotation
    rectangle the known location. Replies (/IRT) become responses."""
    try:
        import pypdf
    except ImportError:
        return []
    reader = pypdf.PdfReader(doc.pdf_path)
    found, by_ref = [], {}
    for pno, page in enumerate(reader.pages):
        annots = page.get("/Annots") or []
        box = page.cropbox
        ox, top = float(box.left), float(box.top)
        for aref in annots:
            a = aref.get_object()
            sub = a.get("/Subtype")
            if sub not in MARKUP:
                continue
            contents = str(a.get("/Contents") or "").strip()
            r = [float(x) for x in a.get("/Rect", [0, 0, 0, 0])]
            rect = (min(r[0], r[2]) - ox, top - max(r[1], r[3]), max(r[0], r[2]) - ox, top - min(r[1], r[3]))
            quote = ""
            if "/QuadPoints" in a:
                q = [float(x) for x in a["/QuadPoints"]]
                quads = []
                for k in range(0, len(q) - 7, 8):
                    xs, ys = q[k:k + 8:2], q[k + 1:k + 8:2]
                    quads.append((min(xs) - ox, top - max(ys), max(xs) - ox, top - min(ys)))
                quote = doc.text_in_rects(pno, quads)
            rec = {"page": pno, "rect": rect, "quote": quote, "text": contents, "author": str(a.get("/T") or ""),
                   "kind": sub[1:], "irt": a.get("/IRT")}
            ref = getattr(aref, "idnum", None)
            if ref is not None:
                by_ref[ref] = rec
            found.append(rec)
    out = []
    for rec in found:
        irt = rec["irt"]
        parent = by_ref.get(getattr(irt, "idnum", None)) if irt is not None else None
        if parent is not None:
            parent.setdefault("replies", []).append(rec)
    for rec in found:
        if rec["irt"] is not None and by_ref.get(getattr(rec["irt"], "idnum", None)) is not None:
            continue
        if not rec["text"] and not rec["quote"]:
            continue
        text = rec["text"] or f"({rec['kind'].lower()})"
        if rec["kind"] in ("StrikeOut",) and rec["quote"]:
            text = f"Delete: \u201c{rec['quote']}\u201d" + (f"\n\n{rec['text']}" if rec["text"] else "")
        out.append({'section': rec["author"] or "PDF annotations", 'status': '',
                    'reviewer_text': text,
                    'responses': [{'color': 'green', 'text': r["text"]} for r in rec.get("replies", []) if r["text"]],
                    'old': {"page": rec["page"], "rect": list(rec["rect"]), "quote": rec["quote"]}})
    out.sort(key=lambda c: (c['old']['page'], c['old']['rect'][1]))
    return out


# ---------------------------------------------------------------- TeX macros
DEFAULT_MACROS = ["todo", "comment", "reviewer", "note", "remark", "fixme", "fxnote", "fxwarning", "rev", "rc"]


def from_tex_macros(doc, macros=None):
    if not doc.lines:
        return []
    macros = macros or DEFAULT_MACROS
    pat = re.compile(r"\\(" + "|".join(map(re.escape, macros)) + r")\*?(?:\[([^\]]*)\])?(?=\{)")
    L = doc.lines
    start = next((i for i, ln in enumerate(L) if "\\begin{document}" in strip_comment(ln)), 0)
    out = []
    for i in range(start, len(L)):
        code = strip_comment(L[i])
        for m in pat.finditer(code):
            # the argument may continue on following lines
            text = brace_arg(code + "\n" + "\n".join(strip_comment(x) for x in L[i + 1:i + 30]), m.end())
            if not text or not text.strip():
                continue
            before = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?(\{[^{}]*\})?|[{}]", " ", code[:m.start()]).split()
            p = doc.passage_at_line(i + 1)
            opts = m.group(2) or ""
            who = re.search(r"(?:id|author)\s*=\s*([^,\]]+)", opts)
            out.append({'section': (who.group(1).strip() if who else (p["section"] if p else "")) or m.group(1),
                        'status': '', 'reviewer_text': re.sub(r"\s+", " ", text).strip(), 'responses': [],
                        'old': {"passage": p["id"] if p else None, "lines": [i + 1, i + 1],
                                "quote": " ".join(before[-10:])}})
    return out


# ---------------------------------------------------------------- loading + rendering
def load(old_doc, comments_path=None, source="auto", macros=None):
    if comments_path:
        ext = os.path.splitext(comments_path)[1].lower()
        items = from_json(comments_path) if ext == ".json" else from_text(comments_path)
        how = f"file {os.path.basename(comments_path)}"
    else:
        items, how = [], ""
        if source in ("auto", "pdf"):
            items, how = from_pdf_annotations(old_doc), "PDF annotations"
        if not items and source in ("auto", "tex"):
            items, how = from_tex_macros(old_doc, macros), "TeX annotation macros"
    if not items:
        raise SystemExit("No comments found. Pass --comments FILE, or annotate the commented PDF/TeX.")
    for k, c in enumerate(items):
        c['id'] = k
        c.setdefault('old', None)
    render_html(items)
    return items, how


def _tex_quotes(t):
    """LaTeX-style ``x'' and `x' quotes -> typographic quotes (not Markdown code)."""
    t = re.sub(r"``([^`\n]+?)''", "\u201c\\1\u201d", t)
    return re.sub(r"`([^`'\n]+)'(?!')", "\u2018\\1\u2019", t)


def render_html(items):
    """Markdown (+ $math$ -> MathML) to HTML in one pandoc call; plain escaping without pandoc."""
    snippets = []
    for c in items:
        snippets.append(c['reviewer_text'])
        snippets.extend(r['text'] for r in c['responses'])
    snippets = [_tex_quotes(t) for t in snippets]
    if shutil.which("pandoc"):
        sep = "QQSEP{:05d}QQ"
        doc = "\n\n".join(sep.format(i) + "\n\n" + t for i, t in enumerate(snippets)) + "\n\n" + sep.format(len(snippets)) + "\n"
        out = subprocess.run(
            ["pandoc", "-f", "markdown-raw_html-raw_tex-subscript-superscript+lists_without_preceding_blankline",
             "-t", "html", "--mathml", "--wrap=none"], input=doc, capture_output=True, text=True, check=True).stdout
        parts = re.split(r"<p>QQSEP(\d{5})QQ</p>", out)
        rendered = {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
        rendered = [rendered.get(i, "") for i in range(len(snippets))]
    else:
        rendered = ["".join(f"<p>{html.escape(p).replace(chr(10), '<br>')}</p>" for p in re.split(r"\n\s*\n", t) if p.strip())
                    for t in snippets]
    k = 0
    for c in items:
        c['reviewer_html'] = rendered[k]; k += 1
        for r in c['responses']:
            r['html'] = rendered[k]; k += 1
