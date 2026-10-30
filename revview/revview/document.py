"""One manuscript version: optional flattened TeX source + PDF text layer, segmented into
passages, with the machinery to turn a TeX line range / passage / quote into PDF rectangles."""
import os
import re
import shutil
import subprocess
from collections import Counter, defaultdict

from .pdftext import PdfText, plain_toks, split_toks
from .texflat import flatten

DROP_ARG = (r"(?:cite[pt]?|ref|eqref|label|autoref|cref|Cref|url|includegraphics|input|bibliography\w*|"
            r"hspace|vspace|begin|end|usepackage|newcommand|renewcommand|def|color|textcolor|resizebox|"
            r"scalebox|setlength)")
MATH_BEGIN = re.compile(r"\\begin\{(equation|align|gather|multline|eqnarray)\*?\}|^\s*\\\[")
MATH_END = re.compile(r"\\end\{(equation|align|gather|multline|eqnarray)\*?\}|\\\]\s*$")
HEADING_RE = re.compile(r"^\s*\\(part|chapter|section|subsection|subsubsection|paragraph)\*?\s*(\[[^\]]*\])?\{(.*)")
FLOAT_BEGIN = re.compile(r"\\begin\{(figure|table|algorithm)\*?\}")
FLOAT_END = re.compile(r"\\end\{(figure|table|algorithm)\*?\}")
ENV_PASSAGE = re.compile(r"^\s*\\(?:begin\{(abstract|keywords?|highlights)\}|(abstract)\{)")


def strip_comment(ln):
    return re.sub(r"(?<!\\)%.*", "", ln)


def is_live(ln):
    return bool(strip_comment(ln).strip())


def tex_plain(lines):
    """TeX lines -> normalised word tokens as they would appear in the PDF text layer."""
    s = " ".join(strip_comment(ln) for ln in lines)
    s = re.sub(r"\$\$.*?\$\$|\\\[.*?\\\]", " ", s)

    def _inline(m):  # keep \text{..} words and plain identifiers of inline math
        x = re.sub(r"\\(?:text|mathrm|textrm|textit|textbf|mathit|operatorname)\{([^{}]*)\}", r" \1 ", m.group(1))
        x = re.sub(r"\\[a-zA-Z]+", " ", x)
        x = re.sub(r"(?<=[A-Za-z0-9])_\{?[A-Za-z0-9]+\}?|\^\{?[A-Za-z0-9]+\}?", " ", x)
        return " " + x + " "
    s = re.sub(r"\$([^$]*)\$", _inline, s)
    s = re.sub(r"\\begin\{(equation|align|gather|multline|eqnarray|tikzpicture)\*?\}.*?\\end\{\1\*?\}", " ", s, flags=re.S)
    s = re.sub(r"\\" + DROP_ARG + r"\*?(\[[^\]]*\])*(\{[^{}]*\})*", " ", s)
    s = re.sub(r"\\[a-zA-Z@]+\*?", " ", s)
    s = s.replace("~", " ").replace("``", " ").replace("''", " ")
    s = re.sub(r"[{}]", "", re.sub(r"[\[\]]", " ", s))
    return [t for w in s.split() for t in split_toks(w)]


def text_toks(text):
    """Tokens of free text that may contain TeX markup (quotes, comments)."""
    return tex_plain([text]) if "\\" in text or "$" in text else plain_toks(text)


