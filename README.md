# AddTOC

Heuristic PDF analysis and Table of Contents (bookmarks) injection using PyMuPDF.

## Prerequisites

- Python 3.10 or newer

## Virtual environment

1. Create a virtual environment in the project directory (folder stays `.venv`; the shell prompt shows `(addtoc)` instead of `(.venv)`):

   ```bash
   python3 -m venv .venv --prompt addtoc
   ```

2. Activate it:

   - **macOS / Linux:** `source .venv/bin/activate`
   - **Windows (cmd):** `.venv\Scripts\activate.bat`
   - **Windows (PowerShell):** `.\.venv\Scripts\Activate.ps1`

3. Install dependencies:

   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

   This installs **PyMuPDF** (import name: `fitz`).

## Run

```bash
python addtoc.py "samples/Pintos Projects Introduction.pdf"
```

Behavior:

- Detects heading levels from font-size distribution (statistical grouping).
- Auto-calculates heading thresholds and number of levels.
- Refines deeper levels using indentation for same-size candidate headings.
- Prints a TOC preview before writing output with aligned style markers:
  - Example: `[15.4 B  ]` (bold), `[10.3  IU]` (italic + underline).
- Shows explicit hierarchy labels on every line (`L1`, `L2`, ...).
- Numbers each preview entry for interactive blocking.
- Shows one entry per line with hierarchy indentation (each line uses a `*` bullet after the `L<n>` label).
- Truncates each preview line to 80 characters.
- Uses ANSI colors on interactive CLI output when stdout is a terminal; set `NO_COLOR` to disable.
- Lets you iteratively adjust thresholds, relax rules, and apply filters until preview looks right.
- If hierarchy gaps are detected at save time, auto-realigns by rebuilding levels from font-size tiers (largest size = shallowest), **never deepening** vs the preview heuristic, then enforcing valid outline steps; shows a warning and the adjusted preview: a **cut** uses one leading `<` whose hyphen run lengthens with how many levels were removed; a **deepen** (if ever shown) uses a lengthening `--…>` run immediately before the `*`. The `*` stays at the new outline indent.
- Asks for confirmation before writing (unless `--yes` is used).
- Saved bookmarks target each heading's exact span origin (`x`,`y`) instead of page top.
- If saving fails, it returns to interactive mode so you can fix and retry.
- Keyboard handling:
  - `Ctrl-C` clears current input and reprompts.
  - `Ctrl-D` exits interactive mode gracefully.

Useful flags:

```bash
# Output path is optional; default is "<input_stem>.with-toc.pdf"
python addtoc.py "input.pdf"

# Show preview only (no output written)
python addtoc.py "input.pdf" --preview-only

# Explicit output path (optional)
python addtoc.py "input.pdf" "output.pdf"

# Skip confirmation prompt
python addtoc.py "input.pdf" --yes

# Start from manual thresholds
python addtoc.py "input.pdf" --thresholds "20.0,16.0,14.5"

# Replace existing PDF outlines if present
python addtoc.py "input.pdf" --force

# Dump font-size/style/indent groups for debugging heuristics
python addtoc.py "input.pdf" --preview-only --yes --dump-font-groups
```

In interactive mode, use commands like:

- `relax bold` / `unrelax bold` (aliases: `more` / `less`)
- `relax italics color`
- `revert` (undo last relax/tighten step)
- `relax list` (or `more list`)
- `filter add + regex "^\d+(?:\.\d+){0,6}\.?(?:\s+.*)?$"`
- `filter update 2 - exact "Data Structures"`
- `filter add - exact "Data Structures"`
- `preset list`
- `preset use` (prompt for number or name) or `preset use deep-numbering`
- `block 12` (blacklist exact title from current numbered preview)

Notes:

- Whitelist filters (`+`) are applied first, then blacklist filters (`-`).
- In `filter list`, preset-backed filters are labeled (for example: `preset:deep-numbering`).
- Informational commands like `help`, `preset list`, `filter list`, and `relax list` do not reprint preview.

## Tests

Run tests from the project root:

```bash
python -m unittest discover -s tests -p "test_*.py"
```

The first integration test case uses:

- `samples/Pintos Projects Introduction.pdf`
