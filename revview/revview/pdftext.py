"""PDF text layer: word boxes from `pdftotext -bbox`, normalised tokens and n-gram indexes."""
import html
import re
import subprocess
from collections import defaultdict

PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
LIG = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl"}


def split_toks(t):
    """Normalised sub-tokens of a word, split on punctuation
    (so 'mesh.movePoints(mOpt' -> mesh, movepoints, mopt)."""
    for k, v in LIG.items():
        t = t.replace(k, v)
    t = t.lower().replace("’", "'")
    out = []
    for part in re.split(r"[^a-z0-9'-]+", t):
        part = part.strip("'-")
        if re.search(r"[a-z]", part) and len(part) >= 2:
            out.append(part)
    return out


def plain_toks(text):
    return [t for w in text.split() for t in split_toks(w)]


class PdfText:
    def __init__(self, pdf_path):
        raw = subprocess.run(["pdftotext", "-bbox", pdf_path, "-"], capture_output=True,
                             text=True, errors="replace", check=True).stdout
        self.pages, self.boxes = [], []
        for pi, pm in enumerate(PAGE_RE.finditer(raw)):
            self.pages.append({"width": float(pm.group(1)), "height": float(pm.group(2))})
            for wm in WORD_RE.finditer(pm.group(3)):
                x0, y0, x1, y1, t = wm.groups()
                self.boxes.append({"page": pi, "x0": float(x0), "y0": float(y0), "x1": float(x1),
                                   "y1": float(y1), "t": html.unescape(t)})
        # tokens: merge end-of-line hyphenation, drop tokens without letters
        self.toks = []  # (norm, [box indices])
        i = 0
        while i < len(self.boxes):
            b = self.boxes[i]
            t = b["t"]
            idx = [i]
            if t.endswith("-") and i + 1 < len(self.boxes):
                nb = self.boxes[i + 1]
                if abs(nb["y0"] - b["y0"]) > 3 and nb["t"][:1].islower():
                    t = t[:-1] + nb["t"]
                    idx.append(i + 1)
                    i += 1
            for n in split_toks(t):
                self.toks.append((n, idx))
            i += 1
        self.tri = defaultdict(list)
        self.bi = defaultdict(list)
        T = [t[0] for t in self.toks]
        for j in range(len(T) - 2):
            self.tri[(T[j], T[j + 1], T[j + 2])].append(j)
        for j in range(len(T) - 1):
            self.bi[(T[j], T[j + 1])].append(j)
        self._textbox = None

    def tok_page(self, j):
        return self.boxes[self.toks[j][1][0]]["page"]

    def page_meta(self):
        return [{"width": p["width"], "height": p["height"]} for p in self.pages]

    def text_box(self):
        """(left, right, typical top) of the body text, robust to outliers."""
        if self._textbox is None:
            xs0 = sorted(b["x0"] for b in self.boxes) or [0]
            xs1 = sorted(b["x1"] for b in self.boxes) or [0]
            tops = sorted(min(b["y0"] for b in self.boxes if b["page"] == q)
                          for q in {b["page"] for b in self.boxes}) or [60]
            self._textbox = (xs0[len(xs0) // 50], xs1[-max(1, len(xs1) // 50)], tops[len(tops) // 10])
        return self._textbox

    def lines(self):
        """Group word boxes into text lines: list of dicts with page, y0, y1, x0, x1, box indices."""
        out = []
        for i, b in enumerate(self.boxes):
            L = out[-1] if out else None
            if L and L["page"] == b["page"] and abs(b["y0"] - L["y0"]) <= 3 and b["x0"] >= L["x1"] - 2:
                L["x1"] = max(L["x1"], b["x1"]); L["y1"] = max(L["y1"], b["y1"]); L["boxes"].append(i)
            else:
                out.append({"page": b["page"], "y0": b["y0"], "y1": b["y1"], "x0": b["x0"],
                            "x1": b["x1"], "boxes": [i]})
        return out
