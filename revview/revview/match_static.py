"""Deterministic matcher: no network, no randomness, same input -> same output.

Commented-version location: quoted phrases > explicit references (Fig./Table/Eq./Section/
Algorithm/page) > BM25 similarity of the comment to each passage (+ section-name bonus).
Revised-version location: best text-similar passage to the commented one (or to text quoted in
the author's reply), with unchanged/changed/removed status from the similarity."""
import math
import re
from collections import Counter, defaultdict

from .document import text_toks

STOP = set("""a an and are as at be been but by can could did do does for from had has have how i if in into is it
its may might more most must no not of on or our over shall should so such than that the their them then there
these they this those to too under up us was we were what when where which while who why will with would you
your also very just e g eg ie et al use used using one two paper section figure fig eq equation table page
line lines see here now please""".split())
QUOTE_RE = re.compile(r"“([^”]{6,400})”|\"([^\"\n]{6,400})\"|``(.{6,400}?)''|‘([^’]{6,300})’")
REF_RE = re.compile(r"\b(fig(?:ure)?s?\.?|tables?|eqs?\.?|equations?|sec(?:tion)?s?\.?|§|algorithms?|appendix|"
                    r"pages?|pp?\.)\s*\(?([A-Z]?\d+(?:\.\d+)*)\)?", re.I)


def content(toks):
    return [t for t in toks if t not in STOP and len(t) > 2]


def sentences(text):
    """Split passage text (TeX or plain) into sentence strings, keeping display math attached."""
    flat = re.sub(r"\s*\n\s*", " ", text)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\\$(])", flat)
    return [p for p in parts if len(text_toks(p)) >= 3]


def ngrams(toks, n=3):
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


