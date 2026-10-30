"""End-to-end tests on a small synthetic manuscript (needs pdflatex/latexmk, poppler, pypdf).

Run:  python3 -m pytest tests   or   python3 tests/test_revview.py"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from revview import build, comments, match_static  # noqa: E402
from revview.document import Document  # noqa: E402

OLD = r"""\documentclass{article}
\newcommand{\method}{FlowMap}
\begin{document}
\section{Introduction}
Finite volume methods are widely used for solid mechanics problems with large deformation.
The mesh quickly becomes distorted when the material undergoes severe plastic flow.
\todo{Explain why distortion matters for accuracy.}

\section{The \method{} Remap}
The \method{} remap transfers cell-centred fields from the distorted mesh to a smoothed mesh.
It is derived from an advection equation in pseudo-time and solved implicitly.
Downwind values are used at every face of the mesh.

\subsection{Rezone}
Each vertex is moved to the average of its neighbours until the quality stops improving.

\section{Results}
The remap is second-order accurate on smooth meshes and conserves the transported quantity.
\end{document}
"""

NEW = r"""\documentclass{article}
\newcommand{\method}{FlowMap}
\begin{document}
\section{Introduction}
Finite volume methods are widely used for solid mechanics problems with large deformation.
The mesh quickly becomes distorted when the material undergoes severe plastic flow,
which degrades the accuracy of the gradient reconstruction.

\section{The \method{} Remap}
The \method{} remap transfers cell-centred fields from the distorted mesh to a smoothed mesh.
It is derived from an advection equation in pseudo-time and solved implicitly.
Upwind values, taken from the cell the mesh moves away from, are used at every face.

\subsection{Rezone}
Each vertex is moved to the average of its neighbours until the quality stops improving.

\section{Results}
The remap is second-order accurate on smooth meshes and conserves the transported quantity.
A new convergence study on distorted meshes is added.
\end{document}
"""

FEEDBACK = """# Section 2

1. "Downwind values are used" - define downwind; do you mean upwind?
2. Section 2.1: is the smoothing order dependent?

# Results

3. Please add a convergence study on distorted meshes.
   Response: added at the end of Section 3.
