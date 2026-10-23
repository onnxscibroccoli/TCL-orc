#!/usr/bin/env python3
"""Check that every math expression in the Markdown docs parses.

Extracts GitHub math (```math fences, $$ display blocks, $`...`$ and $...$
inline) from README.md and docs/**/*.md, then renders each one with KaTeX
(throwOnError) through Node. KaTeX is stricter than GitHub's MathJax, so a
clean KaTeX pass is a good proxy for "renders on GitHub".

Usage: python3 scripts/check_math.py [--strict] [files...]
  KATEX_DIR=/path/containing/node_modules/katex  (or katex resolvable by node)
  --strict  fail if node/katex is unavailable (default: skip with exit 0)

Also lints for the pitfalls listed in docs/STYLE_MATH.md.
"""
from __future__ import annotations
import json, os, re, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def extract(path: Path):
    items, lint = [], []
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    i, n = 0, len(lines)
    while i < n:
        line = lines[i]
        m = re.match(r"^(\s*)(```+|~~~+)\s*(\w*)", line)
        if m:
            fence, lang = m.group(2), m.group(3)
            j = i + 1
            while j < n and not lines[j].strip().startswith(fence):
                j += 1
            body = "\n".join(lines[i + 1:j])
            if lang == "math":
                items.append((i + 1, body, True))
            i = j + 1
            continue
        if line.strip() == "$$":
            j = i + 1
            while j < n and lines[j].strip() != "$$":
                j += 1
            items.append((i + 1, "\n".join(lines[i + 1:j]), True))
            lint.append((i + 1, "use ```math fence instead of $$ block"))
            i = j + 1
            continue
        # inline: strip code spans that are not $`..`$
        for mm in re.finditer(r"\$`(.+?)`\$", line):
            items.append((i + 1, mm.group(1), False))
        rest = re.sub(r"\$`.+?`\$", " ", line)
        rest = re.sub(r"`[^`]*`", " ", rest)
        rest = rest.replace(r"\$", " ")
        for mm in re.finditer(r"(?<![\w$])\$(?!\s)([^$]+?)(?<!\s)\$(?![\w$])", rest):
            items.append((i + 1, mm.group(1), False))
        i += 1
    return items, lint

JS = r"""
const katex = require(process.env.KATEX_DIR ? process.env.KATEX_DIR + '/node_modules/katex' : 'katex');
let data = JSON.parse(require('fs').readFileSync(0, 'utf8'));
let out = data.map(([f, l, src, disp]) => {
  try { katex.renderToString(src, {displayMode: disp, throwOnError: true, strict: 'ignore', trust: false}); return null; }
  catch (e) { return [f, l, src, String(e.message)]; }
}).filter(Boolean);
process.stdout.write(JSON.stringify(out));
"""

def main(argv):
    strict = "--strict" in argv
    files = [Path(a) for a in argv if not a.startswith("--")]
    if not files:
        files = [ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md"))]
    payload, lints = [], []
    for f in files:
        items, lint = extract(f)
        rel = os.path.relpath(f, ROOT)
        payload += [[rel, l, s, d] for (l, s, d) in items]
        lints += [f"{rel}:{l}: {msg}" for (l, msg) in lint]
    print(f"math expressions found: {len(payload)} "
          f"(display {sum(1 for p in payload if p[3])}, inline {sum(1 for p in payload if not p[3])})")
    for x in lints:
        print("LINT", x)
    node = shutil.which("node")
    if not node:
        print("node not found; skipping render check"); return 1 if strict else 0
    r = subprocess.run([node, "-e", JS], input=json.dumps(payload), capture_output=True, text=True)
    if r.returncode != 0:
        print("katex unavailable:", r.stderr.strip().splitlines()[-1:] )
        return 1 if strict else 0
    errs = json.loads(r.stdout)
    for f, l, s, e in errs:
        print(f"FAIL {f}:{l}: {e}\n    {s[:160]!r}")
    print(f"render failures: {len(errs)}")
    return 1 if (errs or lints) else 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