def similarity(a, b):
    """Token-trigram Jaccard, falling back to content-word overlap for short passages."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ga, gb = ngrams(a), ngrams(b)
    if len(ga) >= 3 and len(gb) >= 3:
        tri = len(ga & gb) / len(ga | gb)
    else:
        tri = 0.0
    ca, cb = Counter(content(a)), Counter(content(b))
    inter = sum((ca & cb).values())
    uni = sum((ca | cb).values()) or 1
    return max(tri, 0.8 * inter / uni)


class BM25:
    def __init__(self, docs, k1=1.4, b=0.75):
        self.docs = [Counter(content(d)) for d in docs]
        self.len = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.len) / max(1, len(self.len))
        df = Counter(t for d in self.docs for t in d)
        N = len(self.docs)
        self.idf = {t: math.log(1 + (N - n + 0.5) / (n + 0.5)) for t, n in df.items()}
        self.k1, self.b = k1, b

    def scores(self, query):
        q = set(content(query))
        out = []
        for d, L in zip(self.docs, self.len):
            s = 0.0
            for t in q:
                f = d.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * L / self.avg))
            out.append(s)
        return out


LABEL_RE = re.compile(r"\\label\{([^}]+)\}")


def labels(p):
    if "_labels" not in p:
        p["_labels"] = set(LABEL_RE.findall(p["text"])) if p.get("start") else set()
    return p["_labels"]


def find_run(hay, needle, min_run=4):
    """Length of the longest run of `needle` tokens appearing contiguously in `hay`."""
    if not hay or not needle:
        return 0
    pos = defaultdict(list)
    for i, t in enumerate(hay):
        pos[t].append(i)
    best = 0
    for j in range(len(needle)):
        for i in pos.get(needle[j], ()):
            k = 0
            while j + k < len(needle) and i + k < len(hay) and hay[i + k] == needle[j + k]:
                k += 1
            best = max(best, k)
        if best == len(needle):
            break
    return best if best >= min(min_run, len(needle)) else 0


def quotes_in(text):
    out = []
    for m in QUOTE_RE.finditer(text or ""):
        q = next(g for g in m.groups() if g)
        if len(text_toks(q)) >= 3:
            out.append(q)
    return out


class StaticMatcher:
    def __init__(self, old, new):
        self.old, self.new = old, new
        self.old_bm = BM25([p["toks"] for p in old.passages])
        self.new_bm = BM25([p["toks"] for p in new.passages])
        self._pairs = {}
        self._spans = {}
        self._hnum = {}

    # ------------------------------------------------------------ references
    def _ref_passages(self, doc, kind, num):
        """Passages of doc referenced as e.g. ('fig', '3'), via the PDF text layer."""
        kind = kind.lower().rstrip(".s")
        P = doc.pdf
        boxes = P.boxes
        hits = []
        if kind.startswith(("fig", "tab", "alg")):
            word = {"fig": ("figure", "fig."), "tab": ("table",), "alg": ("algorithm",)}[kind[:3]]
            for i in range(len(boxes) - 1):
                if boxes[i]["t"].lower() in word and re.fullmatch(re.escape(num) + r"[:.|]?", boxes[i + 1]["t"]):
                    # captions start a line; prefer those over in-text mentions
                    line_start = i == 0 or boxes[i - 1]["page"] != boxes[i]["page"] or abs(boxes[i - 1]["y0"] - boxes[i]["y0"]) > 3
                    if line_start and (boxes[i + 1]["t"][-1:] in ":.|" or kind.startswith("alg")):
                        hits.append(i)
            if not hits and doc.lines:  # count float environments in the TeX source instead
                env = {"fig": "figure", "tab": "table", "alg": "algorithm"}[kind[:3]]
                floats = [p for p in doc.passages if p.get("float") == env]
                if num.isdigit() and 0 < int(num) <= len(floats):
                    return [floats[int(num) - 1]]
        elif kind.startswith("eq"):
            for i, b in enumerate(boxes):
                if b["t"] == f"({num})" and b["x0"] > P.text_box()[1] - 60:
                    hits.append(i)
        elif kind.startswith(("sec", "§", "app")):
            j = self.heading_numbers(doc).get(num.rstrip("."))
            return [doc.passages[j]] if j is not None else []
        elif kind.startswith("p"):
            pg = int(num.split(".")[0]) - 1 if num.split(".")[0].isdigit() else -1
            return [p for p in doc.passages if p["page"] == pg]
        out = []
        for i in hits[:1]:
            tok = self._box_to_tok(doc, i)
            anchored = [p for p in doc.passages if p["lo"] is not None]
            inside = [p for p in anchored if p["lo"] <= tok <= p["hi"]]
            if inside:
                out.append(min(inside, key=lambda p: p["hi"] - p["lo"]))
            elif anchored:
                # a caption label sits just before its caption text: prefer the passage that follows
                out.append(min(anchored, key=lambda p: (min(abs(p["lo"] - tok), abs(p["hi"] - tok)), p["lo"] < tok)))
        return out

    def heading_numbers(self, doc):
        """{'2.1': passage index} from the number printed before each heading in the PDF."""
        if doc.key in self._hnum:
            return self._hnum[doc.key]
        out = {}
        for j, p in enumerate(doc.passages):
            if p.get("number") and p["number"] not in out:
                out[p["number"]] = j
        self._hnum[doc.key] = out
        return out

    def _box_to_tok(self, doc, box):
        toks = doc.pdf.toks
        lo, hi = 0, len(toks) - 1
        while lo < hi:  # first token whose first box index >= box
            mid = (lo + hi) // 2
            if toks[mid][1][0] < box:
                lo = mid + 1
            else:
                hi = mid
        return lo

    # ------------------------------------------------------------ commented version
    def section_span(self, doc, label):
        """Passage indices covered by a comment's section label ('Introduction', '5.1 Upset ...',
        'Section 2: DALE', 'Algorithm 1', 'Abstract'), or None if it cannot be resolved."""
        label = (label or "").strip().rstrip(":")
        if not label:
            return None
        key = (doc.key, label)
        if key in self._spans:
            return self._spans[key]
        span = None
        idx = {p["id"]: k for k, p in enumerate(doc.passages)}
        m = REF_RE.match(label)
        if m and not m.group(1).lower().startswith(("sec", "\u00a7", "p")):
            hits = self._ref_passages(doc, m.group(1), m.group(2))
            span = {idx[p["id"]] for p in hits} or None
        elif label.lower() in ("abstract", "summary", "keywords", "highlights", "title"):
            span = {k for k, p in enumerate(doc.passages) if (p["section"] or "").lower() == label.lower()
                    or (label.lower() == "title" and k < 3)} or None
        else:
            words = set(content(text_toks(re.sub(r"(?i)\bsection\b", " ", label))))
            num = re.search(r"\b[A-Z]?\d+(?:\.\d+)*\b", label)
            by_num = self.heading_numbers(doc).get(num.group(0)) if num else None
            best, head = 0.0, None
            for k, p in enumerate(doc.passages):
                if p["kind"] != "heading":
                    continue
                hw = set(content(text_toks(p["section"] or p["text"])))
                sc = (len(hw & words) / len(words)) if (hw and words) else 0.0
                sc += 1.0 if k == by_num else 0.0
                sc -= 0.01 * (p.get("level") or 2)  # prefer the higher-level heading on ties
                if sc > best:
                    best, head = sc, k
            if best < 0.5:
                head = None
            if head is not None:
                lvl = doc.passages[head].get("level") or 2
                end = next((k for k in range(head + 1, len(doc.passages))
                            if doc.passages[k]["kind"] == "heading" and (doc.passages[k].get("level") or 2) <= lvl),
                           len(doc.passages))
                span = set(range(head, end))
        self._spans[key] = span
        return span

    def old_candidates(self, c, k=12):
        """[(score, passage index, kind, detail)] best first."""
        doc = self.old
        text = c["reviewer_text"]
        P = doc.passages
        quote_s, ref_s, why = defaultdict(float), defaultdict(float), {}
        for q in quotes_in(text):
            qt = text_toks(q)
            hits = [(find_run(p["toks"], qt), j) for j, p in enumerate(P)]
            hits = [(r, j) for r, j in hits if r]
            for r, j in hits:
                sc = (3.0 * r / len(qt) + (1.0 if r == len(qt) else 0)) / (1 + 0.5 * (len(hits) - 1))
                if P[j]["kind"] == "heading" and len(hits) > 1:
                    sc *= 0.5
                if sc > quote_s[j]:
                    quote_s[j] = sc
                    why[j] = ("quote", q)
        for m in REF_RE.finditer(text):
            for p in self._ref_passages(doc, m.group(1), m.group(2)):
                j = P.index(p)
                ref_s[j] = max(ref_s[j], 2.0 if not m.group(1).lower().startswith("p") else 0.6)
                why.setdefault(j, ("ref", m.group(0)))
        bm = self.old_bm.scores(text_toks(text))
        top = max(bm) if bm else 0
        span = self.section_span(doc, c.get("section"))
        out = []
        for j, p in enumerate(P):
            sc = quote_s[j] + ref_s[j] + ((bm[j] / top) if top else 0)
            if span is not None:
                sc += 1.0 if j in span else -0.5
            if p["kind"] == "heading":
                sc -= 0.5
            kind, detail = why.get(j, ("text", ""))
            out.append((sc, j, kind, detail))
        out.sort(key=lambda x: (-x[0], x[1]))
        return out[:k]

    def locate_known(self, c):
        doc = self.old
        known = c.get("old") or {}
        if known.get("passage") and known["passage"] in doc.by_id:
            p = doc.by_id[known["passage"]]
            return p, known.get("quote") or self.best_sentence(p, text_toks(c["reviewer_text"])), "high", "given location"
        if known.get("page") is not None and known.get("rect"):
            p = doc.passage_at_rect(known["page"], *known["rect"])
            if p:
                return p, known.get("quote") or "", "high", "PDF annotation"
        return None

    def locate_old_all(self, comments):
        """Pick one commented passage per comment. Reviewers write comments in manuscript order,
        so the choice is a Viterbi path that penalises going backwards in the document."""
        fixed, cands = {}, []
        for c in comments:
            kn = self.locate_known(c)
            if kn:
                fixed[c["id"]] = kn
                j = self.old.passages.index(kn[0])
                cands.append([(5.0, j, "given", "")])
            else:
                cands.append(self.old_candidates(c) or [(0.0, 0, "text", "")])
        BACK = 0.6
        best = [[(cd[0], None) for cd in cands[0]]] if cands else []
        for t in range(1, len(cands)):
            row = []
            for sc, j, *_ in cands[t]:
                opts = []
                for i, (prev_sc, _) in enumerate(best[t - 1]):
                    pj = cands[t - 1][i][1]
                    pen = BACK if j < pj - 1 else 0.0
                    if comments[t].get("section") != comments[t - 1].get("section"):
                        pen *= 0.5
                    opts.append((prev_sc + sc - pen, i))
                row.append(max(opts, key=lambda x: (x[0], -x[1])))
            best.append(row)
        path = [0] * len(cands)
        if cands:
            path[-1] = max(range(len(best[-1])), key=lambda i: (best[-1][i][0], -i))
            for t in range(len(cands) - 1, 0, -1):
                path[t - 1] = best[t][path[t]][1]
        out = {}
        for t, c in enumerate(comments):
            if c["id"] in fixed:
                out[c["id"]] = fixed[c["id"]]
                continue
            sc, j, kind, detail = cands[t][path[t]]
            if sc <= 0.2:
                out[c["id"]] = (None, "", "low", "no similar passage")
                continue
            p = self.old.passages[j]
            others = [x[0] for x in cands[t] if x[1] != j]
            margin = sc - (max(others) if others else 0)
            if kind == "quote":
                out[c["id"]] = (p, detail, "high", f"quoted text found ({detail[:40]}\u2026)")
            else:
                conf = "medium" if kind == "ref" or margin > 0.25 else "low"
                note = f"reference {detail}" if kind == "ref" else "lexical similarity"
                out[c["id"]] = (p, self.best_sentence(p, text_toks(c["reviewer_text"])), conf, note)
        return out

    @staticmethod
    def best_sentence(p, query_toks):
        """The sentence of passage p most similar to the query (used to narrow the highlight)."""
        if p["kind"] != "para":
            return ""
        sents = sentences(p["text"])
        if len(sents) < 3:
            return ""
        q = set(content(query_toks))
        scored = [(len(q & set(content(text_toks(s)))), -i, s) for i, s in enumerate(sents)]
        best = max(scored)
        return best[2] if best[0] >= 2 else ""

    # ------------------------------------------------------------ revised version
    def pair(self, op):
        """Best revised passage for a commented passage, with similarity."""
        if op["id"] in self._pairs:
            return self._pairs[op["id"]]
        cands = []
        rel_old = self.old.passages.index(op) / max(1, len(self.old.passages))
        for k, np_ in enumerate(self.new.passages):
            if np_["kind"] == "heading" and op["kind"] != "heading":
                continue
            s = similarity(op["toks"], np_["toks"])
            if labels(op) & labels(np_):  # \label keys usually survive a revision
                s = max(s, 0.5) + 0.5
            if s > 0:
                rel_new = k / max(1, len(self.new.passages))
                cands.append((s - 0.05 * abs(rel_new - rel_old), s, np_["id"]))
        if not cands:
            self._pairs[op["id"]] = (None, 0.0)
        else:
            _, s, nid = max(cands, key=lambda x: (x[0], x[2]))
            self._pairs[op["id"]] = (self.new.by_id[nid], min(1.0, s) if s <= 1.0 else
                                     similarity(op["toks"], self.new.by_id[nid]["toks"]) or 0.5)
        return self._pairs[op["id"]]

    def locate_new(self, c, op, old_quote):
        new = self.new
        reply = "\n".join(r["text"] for r in c["responses"])
        # text quoted in the author's reply is the strongest hint for where the revision is
        for q in quotes_in(reply) + quotes_in(re.sub(r"^\s*(changed|replaced|now|new)\s*:\s*", "", reply, flags=re.I)):
            qt = text_toks(q)
            best = max(((find_run(p["toks"], qt), p) for p in new.passages), key=lambda x: (x[0], x[1]["id"] == ""), default=(0, None))
            if best[0] >= min(6, len(qt)):
                return best[1], q, "changed", "high", "text quoted in the reply found"
        if op is None:
            bm = self.new_bm.scores(text_toks(c["reviewer_text"] + "\n" + reply))
            if not bm or max(bm) <= 0:
                return None, "", "unclear", "low", ""
            k = max(range(len(bm)), key=lambda i: (bm[i], -i))
            return new.passages[k], "", "unclear", "low", "lexical similarity"
        np_, s = self.pair(op)
        if np_ is None or s < 0.12:
            bm = self.new_bm.scores(op["toks"] + text_toks(reply))
            k = max(range(len(bm)), key=lambda i: (bm[i], -i)) if bm and max(bm) > 0 else None
            if k is None:
                return None, "", "removed", "medium", "no similar passage in the revision"
            return new.passages[k], "", "removed", "low", "passage rewritten or removed; nearest topic shown"
        status = "unchanged" if s >= 0.999 else "changed"
        conf = "high" if s >= 0.6 else ("medium" if s >= 0.3 else "low")
        nq = ""
        note = f"similarity {s:.2f}"
        if old_quote:
            qt = text_toks(old_quote)
            run = find_run(np_["toks"], qt)
            if run == len(qt):
                nq = old_quote
                note += "; quoted text still present"
                if status == "changed":
                    status = "changed"
            else:
                sent = self.best_sentence(np_, qt)
                nq = sent
                note += "; quoted text changed"
                status = "changed"
        return np_, nq, status, conf, note

    # ------------------------------------------------------------ driver
    def match(self, comments):
        out = []
        olds = self.locate_old_all(comments)
        for c in comments:
            op, oq, oconf, onote = olds[c["id"]]
            np_, nq, status, nconf, nnote = self.locate_new(c, op, oq)
            rank = {"high": 2, "medium": 1, "low": 0}
            conf = min(oconf, nconf, key=lambda x: rank[x])
            out.append({
                "id": c["id"],
                "old": {"passage": op["id"], "quote": oq} if op else None,
                "new": {"passage": np_["id"], "quote": nq} if np_ else None,
                "status": status, "confidence": conf,
                "note": "; ".join(x for x in (onote, nnote) if x),
            })
        return out


def match(old, new, comments):
    return StaticMatcher(old, new).match(comments)