"""


def make_case():
    d = tempfile.mkdtemp(prefix="revview_test_")
    for name, text in (("old", OLD), ("new", NEW)):
        with open(os.path.join(d, name + ".tex"), "w") as f:
            f.write(text.replace(r"\todo{Explain why distortion matters for accuracy.}",
                                 r"\todo{Explain why distortion matters for accuracy.}" if name == "old" else ""))
        # \todo is not defined in plain article: define it as a margin note for the commented copy
    with open(os.path.join(d, "old.tex")) as f:
        t = f.read().replace(r"\begin{document}", "\\newcommand{\\todo}[1]{\\marginpar{#1}}\n\\begin{document}")
    with open(os.path.join(d, "old.tex"), "w") as f:
        f.write(t)
    with open(os.path.join(d, "feedback.md"), "w") as f:
        f.write(FEEDBACK)
    return d


def compile_pdf(d, name):
    subprocess.run(["pdflatex", "-interaction=nonstopmode", name + ".tex"], cwd=d, check=True,
                   stdout=subprocess.DEVNULL)


def annotate(src, dst, page_quote):
    """Add a highlight with a comment over `page_quote` (located through the text layer)."""
    import pypdf
    from pypdf.annotations import Highlight, Text
    from pypdf.generic import ArrayObject, FloatObject
    doc = Document("old", "old", [src], tempfile.mkdtemp())
    words = page_quote.split()
    B = doc.pdf.boxes
    i = next(k for k in range(len(B)) if [b["t"] for b in B[k:k + len(words)]] == words)
    x0 = min(b["x0"] for b in B[i:i + len(words)]); x1 = max(b["x1"] for b in B[i:i + len(words)])
    y0 = min(b["y0"] for b in B[i:i + len(words)]); y1 = max(b["y1"] for b in B[i:i + len(words)])
    r = pypdf.PdfReader(src)
    w = pypdf.PdfWriter()
    w.append(r)
    H = float(w.pages[0].mediabox.top)
    rect = (x0, H - y1, x1, H - y0)
    quad = ArrayObject([FloatObject(v) for v in (x0, H - y0, x1, H - y0, x0, H - y1, x1, H - y1)])
    hl = Highlight(rect=rect, quad_points=quad)
    hl[pypdf.generic.NameObject("/Contents")] = pypdf.generic.TextStringObject("Too strong a claim?")
    w.add_annotation(page_number=0, annotation=hl)
    note = Text(rect=(50, 50, 70, 70), text="General: please proofread.")
    w.add_annotation(page_number=0, annotation=note)
    with open(dst, "wb") as f:
        w.write(f)


def test_all():
    d = make_case()
    try:
        compile_pdf(d, "new")
        # 1) TeX only for the commented version (compiled with latexmk), comments from a numbered list
        out = os.path.join(d, "site_static")
        res = build([os.path.join(d, "old.tex")], [os.path.join(d, "new.tex")],
                    comments=os.path.join(d, "feedback.md"), out=out, make_zip=True, log=open(os.devnull, "w"))
        assert res["state"] == "done" and res["comments"] == 3, res
        assert res["located_old"] == 3 and res["located_new"] == 3, res
        m = {x["id"]: x for x in json.load(open(os.path.join(out, ".revview", "matches.json")))}
        old = Document("old", "old", [os.path.join(d, "old.tex")], d)
        new = Document("new", "new", [os.path.join(d, "new.tex")], d)
        assert "Downwind" in old.by_id[m[0]["old"]["passage"]]["text"], m[0]
        assert "Upwind" in new.by_id[m[0]["new"]["passage"]]["text"], m[0]
        assert m[0]["status"] == "changed"
        assert any(w in old.by_id[m[1]["old"]["passage"]]["text"] for w in ("Rezone", "neighbours")), m[1]
        assert "convergence study" in new.by_id[m[2]["new"]["passage"]]["text"], m[2]
        assert os.path.isfile(out + ".zip")
        # determinism: a second run gives identical matches
        out2 = os.path.join(d, "site_static2")
        build([os.path.join(d, "old.tex")], [os.path.join(d, "new.tex")],
              comments=os.path.join(d, "feedback.md"), out=out2, log=open(os.devnull, "w"))
        assert open(os.path.join(out, ".revview", "matches.json")).read() == \
            open(os.path.join(out2, ".revview", "matches.json")).read()

        # 2) \todo macros in the TeX as the comment source
        items, how = comments.load(old, None, "tex")
        assert how == "TeX annotation macros" and len(items) == 1
        assert items[0]["old"]["passage"] and "distortion matters" in items[0]["reviewer_text"]

        # 3) PDF only on both sides, comments from PDF annotations
        old_pdf = os.path.join(d, "old_latex_annot.pdf")
        annotate(os.path.join(d, "site_static", ".revview", "old_latex", "old.pdf"), old_pdf,
                 "second-order accurate on smooth meshes")
        out3 = os.path.join(d, "site_pdf")
        res = build([old_pdf], [os.path.join(d, "new.pdf")], out=out3, log=open(os.devnull, "w"))
        assert res["comments"] == 2 and res["located_old"] == 2 and res["located_new"] >= 1, res
        a = json.load(open(os.path.join(out3, ".revview", "assembled.json")))
        hl = next(c for c in a if "Too strong" in c["reviewer_text"])
        assert "second-order" in hl["old"]["quote"]
        assert hl["match"]["new"] and hl["v3"]["page"] == 0

        # 4) agent mode: task files, then results (one valid, one missing -> static fallback)
        out4 = os.path.join(d, "site_agent")
        res = build([os.path.join(d, "old.tex")], [os.path.join(d, "new.tex")],
                    comments=os.path.join(d, "feedback.md"), out=out4, matcher="agent", log=open(os.devnull, "w"))
        assert res["state"] == "awaiting_agent" and res["chunks"] == 1
        task = os.path.join(out4, ".revview", "agent")
        recs = json.load(open(os.path.join(task, "chunks", "chunk_01.json")))
        assert [r["id"] for r in recs] == [0, 1, 2]
        p_old = m[0]["old"]["passage"]
        p_new = m[0]["new"]["passage"]
        with open(os.path.join(task, "results", "chunk_01.json"), "w") as f:
            json.dump({"matches": [{"id": 0, "old_passage": p_old, "old_quote": "Downwind values are used",
                                    "new_passage": p_new, "new_quote": "Upwind values", "status": "changed",
                                    "confidence": "high", "note": "downwind became upwind"}]}, f)
        res = build([os.path.join(d, "old.tex")], [os.path.join(d, "new.tex")],
                    comments=os.path.join(d, "feedback.md"), out=out4,
                    matches=os.path.join(task, "results"), log=open(os.devnull, "w"))
        assert res["state"] == "done" and res["located_old"] == 3
        a = json.load(open(os.path.join(out4, ".revview", "assembled.json")))
        assert a[0]["match"]["note"] == "downwind became upwind"
        assert a[1]["match"]["note"].startswith("(static)")
        assert len(a[0]["v2"]["rects"]) == 1  # the quote narrows the highlight to one line

        # 5) api mode with a mocked SDK
        calls = []

        class FakeStream:
            def __init__(self, kw):
                self.kw = kw

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def get_final_message(self):
                recs = json.loads(self.kw["messages"][0]["content"].split("\n\n", 1)[1])
                rows = [{"id": r["id"], "old_passage": r["hint"]["old_passage"], "old_quote": "",
                         "new_passage": r["hint"]["new_passage"], "new_quote": "", "status": "changed",
                         "confidence": "medium", "note": "mock"} for r in recs]
                usage = types.SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=100,
                                              cache_read_input_tokens=0)
                return types.SimpleNamespace(stop_reason="end_turn", usage=usage, content=[
                    types.SimpleNamespace(type="text", text=json.dumps({"matches": rows}))])

        class FakeClient:
            def __init__(self):
                self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=self.stream))

            def stream(self, **kw):
                calls.append(kw)
                return FakeStream(kw)

        fake = types.ModuleType("anthropic")
        fake.Anthropic = FakeClient
        for e in ("AuthenticationError", "BadRequestError", "RateLimitError", "APIConnectionError", "InternalServerError"):
            setattr(fake, e, type(e, (Exception,), {}))
        sys.modules["anthropic"] = fake
        try:
            out5 = os.path.join(d, "site_api")
            res = build([os.path.join(d, "old.tex")], [os.path.join(d, "new.tex")],
                        comments=os.path.join(d, "feedback.md"), out=out5, matcher="api", batch_size=2,
                        log=open(os.devnull, "w"))
        finally:
            del sys.modules["anthropic"]
        assert res["state"] == "done" and len(calls) == 2, (res, len(calls))
        kw = calls[0]
        assert kw["model"] == "claude-opus-5-5" and kw["fallbacks"] == "default"
        assert kw["output_config"]["format"]["type"] == "json_schema"
        assert kw["system"][1]["cache_control"] == {"type": "ephemeral"}
        assert all(c["match"]["note"] == "mock" for c in json.load(open(os.path.join(out5, ".revview", "assembled.json"))))
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    test_all()
    print("all tests passed")
