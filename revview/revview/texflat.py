"""Flatten a LaTeX project into one line list, remembering each line's source file:line."""
import os
import re

INPUT_RE = re.compile(r"^(?P<pre>[^%]*?)\\(?:input|include|subfile)\{(?P<name>[^}]+)\}")


def _resolve(name, base_dir):
    for cand in (name, name + ".tex"):
        p = os.path.join(base_dir, cand)
        if os.path.isfile(p):
            return p
    return None


def flatten(main_path):
    """Return (lines, src) where src[i] = (relative file, 1-based line) of lines[i].

    \\input/\\include/\\subfile on their own line are replaced by the included file,
    wrapped in '% >>> begin/end inlined: <file> <<<' marker lines."""
    main_path = os.path.abspath(main_path)
    root = os.path.dirname(main_path)
    lines, src = [], []
    stack = []

    def walk(path):
        if path in stack:
            return
        stack.append(path)
        rel = os.path.relpath(path, root)
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read().split("\n")
        for no, ln in enumerate(text, 1):
            m = INPUT_RE.match(ln)
            inc = _resolve(m.group("name").strip(), root) if m and not m.group("pre").strip() else None
            if inc:
                increl = os.path.relpath(inc, root)
                lines.append(f"% >>> begin inlined: {increl} <<<")
                src.append((rel, no))
                walk(inc)
                lines.append(f"% >>> end inlined: {increl} <<<")
                src.append((rel, no))
            else:
                lines.append(ln)
                src.append((rel, no))
        stack.pop()

    walk(main_path)
    return lines, src
