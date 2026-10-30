"""Command line: revview build | install-skill."""
import argparse
import os
import shlex
import shutil
import sys

from . import __version__
from .pipeline import build

SKILL_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skill", "SKILL.md")



def self_command():
    """How to invoke revview again: the installed script, or python -m from this checkout."""
    if shutil.which("revview"):
        return "revview"
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return f"PYTHONPATH={shlex.quote(root)} {shlex.quote(sys.executable)} -m revview"


def main(argv=None):
    ap = argparse.ArgumentParser(prog="revview", description=(
        "Side-by-side viewer of reviewer comments on a commented manuscript and its revision."))
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build the viewer", formatter_class=argparse.RawDescriptionHelpFormatter,
                       description="""Build the comparison viewer.

Inputs are one .tex and/or one .pdf per version (a .tex alone uses the PDF next to it, or is
compiled with latexmk; a .pdf alone works without sources). Comments come from --comments, or
else from the commented PDF's annotations, or else from \\todo/\\comment-style macros in its TeX.

Matchers:
  static  deterministic, offline (default)
  api     Claude API; needs `pip install anthropic` and ANTHROPIC_API_KEY (or `ant auth login`)
  agent   writes a task for Claude Code; let Claude do it, then rerun with --matches DIR""")
    b.add_argument("--commented", nargs="+", required=True, metavar="FILE", help="commented version: .tex and/or .pdf")
    b.add_argument("--revised", nargs="+", required=True, metavar="FILE", help="revised version: .tex and/or .pdf")
    b.add_argument("--comments", metavar="FILE", help="comments file (.md/.txt/.json)")
    b.add_argument("--comment-source", choices=["auto", "pdf", "tex"], default="auto",
                   help="where to look for comments when --comments is not given")
    b.add_argument("--macro", action="append", metavar="NAME", help="TeX comment macro name (repeatable)")
    b.add_argument("--matcher", choices=["static", "api", "agent"], default="static")
    b.add_argument("--matches", metavar="PATH", help="use existing match results (agent output dir or JSON)")
    b.add_argument("-o", "--out", default="revview_site", help="output directory (default: revview_site)")
    b.add_argument("--work", metavar="DIR", help="working directory (default: OUT/.revview)")
    b.add_argument("--labels", nargs=2, metavar=("OLD", "NEW"), help="version labels (default: file names)")
    b.add_argument("--title")
    b.add_argument("--dpi", type=int, default=150)
    b.add_argument("--format", choices=["webp", "png"], default="webp", help="page image format")
    b.add_argument("--zip", action="store_true", help="also write OUT.zip for sharing")
    g = b.add_argument_group("api matcher")
    g.add_argument("--model", help="Claude model (default: claude-opus-5-5)")
    g.add_argument("--effort", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    g.add_argument("--batch-size", type=int, default=10, help="comments per API request")
    g.add_argument("--jobs", type=int, default=4, help="parallel API requests")
    b.add_argument("--chunk-size", type=int, default=12, help="comments per agent chunk")

    s = sub.add_parser("install-skill", help="install the Claude Code skill (~/.claude/skills/revview)")
    s.add_argument("--dest", default=os.path.expanduser("~/.claude/skills/revview"))

    a = ap.parse_args(argv)
    if a.cmd == "install-skill":
        cmd = self_command()
        os.makedirs(a.dest, exist_ok=True)
        with open(SKILL_SRC, encoding="utf-8") as f:
            text = f.read().replace("{{REVVIEW}}", cmd)
        with open(os.path.join(a.dest, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Installed {os.path.join(a.dest, 'SKILL.md')} (runs `{cmd}`).\n"
              "In Claude Code: /revview, or ask to compare a commented manuscript with its revision.")
        return 0

    work = a.work or os.path.join(a.out, ".revview")
    rerun = ["build", "--commented", *a.commented, "--revised", *a.revised]
    if a.comments:
        rerun += ["--comments", a.comments]
    if a.labels:
        rerun += ["--labels", *a.labels]
    if a.title:
        rerun += ["--title", a.title]
    rerun += ["-o", a.out, "--matches", os.path.join(work, "agent", "results")] + (["--zip"] if a.zip else [])
    res = build(a.commented, a.revised, comments=a.comments, comment_source=a.comment_source, macros=a.macro,
                matcher=a.matcher, matches=a.matches, out=a.out, workdir=work, labels=a.labels or (None, None),
                title=a.title, dpi=a.dpi, image_format=a.format, make_zip=a.zip, model=a.model, effort=a.effort,
                batch_size=a.batch_size, jobs=a.jobs, chunk_size=a.chunk_size, build_cmd=self_command() + " " + shlex.join(rerun))
    return 0 if res["state"] in ("done", "awaiting_agent") else 1


if __name__ == "__main__":
    sys.exit(main())
