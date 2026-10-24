# Math style guide

How every formula in this repository is written so that it renders on GitHub like a printed textbook, not like code.

## Why this standard

GitHub renders LaTeX math in Markdown files, issues and pull requests with MathJax ([GitHub docs](https://docs.github.com/en/get-started/writing-on-github/working-with-advanced-formatting/writing-mathematical-expressions)).
It accepts four delimiters. We use the two that cannot be mangled by the Markdown parser:

| Use | Write | Do not write |
| --- | --- | --- |
| Display (own line) equation | a ` ```math ` fenced block | <code>&#36;&#36; … &#36;&#36;</code> |
| Inline formula inside a sentence | <code>&#36;&#96;…&#96;&#36;</code> | `` `x = y` `` (code span) or bare ASCII |

Plain <code>&#36;…&#36;</code> is tolerated for a single symbol with no backslash, underscore, asterisk or brace (for example <code>&#36;T&#36;</code>, <code>&#36;B&#36;</code>), because there is nothing for Markdown to misread.

## Known GitHub pitfalls this avoids

1. **Markdown runs before MathJax** in <code>&#36;…&#36;</code> and <code>&#36;&#36;…&#36;&#36;</code>: `_` and `*` can turn into italics, `\\` collapses to `\`, `\{` loses its backslash. Inside a math fence and the dollar-backtick inline form the text is passed through verbatim.
2. **<code>&#36;&#36;</code> blocks break inside lists, tables and blockquotes**, and need blank lines around them. ` ```math ` fences always work.
3. **A dollar sign next to a word or number** may or may not be read as a delimiter, depending on context. Write currency as `USD 5`, or <code>\&#36;</code> inside math, or <code>&lt;span&gt;&#36;&lt;/span&gt;</code> in prose.
4. **`|` inside a table cell** ends the cell even inside math. Use `\mid`, `\lvert…\rvert` or `\vert` in tables.
5. **Multi-line display math** needs one environment: wrap rows in `\begin{aligned}…\end{aligned}` or `\begin{cases}…\end{cases}`; a bare `\\` outside an environment is ignored.
6. **`\tag`** goes outside `aligned` (one tag per block), at the end of the block.

## Typesetting rules

- Words and multi-letter names are upright: `\mathrm{fee}`, `\operatorname{Poseidon}`, `\text{if }`. Never write `fee` bare (it typesets as $f \cdot e \cdot e$).
- Multiplication is `\cdot` (or juxtaposition); never `*`. Use `\times` only for scientific notation, e.g. $`5.788 \times 10^{-9}`$.
- Use `\le`, `\ge`, `\ne`, `\lt`, `\gt`, `\in`, `\to`, `\Rightarrow`; never `<=`, `>=`, `!=`, `->`.
- Big brackets scale: `\left( … \right)`, `\bigl( … \bigr)`; floor/ceil with `\lfloor … \rfloor`, `\lceil … \rceil`.
- Thousands separators are thin spaces: `5\,000\,000`.
- Units are upright after a thin space: `0.0005\ \mathrm{day}^{-1}`, `86\,400\ \mathrm{s}`.
- Piecewise definitions use `cases`; chains of equalities use `aligned` with `&=`.
- Number an equation with `\tag{section.n}` only when the text refers to it.
- Code identifiers (`Ledger.settle`, `VAULT_ID()`) stay in code spans, in prose, never inside math. Solidity/Python expressions that are literally code (bit shifts, `address(this).balance`) stay as code.

## Every equation is followed by its symbols

Right after each display equation, add a `where …` sentence or list naming every symbol not already defined in that section.
All symbols share one meaning across the repository; see [NOTATION.md](NOTATION.md). Add new symbols there first.

## Checking

```bash
npm install --prefix /tmp/katex katex@0.16      # once, offline afterwards
KATEX_DIR=/tmp/katex python3 scripts/check_math.py --strict
```

The script finds every math expression in `README.md` and `docs/`, renders each with KaTeX (`throwOnError`), and lints for <code>&#36;&#36;</code> blocks.
KaTeX is stricter than MathJax, so passing it is a good proxy for rendering on GitHub.
Without Node/KaTeX it skips (exit 0) unless `--strict` is passed, so it is safe to wire into CI as optional.
To see GitHub's own rendering of a file: <code>gh api markdown -f mode=gfm -f text="&#36;(cat docs/FILE.md)"</code>.