def brace_arg(text, i):
    """Content of the {...} group starting at or after index i (balanced), or None."""
    j = text.find("{", i)
    if j < 0:
        return None
    depth = 0
    for k in range(j, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return text[j + 1:k]
    return text[j + 1:]


NEWCMD_RE = re.compile(r"\\(?:newcommand|renewcommand|providecommand|def)\*?\s*\{?\\([A-Za-z@]+)\}?\s*(\[0\])?\s*\{")


def expand_simple_macros(lines):
    """Expand argument-free user macros (\\newcommand{\\name}{text}) in the document body, so the
    TeX text matches what the PDF shows. Line count is unchanged."""
    defs = {}
    for ln in lines:
        code = strip_comment(ln)
        for m in NEWCMD_RE.finditer(code):
            rest = code[m.end() - 1:]
            if re.match(r"\s*\[[1-9]", code[m.end(1) + 1:m.end()]):
                continue
            body = brace_arg(rest, 0)
            if body is not None and "#" not in body and len(body) < 200 and "\\" + m.group(1) not in body:
                defs[m.group(1)] = body
    if not defs:
        return lines
    pat = re.compile(r"\\(" + "|".join(sorted(map(re.escape, defs), key=len, reverse=True)) + r")(?![A-Za-z@])(\{\})?")
    out, body = [], False
    for ln in lines:
        if not body and "\\begin{document}" in ln:
            body = True
        if body and "\\" in ln:
            for _ in range(3):
                new = pat.sub(lambda m: defs[m.group(1)] + " ", ln)
                if new == ln:
                    break
                ln = new
        out.append(ln)
    return out


def compile_tex(tex_path, outdir):
    if not shutil.which("latexmk"):
        raise SystemExit(f"No PDF next to {tex_path} and latexmk is not installed; pass the PDF explicitly.")
    os.makedirs(outdir, exist_ok=True)
    subprocess.run(["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", f"-outdir={outdir}",
                    os.path.basename(tex_path)], cwd=os.path.dirname(os.path.abspath(tex_path)),
                   check=True, stdout=subprocess.DEVNULL)
    return os.path.join(outdir, os.path.splitext(os.path.basename(tex_path))[0] + ".pdf")


def split_inputs(paths):
    """['a.tex', 'a.pdf'] in any order -> (tex or None, pdf or None)."""
    tex = pdf = None
    for p in paths:
        ext = os.path.splitext(p)[1].lower()
        if ext == ".pdf":
            pdf = p
        elif ext in (".tex", ".ltx"):
            tex = p
        else:
            raise SystemExit(f"Unsupported input {p!r}: give a .tex and/or a .pdf file")
        if not os.path.isfile(p):
            raise SystemExit(f"Input not found: {p}")
    return tex, pdf


class Document:
    def __init__(self, key, label, paths, workdir):
        self.key, self.label = key, label
        tex, pdf = split_inputs(paths)
        if tex and not pdf:
            sib = os.path.splitext(tex)[0] + ".pdf"
            pdf = sib if os.path.isfile(sib) else compile_tex(tex, os.path.join(workdir, key + "_latex"))
        if not pdf:
            raise SystemExit(f"{label}: need a PDF (or a .tex that compiles)")
        self.tex_path, self.pdf_path = tex, pdf
        self.lines = self.src = None
        if tex:
            self.lines, self.src = flatten(tex)
            self.lines = expand_simple_macros(self.lines)
        self.pdf = PdfText(pdf)
        self.passages = self._tex_passages() if tex else self._pdf_passages()
        self.by_id = {p["id"]: p for p in self.passages}

    # ------------------------------------------------------------------ passages
    def _tex_passages(self):
        L = self.lines
        n = len(L)
        start = next((i + 1 for i, ln in enumerate(L) if "\\begin{document}" in strip_comment(ln)), 0)
        stop = next((i for i, ln in enumerate(L) if "\\end{document}" in strip_comment(ln)), n)
        out, cur, section, level = [], [], "", 0
        in_float = False
        LEVEL = {"part": 0, "chapter": 1, "section": 2, "subsection": 3, "subsubsection": 4, "paragraph": 5}

        def close(kind="para"):
            nonlocal cur
            live = [i for i in cur if is_live(L[i])]
            if live:
                out.append({"kind": kind, "start": live[0] + 1, "end": live[-1] + 1, "section": section,
                            "level": level if kind == "heading" else None})
            cur = []
        for i in range(start, stop):
            ln = L[i]
            code = strip_comment(ln)
            if in_float:
                cur.append(i)
                if FLOAT_END.search(code):
                    in_float = False
                    close("float")
                continue
            if not code.strip():
                if not ln.strip():  # blank line ends a paragraph; comment lines do not
                    close()
                continue
            h = HEADING_RE.match(code)
            if h:
                close()
                arg = brace_arg(code, h.start(3) - 1)
                section = re.sub(r"\s+", " ", re.sub(r"\\[a-zA-Z]+\*?|[{}$]", " ", arg or "")).strip()
                level = LEVEL[h.group(1)]
                cur = [i]
                close("heading")
                continue
            if FLOAT_BEGIN.search(code):
                close()
                in_float = True
                cur.append(i)
                if FLOAT_END.search(code):
                    in_float = False
                    close("float")
                continue
            if ENV_PASSAGE.match(code):
                close()
                m = ENV_PASSAGE.match(code)
                section = (m.group(1) or m.group(2)).capitalize()
            cur.append(i)
        close()
        for k, p in enumerate(out):
            p["id"] = f"P{k + 1:04d}"
            body = [strip_comment(x).rstrip() for x in L[p["start"] - 1:p["end"]]]
            p["text"] = "\n".join(x for x in body if x.strip())
            p["toks"] = tex_plain(L[p["start"] - 1:p["end"]])
            cap = brace_arg(p["text"], p["text"].find("\\caption") + 8) if "\\caption" in p["text"] else None
            if p["kind"] == "float":
                env = re.search(r"\\begin\{(figure|table|algorithm)", p["text"])
                p["float"] = env.group(1) if env else None
            al_toks = tex_plain([cap]) if cap and len(tex_plain([cap])) >= 3 else p["toks"]
            al = self.align(al_toks) if al_toks else None
            p["lo"], p["hi"] = (al[0], al[1]) if al and al[3] >= 0.3 else (None, None)
            p["page"] = self.pdf.tok_page(p["lo"]) if p["lo"] is not None else None
        self._anchor_headings(out)
        return out

    def _anchor_headings(self, passages):
        """Headings are too short for n-gram alignment: find them as PDF lines that start with a
        section number followed by the heading words, scanning in document order."""
        P = self.pdf
        B = P.boxes
        num_re = re.compile(r"^(?:[A-Z]|[A-Z]?\d+(?:\.\d+)*)\.?$")
        box2tok = {}
        for j, (_, idx) in enumerate(P.toks):
            for b in idx:
                box2tok.setdefault(b, j)
        cands = [i for i in range(len(B) - 1)
                 if num_re.match(B[i]["t"]) and B[i + 1]["page"] == B[i]["page"]
                 and abs(B[i + 1]["y0"] - B[i]["y0"]) <= 3
                 and (i == 0 or B[i - 1]["page"] != B[i]["page"] or abs(B[i - 1]["y0"] - B[i]["y0"]) > 3)]
        pos = 0
        for p in passages:
            if p["kind"] != "heading" or not p["toks"]:
                continue
            want = p["toks"][:3]
            for ci in list(range(pos, len(cands))) + list(range(0, pos)):
                i = cands[ci]
                j = box2tok.get(i + 1)
                line = [t for t, idx in P.toks[j:j + len(want) + 3] if B[idx[0]]["y0"] - B[i]["y0"] <= 3] if j is not None else []
                k = 0
                for t in line:  # heading words in order (macros or acronyms may sit in between)
                    if k < len(want) and t == want[k]:
                        k += 1
                if k == len(want) and line and line[0] in want[:2]:
                    p["lo"], p["hi"] = j, j + len(p["toks"]) - 1
                    p["page"] = B[i]["page"]
                    p["number"] = B[i]["t"].rstrip(".")
                    pos = ci + 1
                    break

    def _pdf_passages(self):
        P = self.pdf
        lines = P.lines()
        gaps = sorted(b["y0"] - a["y0"] for a, b in zip(lines, lines[1:])
                      if a["page"] == b["page"] and 0 < b["y0"] - a["y0"] < 40)
        lead = gaps[len(gaps) // 2] if gaps else 12
        left = P.text_box()[0]
        groups, cur = [], []
        for a in lines:
            prev = cur[-1] if cur else None
            new_par = (prev is None or a["page"] != prev["page"] or a["y0"] - prev["y0"] > 1.45 * lead
                       or (a["x0"] - left > 8 and a["x0"] - prev["x0"] > 8))
            if new_par and cur:
                groups.append(cur)
                cur = []
            cur.append(a)
        if cur:
            groups.append(cur)
        box2tok = {}
        for j, (_, idx) in enumerate(P.toks):
            for b in idx:
                box2tok.setdefault(b, j)
        out, section = [], ""
        for g in groups:
            toks = sorted(box2tok[b] for ln in g for b in ln["boxes"] if b in box2tok)
            words = " ".join(P.boxes[b]["t"] for ln in g for b in ln["boxes"])
            if not toks:
                continue
            kind, level = "para", None
            if len(g) == 1 and re.match(r"^(\d+(\.\d+)*\.?|[A-Z]\.|Appendix [A-Z]\.?)\s+[A-Z]", words) and len(words) < 90:
                kind, section = "heading", words
                level = 2 + words.split()[0].rstrip(".").count(".")
            elif re.match(r"^(Figure|Fig\.|Table|Algorithm)\s+\d+\s*[:.|]", words):
                kind = "float"
            out.append({"id": f"P{len(out) + 1:04d}", "kind": kind, "start": None, "end": None,
                        "number": words.split()[0].rstrip(".") if kind == "heading" else None,
                        "section": section, "level": level, "text": words, "toks": [P.toks[j][0] for j in range(toks[0], toks[-1] + 1)],
                        "lo": toks[0], "hi": toks[-1], "page": g[0]["page"]})
        return out

    def passage_at_line(self, line):
        for p in self.passages:
            if p["start"] and p["start"] <= line <= p["end"]:
                return p
        cands = [p for p in self.passages if p["start"] and p["start"] > line]
        return cands[0] if cands else (self.passages[-1] if self.passages else None)

    def passage_at_rect(self, page, x0, y0, x1, y1):
        """Passage owning the most PDF tokens inside a rectangle (PDF points, top-left origin)."""
        hit = Counter()
        for j, (_, idx) in enumerate(self.pdf.toks):
            b = self.pdf.boxes[idx[0]]
            if b["page"] == page and b["x1"] >= x0 - 2 and b["x0"] <= x1 + 2 and b["y1"] >= y0 - 2 and b["y0"] <= y1 + 2:
                for p in self.passages:
                    if p["lo"] is not None and p["lo"] <= j <= p["hi"]:
                        hit[p["id"]] += 1
                        break
        return self.by_id[hit.most_common(1)[0][0]] if hit else None

    def text_in_rects(self, page, rects):
        words = []
        for b in self.pdf.boxes:
            if b["page"] != page:
                continue
            cx, cy = (b["x0"] + b["x1"]) / 2, (b["y0"] + b["y1"]) / 2
            if any(x0 - 1 <= cx <= x1 + 1 and y0 - 1 <= cy <= y1 + 1 for x0, y0, x1, y1 in rects):
                words.append(b["t"])
        return " ".join(words)

    # ------------------------------------------------------------------ PDF alignment
    def align(self, toks, window=None):
        """Vote on the diagonal offset (pdf_pos - tok_pos) with trigrams then bigrams.
        Returns (lo, hi, votes, coverage) of the PDF token range, or None."""
        P = self.pdf
        votes = []
        ok = (lambda p: window[0] <= p <= window[1]) if window else (lambda p: True)
        limit = 400 if window else 40
        for n, table in ((3, P.tri), (2, P.bi)):
            for t in range(len(toks) - n + 1):
                hits = [p for p in table.get(tuple(toks[t:t + n]), []) if ok(p)]
                if 0 < len(hits) <= limit:
                    votes.extend((p - t, p, n) for p in hits)
            if len([v for v in votes if v[2] == 3]) >= 3:
                break
        if not votes and window and len(toks) <= 2:
            votes = [(j, j, 1) for j in range(window[0], window[1] + 1) if P.toks[j][0] in toks[:1]]
        if not votes:
            return None
        bins = Counter(v[0] // 8 for v in votes)
        best_bin, cnt = max(bins.items(), key=lambda kv: (kv[1], -kv[0]))
        if cnt < 2 and len(toks) > 4:
            return None
        sel = [v for v in votes if abs(v[0] // 8 - best_bin) <= 1]
        lo = min(v[1] for v in sel)
        hi = max(v[1] + v[2] - 1 for v in sel)
        if hi - lo > 3 * len(toks) + 20:
            hi = lo + len(toks) + 5
        coverage = len({v[1] for v in sel}) / max(1, len(toks))
        return lo, hi, cnt, coverage

    def rects(self, lo, hi):
        P = self.pdf
        idx = sorted({b for j in range(lo, hi + 1) for b in P.toks[j][1]})
        groups = []
        for bi in idx:
            b = P.boxes[bi]
            g = groups[-1] if groups else None
            if g and g["page"] == b["page"] and abs(b["y0"] - g["y0"]) <= 4 and b["x0"] >= g["x1"] - 2:
                g["x1"] = max(g["x1"], b["x1"]); g["y0"] = min(g["y0"], b["y0"]); g["y1"] = max(g["y1"], b["y1"])
            else:
                groups.append({k: b[k] for k in ("page", "x0", "y0", "x1", "y1")})
        out = []
        for g in groups:
            pw, ph = P.pages[g["page"]]["width"], P.pages[g["page"]]["height"]
            out.append({"page": g["page"], "left": (g["x0"] - 1.5) / pw, "top": (g["y0"] - 1.5) / ph,
                        "width": (g["x1"] - g["x0"] + 3) / pw, "height": (g["y1"] - g["y0"] + 3) / ph})
        return out

    def block_rect(self, page, y0, y1):
        pw, ph = self.pdf.pages[page]["width"], self.pdf.pages[page]["height"]
        x0, x1, _ = self.pdf.text_box()
        return {"page": page, "left": (x0 - 3) / pw, "top": (y0 - 3) / ph,
                "width": (x1 - x0 + 6) / pw, "height": (y1 - y0 + 6) / ph}

    def blockify(self, rr):
        """Merge per-line rects into one full-width block per page (covers equations between lines)."""
        out = []
        for p in sorted({r["page"] for r in rr}):
            ph = self.pdf.pages[p]["height"]
            sub = [r for r in rr if r["page"] == p]
            y0 = min(r["top"] for r in sub) * ph + 1.5
            y1 = max(r["top"] + r["height"] for r in sub) * ph - 1.5
            out.append(self.block_rect(p, y0, y1))
        return out

    def figure_rects(self, cap_rr):
        """Caption rects -> one block from the last full-width body line above the caption to caption bottom."""
        P = self.pdf
        p = cap_rr[0]["page"]
        ph = P.pages[p]["height"]
        cap_top = min(r["top"] for r in cap_rr if r["page"] == p) * ph
        cap_bot = max(r["top"] + r["height"] for r in cap_rr if r["page"] == p) * ph
        x0, x1, typical_top = P.text_box()
        lines = defaultdict(lambda: [1e9, -1e9, 0])
        for b in P.boxes:
            if b["page"] == p and b["y1"] < cap_top - 2:
                L = lines[round(b["y0"])]
                L[0] = min(L[0], b["x0"]); L[1] = max(L[1], b["x1"]); L[2] = max(L[2], b["y1"])
        top = None
        for y, (lx0, lx1, ly1) in sorted(lines.items(), reverse=True):
            if (lx1 - lx0) > 0.8 * (x1 - x0):
                top = ly1 + 4
                break
        if top is None:
            top = typical_top
        return [self.block_rect(p, top, cap_bot)]

    def algorithm_rects(self, cap_rr, cap_hi, joined):
        """Caption rects -> one block from the caption down to the last line of the algorithm body
        (on the caption's page)."""
        body = joined[joined.find("\\caption"):]
        body = body[len(brace_arg(body, 0) or "") + 10:]
        btoks = tex_plain([body])
        p = cap_rr[0]["page"]
        ph = self.pdf.pages[p]["height"]
        top = min(r["top"] for r in cap_rr if r["page"] == p) * ph + 1.5
        bot = max(r["top"] + r["height"] for r in cap_rr if r["page"] == p) * ph - 1.5
        if btoks:
            al = self.align(btoks, (cap_hi, min(len(self.pdf.toks) - 1, cap_hi + 3 * len(btoks) + 60)))
            if al:
                tail = self.align(btoks[-8:], (al[1], min(len(self.pdf.toks) - 1, al[1] + 60))) if len(btoks) > 8 else None
                end = max(al[1], tail[1]) if tail else al[1]
                last = [r for r in self.rects(al[0], end) if r["page"] == p]
                if last:
                    bot = max(bot, max(r["top"] + r["height"] for r in last) * ph - 1.5)
        return [self.block_rect(p, top, bot)]

    def context_toks(self, i, step, need=8):
        """Tokens from live lines walking from index i in direction step until `need` tokens."""
        lines, L = [], self.lines
        while 0 <= i < len(L) and len(lines) < 12:
            lines.append(L[i])
            if len(tex_plain(lines if step > 0 else lines[::-1])) >= need:
                break
            i += step
        return tex_plain(lines if step > 0 else lines[::-1])

    def equation_rects(self, s, e):
        P = self.pdf
        b = self.align(self.context_toks(s - 2, -1))
        a = self.align(self.context_toks(e, +1))
        rb = self.rects(b[0], b[1]) if b else []
        ra = self.rects(a[0], a[1]) if a else []
        if rb and ra and rb[-1]["page"] == ra[0]["page"]:
            p = rb[-1]["page"]; ph = P.pages[p]["height"]
            y0 = (rb[-1]["top"] + rb[-1]["height"]) * ph
            y1 = ra[0]["top"] * ph
            if y1 > y0 + 4:
                return [self.block_rect(p, y0, y1)]
        if rb:
            p = rb[-1]["page"]; ph = P.pages[p]["height"]
            y0 = (rb[-1]["top"] + rb[-1]["height"]) * ph
            ys = sorted({round(w["y1"]) for w in P.boxes if w["page"] == p})
            body_bottom = max([y for y in ys if y < ys[-1] - 15] or [ph - 60]) if ys else ph - 60
            return [self.block_rect(p, y0, max(y0 + 10, min(y0 + 70, body_bottom + 4)))]
        if ra:
            p = ra[0]["page"]; ph = P.pages[p]["height"]
            y1 = ra[0]["top"] * ph
            return [self.block_rect(p, max(60, y1 - 70), y1)]
        return []

    def extend_over_math(self, s, e, rr):
        """A range that starts/ends with a display equation: stretch the block up/down to the
        neighbouring text so the equation itself is covered."""
        live = [ln for ln in self.lines[s - 1:e] if is_live(ln)]
        if not live or len(rr) != 1:
            return rr
        r0 = rr[0]; p = r0["page"]; ph = self.pdf.pages[p]["height"]
        top, bot = r0["top"] * ph, (r0["top"] + r0["height"]) * ph
        if MATH_END.search(live[-1]):
            a = self.align(self.context_toks(e, +1))
            ra = self.rects(a[0], a[1]) if a else []
            if ra and ra[0]["page"] == p and ra[0]["top"] * ph > bot:
                bot = min(ra[0]["top"] * ph - 1, bot + 160)
        if MATH_BEGIN.search(live[0]):
            b = self.align(self.context_toks(s - 2, -1))
            rb = self.rects(b[0], b[1]) if b else []
            if rb and rb[-1]["page"] == p and (rb[-1]["top"] + rb[-1]["height"]) * ph < top:
                top = max((rb[-1]["top"] + rb[-1]["height"]) * ph + 1, top - 160)
        return [self.block_rect(p, top + 3, bot - 3)]

    # ------------------------------------------------------------------ anchors
    def src_label(self, s, e):
        f0, l0 = self.src[s - 1]
        f1, l1 = self.src[e - 1]
        return f"{f0}:{l0}" + (f"-{l1}" if f1 == f0 and l1 != l0 else "")

    def locate_quote_lines(self, s, e, quote):
        """Shift a line range so that it contains `quote` (searching nearby first)."""
        q = text_toks(quote)[:12]
        if len(q) < 2:
            return s, e, True
        L = self.lines

        def has(i):
            toks = tex_plain(L[max(0, i - 1):i + 2])
            return any(toks[k:k + len(q[:4])] == q[:4] for k in range(len(toks)))
        if any(has(i) for i in range(s - 1, e)):
            return s, e, True
        for d in range(1, 60):
            for c in (s - 1 - d, e - 1 + d):
                if 0 <= c < len(L) and has(c):
                    return c + 1, c + 1 + (e - s), True
        return s, e, False

    def anchor_lines(self, s, e):
        """TeX line range (1-based, inclusive) -> anchor dict."""
        L = self.lines
        s, e = max(1, s), min(len(L), max(s, e))
        lines = L[s - 1:e]
        note = []
        commented = not any(is_live(x) for x in lines)
        src = lines
        if commented:  # %-commented text is not in the PDF: use adjacent live text
            before, j = [], s - 2
            while j >= 0 and len(before) < 3:
                if is_live(L[j]):
                    before.insert(0, L[j])
                j -= 1
            after, j = [], e
            while j < len(L) and len(after) < 3:
                if is_live(L[j]):
                    after.append(L[j])
                j += 1
            src = before + after
            note.append("passage is %-commented in the TeX (not in PDF); showing adjacent text")
        rec = {"src": self.src_label(s, e), "lines": [s, e]}
        joined = "\n".join(strip_comment(x) for x in src)
        is_float = bool(re.search(r"\\begin\{(figure|table)", joined)) or "\\caption" in joined
        has_math = bool(re.search(r"\\begin\{(equation|align|gather|multline)|\\\[|\$\$", joined))
        cap = None
        if is_float and "\\caption" in joined:
            tail = joined[joined.find("\\caption"):]
            cap = re.split(r"\\end\{(?:figure|table)\*?\}|\\label\{", tail)[0]
        is_alg = "\\begin{algorithm" in joined
        prefix = ["table"] if "\\begin{table" in joined else ["algorithm"] if is_alg else ["fig"]
        toks = (prefix + tex_plain([cap])) if cap else tex_plain(src)
        if len(toks) < 3 and not cap and not commented:
            rr = self.equation_rects(s, e)
            if rr:
                rec.update(page=rr[0]["page"], rects=rr, approx=False, note="equation (band between surrounding text)")
                return rec
        widened = False
        if len(toks) < 3 and not cap:
            toks = tex_plain(L[max(0, s - 4):min(len(L), e + 3)])
            widened = True
            note.append("widened (math)")
        al = self.align(toks) if toks else None
        if not al:
            rec.update(page=None, rects=[], approx=True)
            note.append("not found in PDF text")
        else:
            lo, hi, cnt, cov = al
            rr = self.rects(lo, hi)
            if cap and rr and is_alg:
                rr = self.algorithm_rects(rr, hi, joined)
                note.append("algorithm block")
            elif cap and rr and "\\begin{table" not in joined:
                rr = self.figure_rects(rr)
                note.append("figure block")
            elif rr and (widened or has_math or is_float):
                rr = self.blockify(rr)
                if has_math and not widened:
                    rr = self.extend_over_math(s, e, rr)
            rec.update(page=rr[0]["page"] if rr else None, rects=rr, approx=commented or cov < 0.3)
            if cov < 0.3:
                note.append(f"weak PDF alignment ({cov:.0%})")
        rec["note"] = "; ".join(note)
        return rec

    def anchor_quote(self, quote, window=None):
        q = text_toks(quote)
        if len(q) < 2:
            return None
        al = self.align(q, window)
        if not al or al[3] < 0.4:
            return None
        rr = self.rects(al[0], al[1])
        return {"page": rr[0]["page"], "rects": rr, "approx": False, "note": "quoted text"} if rr else None

    def anchor(self, loc):
        """Resolve a location {lines|passage|quote|page+rect} to {src, page, rects, approx, note}."""
        if not loc:
            return None
        quote = (loc.get("quote") or "").strip()
        p = self.by_id.get(loc.get("passage") or "")
        if loc.get("lines") and self.lines:
            s, e = loc["lines"][0], loc["lines"][-1]
            ok = True
            if quote:
                s, e, ok = self.locate_quote_lines(s, e, quote)
            rec = self.anchor_lines(s, e)
            if not ok:
                rec["note"] = "; ".join(x for x in (rec.get("note"), "quote not verified") if x)
            return rec
        if p:
            if p["start"]:
                rec = self.anchor_lines(p["start"], p["end"])
            else:
                rr = self.rects(p["lo"], p["hi"])
                rec = {"src": f"p. {p['page'] + 1}", "page": p["page"], "rects": rr, "approx": False, "note": ""}
            rec["passage"] = p["id"]
            if quote and p["lo"] is not None and len(rec.get("rects") or []) > 1:
                q = self.anchor_quote(quote, (max(0, p["lo"] - 40), min(len(self.pdf.toks) - 1, p["hi"] + 40)))
                if q:
                    rec.update(page=q["page"], rects=q["rects"])
            return rec
        if loc.get("page") is not None and loc.get("rect"):
            pg = loc["page"]
            pw, ph = self.pdf.pages[pg]["width"], self.pdf.pages[pg]["height"]
            x0, y0, x1, y1 = loc["rect"]
            return {"src": f"p. {pg + 1}", "page": pg, "approx": False, "note": "PDF annotation",
                    "rects": [{"page": pg, "left": x0 / pw, "top": y0 / ph, "width": (x1 - x0) / pw,
                               "height": (y1 - y0) / ph}]}
        if quote:
            rec = self.anchor_quote(quote)
            if rec:
                rec["src"] = f"p. {rec['page'] + 1}"
            return rec
        return None
