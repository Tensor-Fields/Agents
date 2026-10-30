"""Write the static viewer: index.html + data.js + rendered page images (+ optional zip)."""
import glob
import json
import os
import shutil
import subprocess
import zipfile
from concurrent.futures import ProcessPoolExecutor

TEMPLATE = os.path.join(os.path.dirname(__file__), "template", "index.html")


def _convert(job):
    src, dst, fmt, quality = job
    if fmt == "webp":
        from PIL import Image
        Image.open(src).save(dst, "WEBP", quality=quality, method=4)
        os.remove(src)
    else:
        os.replace(src, dst)


def render_pages(pdf_path, outdir, dpi=150, fmt="webp", quality=80):
    """PDF pages -> outdir/p-1.<fmt>, p-2.<fmt>, ... ; skipped when already rendered from the same PDF."""
    st = os.stat(pdf_path)
    stamp = f"{os.path.abspath(pdf_path)}|{st.st_mtime_ns}|{st.st_size}|{dpi}|{fmt}|{quality}"
    stamp_file = os.path.join(outdir, ".stamp")
    if os.path.isfile(stamp_file) and open(stamp_file).read() == stamp:
        return fmt
    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir)
    if not shutil.which("pdftoppm"):
        raise SystemExit("pdftoppm (poppler-utils) is required to render pages")
    subprocess.run(["pdftoppm", "-r", str(dpi), "-png", pdf_path, os.path.join(outdir, "raw")], check=True)
    if fmt == "webp":
        try:
            from PIL import Image
        except ImportError:
            fmt = "png"
    jobs = [(f, os.path.join(outdir, f"p-{int(os.path.basename(f)[4:-4])}.{fmt}"), fmt, quality)
            for f in glob.glob(os.path.join(outdir, "raw-*.png"))]
    with ProcessPoolExecutor() as ex:
        list(ex.map(_convert, jobs))
    with open(stamp_file, "w") as fh:
        fh.write(stamp)
    return fmt


def slim_anchor(a):
    return {k: v for k, v in a.items() if k in ("src", "page", "rects", "approx")} if a else None


def write(out, old, new, comments, title, dpi=150, fmt="webp", make_zip=False, log=None):
    os.makedirs(out, exist_ok=True)
    if log:
        print(f"Rendering pages ({dpi} dpi, {fmt})...", file=log)
    f1 = render_pages(old.pdf_path, os.path.join(out, "old_pages"), dpi, fmt)
    f2 = render_pages(new.pdf_path, os.path.join(out, "new_pages"), dpi, fmt)
    sections = []
    for c in comments:
        if c["section"] not in sections:
            sections.append(c["section"])
    config = {"title": title, "oldLabel": old.label, "newLabel": new.label, "imageExt": f1 if f1 == f2 else f1,
              "sections": sections}
    slim = []
    for c in comments:
        slim.append({"id": c["id"], "section": c["section"], "status": c["status"],
                     "reviewer_text": c["reviewer_text"], "reviewer_html": c["reviewer_html"],
                     "responses": [{"color": r["color"], "text": r["text"], "html": r["html"]} for r in c["responses"]],
                     "v2": slim_anchor(c.get("v2")), "v3": slim_anchor(c.get("v3")),
                     "match_status": c.get("match_status", "")})
    with open(os.path.join(out, "data.js"), "w", encoding="utf-8") as f:
        f.write("const CONFIG = " + json.dumps(config, ensure_ascii=False) + ";\n")
        f.write("const OLD_PAGE_META = " + json.dumps(old.pdf.page_meta()) + ";\n")
        f.write("const NEW_PAGE_META = " + json.dumps(new.pdf.page_meta()) + ";\n")
        f.write("const COMMENTS = " + json.dumps(slim, ensure_ascii=False) + ";\n")
    shutil.copy(TEMPLATE, os.path.join(out, "index.html"))
    if make_zip:
        zpath = out.rstrip("/") + ".zip"
        base = os.path.basename(out.rstrip("/"))
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for root, dirs, files in os.walk(out):
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for fn in files:
                    if fn.startswith("."):
                        continue
                    full = os.path.join(root, fn)
                    z.write(full, os.path.join(base, os.path.relpath(full, out)))
        return zpath
    return None
