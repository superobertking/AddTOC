#!/usr/bin/env python3
# Author: robertking
# addtoc.py - PDF Heuristic & Structure Engine
# Vibe coded with Gemini and Cursor

import argparse
import os
import re
import shlex
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import List, Optional, Tuple

import fitz  # PyMuPDF
import numpy as np

# Bookmark Y (see .cursorrules): PyMuPDF span bbox y is y-down (larger y = lower on page).
# Top of the heading is min(y0, y1). In that space, *adding* y moves *down*, so +margin
# would land below the title top—wrong. Use y_top - margin to nudge slightly *up*.
TOC_DEST_TOP_MARGIN_PT = 3.0


class TerminalColors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    FG_CYAN = "\033[36m"
    FG_YELLOW = "\033[33m"
    FG_GREEN = "\033[32m"
    FG_RED = "\033[31m"
    FG_BLUE = "\033[34m"

def _terminal_color_enabled() -> bool:
    if os.environ.get("NO_COLOR", "").strip():
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    try:
        return sys.stdout.isatty()
    except (ValueError, AttributeError):
        return False

def _style(text: str, *sgr: str) -> str:
    if not text or not _terminal_color_enabled():
        return text
    return "".join(sgr) + text + TerminalColors.RESET

def _make_styler(*sgr: str):
    def styler(text: str) -> str:
        return _style(text, *sgr)
    return styler

_hdr  = _make_styler(TerminalColors.BOLD, TerminalColors.FG_CYAN)
_cmd  = _make_styler(TerminalColors.FG_YELLOW)
_dim  = _make_styler(TerminalColors.DIM)
_ok   = _make_styler(TerminalColors.FG_GREEN)
_warn = _make_styler(TerminalColors.BOLD, TerminalColors.FG_YELLOW)
_err  = _make_styler(TerminalColors.BOLD, TerminalColors.FG_RED)
_lbl  = _make_styler(TerminalColors.BOLD, TerminalColors.FG_BLUE)
_info = _make_styler(TerminalColors.FG_CYAN)

def lbl(msg: str) -> None: print(_lbl(msg))
def hdr(msg: str) -> None: print(_hdr(msg))
def dim(msg: str) -> None: print(_dim(msg))
def ok(msg: str) -> None: print(_ok(msg))
def warn(msg: str) -> None: print(_warn(msg))
def err(msg: str) -> None: print(_err(msg))
def info(msg: str) -> None: print(_info(msg))


def _print_help_row(cmd: str, desc: str, width: int) -> None:
    if _terminal_color_enabled():
        pad = max(0, width - len(cmd) + 2)
        print(f"  {_cmd(cmd)}{' ' * pad}{_dim(desc)}")
    else:
        print(f"  {cmd:<{width}}  {desc}")


def _bookmark_y_from_span(span: dict) -> float:
    """Vertical anchor: top of span (min y in y-down bbox space), minus a small margin."""
    bbox = span.get("bbox")
    if bbox is not None and len(bbox) >= 4:
        y0, y1 = float(bbox[1]), float(bbox[3])
        y_top = min(y0, y1)
        return y_top - TOC_DEST_TOP_MARGIN_PT
    origin = span.get("origin", (0.0, 0.0))
    size = float(span.get("size", 10.0))
    return float(origin[1]) - size * 0.85 - TOC_DEST_TOP_MARGIN_PT


# --- Data structures ---------------------------------------------------------


@dataclass(frozen=True)
class SpanRecord:
    """One text span extracted from PDF content streams."""

    page: int
    text: str
    size: float
    font: str
    flags: int
    color: int
    x: float
    y: float
    line_key: Tuple[int, int, int]
    order: Tuple[int, int, int, int]

    def is_bold_font(self) -> bool:
        return "bold" in (self.font or "").lower()

    def is_italic_font(self) -> bool:
        fn = (self.font or "").lower()
        return "italic" in fn or "oblique" in fn

    def is_underlined_font(self) -> bool:
        return "underline" in (self.font or "").lower()

    def format_style_marks(self) -> str:
        return "".join(
            [
                "B" if self.is_bold_font() else " ",
                "I" if self.is_italic_font() else " ",
                "U" if self.is_underlined_font() else " ",
            ]
        )

    @classmethod
    def estimate_body_font_size(cls, records: List["SpanRecord"]) -> float:
        if not records:
            return 0.0
        weighted_counts: dict[float, float] = {}
        for rec in records:
            rounded_size = round(rec.size, 1)
            weighted_counts[rounded_size] = weighted_counts.get(rounded_size, 0.0) + len(rec.text)
        return max(weighted_counts, key=weighted_counts.get)

    @classmethod
    def auto_calculate_thresholds(cls, records: List["SpanRecord"], max_levels=4) -> List[float]:
        if not records:
            return []
        body_size = cls.estimate_body_font_size(records)
        rounded_sizes = np.array([round(rec.size, 1) for rec in records], dtype=float)
        unique_sizes, counts = np.unique(rounded_sizes, return_counts=True)
        size_to_count = {float(size): int(count) for size, count in zip(unique_sizes, counts)}
        candidate_sizes = sorted(
            (size for size in size_to_count if size > body_size + 0.6),
            reverse=True,
        )
        if not candidate_sizes:
            return []
        grouped = []
        for size in candidate_sizes:
            if not grouped or grouped[-1] - size >= 0.8:
                grouped.append(size)
        filtered = []
        for size in grouped:
            if size >= body_size + 2.0 or size_to_count.get(size, 0) >= 2:
                filtered.append(size)
        return filtered[:max_levels] if filtered else grouped[:max_levels]

@dataclass
class TocEntry:
    """A candidate heading / bookmark line after heuristics."""

    level: int
    title: str
    page: int
    size: float
    x: float
    y: float
    style: str
    source_level: int
    level_reason: str

    def preview_line(self, width: int = 80, index: Optional[int] = None) -> str:
        prefix = f'{"  " * (self.level - 1)}* '
        line = f"[{self.size:>4.1f} {self.style}] L{self.level} {prefix}{self.title} (p. {self.page})"
        if index is not None:
            line = f"{index:>3}. {line}"
        if len(line) <= width:
            return line
        if width <= 3:
            return "." * width
        return f"{line[: width - 3]}..."

    def to_save_item(self) -> "TocSaveItem":
        return TocSaveItem(level=self.level, title=self.title, page=self.page, x=float(self.x), y=float(self.y))

    @classmethod
    def render_preview(cls, toc_entries: List["TocEntry"], width: int = 80) -> str:
        items = list(toc_entries)
        return "\n".join(entry.preview_line(width, idx) for idx, entry in enumerate(items, start=1))

    @classmethod
    def validate_hierarchy(cls, entries: List["TocEntry"]) -> List["HierarchyIssue"]:
        items = list(entries)
        issues: List[HierarchyIssue] = []
        prev_level = None
        for idx, entry in enumerate(items, start=1):
            level = entry.level
            if idx == 1 and level != 1:
                issues.append(
                    HierarchyIssue(
                        row=idx,
                        level=level,
                        prev_level=None,
                        reason="first_row_must_be_level_1",
                        entry=entry,
                    )
                )
            if prev_level is not None and level > prev_level + 1:
                issues.append(
                    HierarchyIssue(
                        row=idx,
                        level=level,
                        prev_level=prev_level,
                        reason="level_jump_too_large",
                        entry=entry,
                    )
                )
            prev_level = level
        return issues

    # NOTE: this function is not used now. maybe provide a mode to use this in the future.
    @staticmethod
    def _outline_levels_from_font_sizes(entries: List["TocEntry"]) -> List[int]:
        """
        Map each entry to a 1-based tier from rounded font size: largest size on the page
        becomes tier 1 (top-level), next smaller tier 2, etc. Same size => same tier.
        """
        if not entries:
            return []
        rounded = [round(e.size, 1) for e in entries]
        unique_desc = sorted(set(rounded), reverse=True)
        tier_of = {s: i for i, s in enumerate(unique_desc)}
        return [tier_of[r] + 1 for r in rounded]

    # TODO: we could instead just remove all the dangling levels. maybe provide
    # this mode in the future.

    @staticmethod
    def _outline_compact_levels(entries: List["TocEntry"]) -> List[int]:
        """
        Compact the levels of the entries into a single list of levels.
        """
        if not entries:
            return []
        levels = sorted(set(entry.level for entry in entries))
        level_map = {level: i + 1 for i, level in enumerate(levels)}
        return [level_map[entry.level] for entry in entries]


    @staticmethod
    def _repair_outline_levels(raw: List[int]) -> List[int]:
        """
        Turn font-tier depths into a valid PDF outline sequence (each step down by at most
        one). Consecutive equal raw tiers stay equal (siblings); never uses the previous row's
        *heuristic* level—only the previous *output* level and raw tier.
        First row is always 1 (PDF outline convention).
        """
        if not raw:
            return []
        out: List[int] = [1]
        for i in range(1, len(raw)):
            if raw[i] == raw[i - 1]:
                out.append(out[-1])
            else:
                out.append(min(raw[i], out[-1] + 1))
        return out

    @classmethod
    def realign_for_save(
        cls, entries: List["TocEntry"]
    ) -> Tuple[List["TocEntry"], List["LevelAdjustment"]]:
        """
        Rebuild outline levels from font-size tiers, then enforce valid one-step nesting.
        This avoids bogus jumps from the heading heuristic (e.g. after blocking rows) and keeps
        headings with the same font size at the same outline level when consecutive.
        """
        items = list(entries)
        if not items:
            return [], []
        raw = cls._outline_compact_levels(items)
        # raw = cls._outline_levels_from_font_sizes(items)
        new_levels = cls._repair_outline_levels(raw)
        adjusted_entries: List[TocEntry] = []
        adjustments: List[LevelAdjustment] = []
        for idx, (entry, new_level) in enumerate(zip(items, new_levels), start=1):
            original_level = entry.level
            adjusted_entry = replace(entry, level=new_level)
            adjusted_entries.append(adjusted_entry)
            if new_level != original_level:
                adjustments.append(
                    LevelAdjustment(
                        row=idx,
                        from_level=original_level,
                        to_level=new_level,
                        title=entry.title,
                    )
                )
        return adjusted_entries, adjustments

    @classmethod
    def print_diagnostics(cls, entries: List["TocEntry"], max_rows: int = 20) -> None:
        items = list(entries)
        hdr("Heuristic hierarchy assignment (row, level, reason):")
        for idx, entry in enumerate(items[:max_rows], start=1):
            reason = entry.level_reason or "n/a"
            print(
                f"  {_dim(f'row {idx:>3}:')} {_info(f'L{entry.level}')} {_dim('|')} "
                f"{reason} {_dim('|')} {entry.title} (p. {entry.page})"
            )
        if len(items) > max_rows:
            dim(f"  ... {len(items) - max_rows} more rows omitted")


@dataclass
class Relaxations:
    bold: bool = False
    italics: bool = False
    color: bool = False

@dataclass
class FilterRule:
    id: int
    action: str  # "+" whitelist, "-" blacklist
    mode: str  # "exact" or "regex"
    pattern: str
    preset: Optional[str] = None

    def matches(self, title: str) -> bool:
        if self.mode == "exact":
            return title == self.pattern
        return re.search(self.pattern, title) is not None

    @classmethod
    def apply_to_entries(cls, entries: List["TocEntry"], filters: List["FilterRule"]) -> List["TocEntry"]:
        if not filters:
            return entries
        allow_rules = [f for f in filters if f.action == "+"]
        deny_rules = [f for f in filters if f.action == "-"]
        if allow_rules:
            entries = [e for e in entries if any(rule.matches(e.title) for rule in allow_rules)]
        return [e for e in entries if not any(rule.matches(e.title) for rule in deny_rules)]

    @classmethod
    def create(
        cls, next_filter_id: int, action: str, mode: str, pattern: str, preset: Optional[str] = None
    ) -> "FilterRule":
        if action not in {"+", "-"}:
            raise ValueError("Action must be '+' or '-'.")
        if mode not in {"exact", "regex"}:
            raise ValueError("Mode must be 'exact' or 'regex'.")
        if not pattern:
            raise ValueError("Pattern must be non-empty.")
        if mode == "regex":
            re.compile(pattern)
        return cls(id=next_filter_id, action=action, mode=mode, pattern=pattern, preset=preset)


@dataclass
class HierarchyIssue:
    row: int
    level: int
    prev_level: Optional[int]
    reason: str
    entry: TocEntry


@dataclass
class LevelAdjustment:
    row: int
    from_level: int
    to_level: int
    title: str


@dataclass(frozen=True)
class TocSaveItem:
    """Payload for one outline row with exact destination coordinates."""

    level: int
    title: str
    page: int
    x: float
    y: float

    @classmethod
    def build_sequence(cls, entries: List[TocEntry]) -> List["TocSaveItem"]:
        return [e.to_save_item() for e in entries]


# --- PDF / text extraction ---------------------------------------------------


def get_existing_toc(input_path):
    """Return current TOC entries from a PDF."""
    doc = fitz.open(input_path)
    try:
        return doc.get_toc()
    finally:
        doc.close()


def collect_spans(input_path) -> List[SpanRecord]:
    """Collect text spans with line grouping metadata."""
    doc = fitz.open(input_path)
    records: List[SpanRecord] = []

    try:
        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("dict")["blocks"]

            for block_idx, block in enumerate(blocks):
                if "lines" not in block:
                    continue

                for line_idx, line in enumerate(block["lines"]):
                    line_key = (page_num + 1, block_idx, line_idx)
                    for span_idx, span in enumerate(line["spans"]):
                        text = span["text"].strip()
                        if len(text) <= 1:
                            continue
                        origin = span.get("origin", (0.0, 0.0))
                        records.append(
                            SpanRecord(
                                page=page_num + 1,
                                text=text,
                                size=float(span["size"]),
                                font=span.get("font", ""),
                                flags=int(span.get("flags", 0)),
                                color=int(span.get("color", 0)),
                                x=float(origin[0]),
                                y=_bookmark_y_from_span(span),
                                line_key=line_key,
                                order=(page_num, block_idx, line_idx, span_idx),
                            )
                        )
    finally:
        doc.close()

    # assume single column pages for now.
    records.sort(key=lambda r: (r.page, r.y, r.x, r.order))
    # TODO: For multi column pages, we need to sort the records by page, rounded(x), y, and order.

    return records


def classify_level_by_thresholds(size, thresholds):
    """Map a font size to heading level using descending thresholds."""
    for idx, threshold in enumerate(thresholds, start=1):
        if size >= threshold:
            return idx
    return None


def build_toc_entries(
    records: List[SpanRecord],
    thresholds,
    relaxations: Optional[Relaxations] = None,
    filters: Optional[List[FilterRule]] = None,
) -> List[TocEntry]:
    """
    Build TOC entries from span records and thresholds.
    """
    spans = list(records)
    relax = relaxations or Relaxations()
    filter_rules = list(filters) if filters else []

    entries: List[TocEntry] = []
    seen = set()
    current_line_key = None
    line_selected = False
    body_size = SpanRecord.estimate_body_font_size(spans)
    min_heading_size = min(thresholds) if thresholds else body_size + 0.6

    for rec in spans:
        if rec.line_key != current_line_key:
            current_line_key = rec.line_key
            line_selected = False

        if line_selected:
            continue

        level = classify_level_by_thresholds(rec.size, thresholds)
        looks_like_heading_length = len(rec.text) <= 120 and len(rec.text.split()) <= 16
        near_body_size = rec.size >= body_size - 0.25 and rec.size < min_heading_size
        can_relax = looks_like_heading_length and near_body_size
        relaxed_match = can_relax and (
            (relax.bold and rec.is_bold_font())
            or (relax.italics and rec.is_italic_font())
            or (relax.color and rec.color != 0)
        )

        if level is None and not relaxed_match:
            continue
        selection_reason = ""
        source_level = level
        if level is None:
            level = len(thresholds) + 1
            if relax.bold and rec.is_bold_font():
                selection_reason = "relax:bold"
            elif relax.italics and rec.is_italic_font():
                selection_reason = "relax:italics"
            elif relax.color and rec.color != 0:
                selection_reason = "relax:color"
            else:
                selection_reason = "relax:other"
        else:
            selection_reason = f"size>={thresholds[level - 1]:.1f}"

        key = (level, rec.text, rec.page)
        if key in seen:
            line_selected = True
            continue

        sl = source_level if source_level is not None else level
        entries.append(
            TocEntry(
                level=level,
                title=rec.text,
                page=rec.page,
                size=rec.size,
                x=rec.x,
                y=rec.y,
                style=rec.format_style_marks(),
                source_level=sl,
                level_reason=selection_reason,
            )
        )
        seen.add(key)
        line_selected = True

    return FilterRule.apply_to_entries(apply_indent_refinement(entries), filter_rules)


def apply_indent_refinement(entries: List[TocEntry], tolerance=4.0) -> List[TocEntry]:
    """
    Refine hierarchy using x-origin indentation clusters.
    Same-size headings often use indentation for deeper levels.
    """
    if not entries:
        return entries

    grouped_x: dict[int, List[float]] = {}
    for entry in entries:
        grouped_x.setdefault(entry.level, []).append(entry.x)

    level_clusters: dict[int, List[float]] = {}
    for level, x_values in grouped_x.items():
        clusters = []
        for x in sorted(x_values):
            if not clusters or x - clusters[-1] >= tolerance:
                clusters.append(x)
        level_clusters[level] = clusters

    refined: List[TocEntry] = []
    for entry in entries:
        base_level = entry.level
        clusters = level_clusters.get(base_level, [entry.x])
        indent_level = 0
        for idx, x in enumerate(clusters):
            if entry.x >= x - 0.01:
                indent_level = idx
        new_level = base_level + indent_level
        if indent_level > 0:
            reason = f"{entry.level_reason} + indent@x={entry.x:.1f} (+{indent_level})"
        else:
            reason = f"{entry.level_reason} + indent@base"
        refined.append(
            replace(entry, level=new_level, level_reason=reason),
        )

    return refined


def find_headers(input_path, thresholds=None):
    """
    Scan a PDF and return TOC entries as [level, title, page_number].
    """
    records = collect_spans(input_path)
    final_thresholds = thresholds or SpanRecord.auto_calculate_thresholds(records)
    entries = build_toc_entries(records, final_thresholds)
    return [[e.level, e.title, e.page] for e in entries]


def render_toc_preview(toc_entries: List[TocEntry], width=80) -> str:
    """Return printable preview text (one TOC entry per line)."""
    return TocEntry.render_preview(toc_entries, width)


def _toc_realign_preview_line(
    entry: TocEntry,
    adj: Optional[LevelAdjustment],
    width: int,
    index: int,
) -> str:
    """
    One preview line like render_toc_preview, with optional realign markers:
    - Level cut (shallower): ``<`` then one hyphen per level dropped, between ``*`` and title.
    - Level deepen: (gain + 1) hyphens, ``>``, space, then ``*`` (at the new indent).
    """
    level = entry.level
    indent = "  " * (level - 1)
    if adj is None:
        star_segment = f"{indent}* "
        between = ""
    elif adj.to_level > adj.from_level:
        gain = adj.to_level - adj.from_level
        arrow = "-" * (gain * 2 - 2) + ">"
        star_segment = f"{indent}{arrow} * "
        between = ""
    else:
        drops = adj.from_level - adj.to_level
        cut = "<" + "-" * (drops * 2 - 2)
        star_segment = f"{indent}* "
        between = f"{cut} "

    line = (
        f"[{entry.size:>4.1f} {entry.style}] L{level} {star_segment}{between}"
        f"{entry.title} (p. {entry.page})"
    )
    line = f"{index:>3}. {line}"
    if len(line) <= width:
        return line
    if width <= 3:
        return "." * width
    return f"{line[: width - 3]}..."


def render_toc_realign_preview(
    toc_entries: List[TocEntry],
    adjustments: List[LevelAdjustment],
    width: int = 80,
) -> str:
    """Like ``render_toc_preview`` but annotates rows that ``realign_toc_entries_for_save`` changed."""
    by_row = {a.row: a for a in adjustments}
    return "\n".join(
        _toc_realign_preview_line(entry, by_row.get(idx), width, idx)
        for idx, entry in enumerate(toc_entries, start=1)
    )


def validate_toc_hierarchy(entries: List[TocEntry]) -> List[HierarchyIssue]:
    """Validate TOC levels for PyMuPDF compatibility."""
    return TocEntry.validate_hierarchy(entries)


def realign_toc_entries_for_save(
    entries: List[TocEntry],
) -> Tuple[List[TocEntry], List[LevelAdjustment]]:
    """
    Return a save-safe hierarchy by capping large forward jumps.
    Uses the previous row's original level for gap detection.
    Also forces first entry to level 1.
    """
    return TocEntry.realign_for_save(entries)


def print_hierarchy_diagnostics(entries: List[TocEntry], max_rows=20) -> None:
    """Print assigned hierarchy and selection reason for each entry."""
    TocEntry.print_diagnostics(entries, max_rows)


def dump_font_groups(records: List[SpanRecord]):
    """Print quick diagnostics for size/style/indent groups."""
    spans = list(records)
    if not spans:
        warn("No spans found.")
        return

    grouped = {}
    for rec in spans:
        key = (round(rec.size, 2), "bold" if rec.is_bold_font() else "regular", round(rec.x, 1))
        grouped[key] = grouped.get(key, 0) + 1

    hdr("Font group dump (size, weight, x -> count):")
    for (size, weight, x), count in sorted(grouped.items(), key=lambda i: (-i[0][0], i[0][2], i[0][1])):
        dim(f"  {size:>5.2f}, {weight:<7}, x={x:>6.1f} -> {count}")


def build_toc_for_save(entries: List[TocEntry]) -> List[TocSaveItem]:
    """Build destination-aware TOC payload from enriched entries."""
    return TocSaveItem.build_sequence(entries)


def inject_toc(input_path, output_path, toc: List[TocSaveItem], force=False):
    """Inject TOC into output PDF with optional overwrite of existing outlines."""
    if not toc:
        raise ValueError("No TOC entries detected.")

    doc = fitz.open(input_path)
    try:
        existing_toc = doc.get_toc()
        if existing_toc and not force:
            raise ValueError(
                "This PDF already has a TOC. Use --force to replace existing outlines."
            )

        toc_rows = [[item.level, item.title, item.page, item.y] for item in toc]
        doc.set_toc(toc_rows)
        for idx, item in enumerate(toc, start=0):
            doc.set_toc_item(
                idx,
                kind=fitz.LINK_GOTO,
                pno=item.page,
                to=fitz.Point(item.x, item.y),
            )
        doc.save(output_path)
    finally:
        doc.close()


def parse_thresholds(raw_value):
    """Parse user-provided comma-separated thresholds (descending)."""
    values = [item.strip() for item in raw_value.split(",") if item.strip()]
    thresholds = [float(v) for v in values]
    if not thresholds:
        raise ValueError("At least one threshold is required.")
    if sorted(thresholds, reverse=True) != thresholds:
        raise ValueError("Thresholds must be in descending order (largest to smallest).")
    return thresholds


def make_filter(next_filter_id, action, mode, pattern, preset: Optional[str] = None) -> FilterRule:
    """Construct a validated filter rule (backwards-compatible name)."""
    return FilterRule.create(next_filter_id, action, mode, pattern, preset)


def print_interactive_help():
    rows = [
        ("ok", "Accept current preview"),
        ("thresholds <csv>", "Set thresholds (descending)"),
        ("relax|more <bold|italics|color>...", "Relax selected rules (more = same)"),
        ("unrelax|less <bold|italics|color>...", "Tighten (less = same)"),
        ("revert", "Revert last relax/tighten change"),
        ("relax list | more list", "Show current relaxations"),
        ("filter list", "List filters"),
        ("filter add <+|-> <exact|regex> <pattern>", ""),
        ("filter update <id> <+|-> <exact|regex> <pattern>", ""),
        ("filter del <id>", "Delete a filter"),
        ("preset list", "List built-in presets"),
        ("preset use [<name>]", "Apply preset by name, or choose at a prompt"),
        ("block <entry_number>", "Blacklist exact title from preview"),
        ("show", "Print current preview now"),
        ("why <entry_number>", "Show assigned level reason for one entry"),
        ("help", "Show command help"),
    ]
    w = max(len(cmd) for cmd, _ in rows)
    hdr("\nInteractive commands:")
    for cmd, desc in rows:
        _print_help_row(cmd, desc, w)


def read_command_input(prompt):
    """
    Read interactive command input.
    - Ctrl-C clears current command line and keeps prompt active.
    - Ctrl-D exits interactive mode.
    """
    while True:
        try:
            return input(_lbl(prompt)).strip()
        except KeyboardInterrupt:
            warn("\nCommand cleared. Type a new command.")
            continue
        except EOFError:
            dim("\nExiting interactive mode.")
            return None


def read_prompt_input(prompt):
    """
    Read non-command input.
    - Ctrl-C clears current input and reprompts.
    - Ctrl-D returns None.
    """
    while True:
        try:
            return input(_lbl(prompt)).strip()
        except KeyboardInterrupt:
            warn("\nInput cleared.")
            continue
        except EOFError:
            print()
            return None


class CommandHandler:
    """Interactive tuning: thresholds, relaxations, filters."""

    def __init__(
        self,
        records: List[SpanRecord],
        initial_thresholds: List[float],
        initial_entries: Optional[List[TocEntry]] = None,
        show_initial_preview: bool = True,
    ):
        self.records = list(records)
        self.thresholds = list(initial_thresholds)
        self.relaxations = Relaxations()
        self.relax_stack: List[Relaxations] = []
        self.filters: List[FilterRule] = []
        self.next_filter_id = 1
        self.preset_registry = {
            "deep-numbering": r"^\d+(?:\.\d+){0,6}\.?(?:\s+.*)?$",
        }
        self.toc_entries: List[TocEntry] = list(initial_entries or [])
        self.needs_refresh = show_initial_preview or not self.toc_entries

    def run(self) -> Tuple[Optional[List[float]], Optional[List[TocEntry]]]:
        print_interactive_help()
        while True:
            if self.needs_refresh:
                self._rebuild_toc()
                self._print_state()
                self.needs_refresh = False

            raw = read_command_input("Command [ok/help]: ")
            if raw is None:
                return None, None
            if not raw:
                continue
            try:
                parts = shlex.split(raw)
            except ValueError as exc:
                err(f"Invalid command: {exc}")
                continue
            if not parts:
                continue

            done = self._dispatch(parts)
            if done:
                return self.thresholds, self.toc_entries

    def _rebuild_toc(self):
        self.toc_entries = build_toc_entries(self.records, self.thresholds, self.relaxations, self.filters)

    def _print_state(self):
        thr = ", ".join(f"{v:.1f}" for v in self.thresholds) or "(none)"
        print(f"\n{_lbl('Auto/Current thresholds:')} {_info(thr)}")
        rel = ", ".join(f"{name}={'on' if enabled else 'off'}" for name, enabled in self._relax_items())
        print(f"{_lbl('Relaxations:')} {_info(rel)}")
        lbl("Detected TOC preview:")
        if self.toc_entries:
            print(render_toc_preview(self.toc_entries, width=80))
        else:
            dim("(No entries detected for current settings.)")
        info(f"\nDetected {len(self.toc_entries)} potential TOC entries.")

    def _relax_items(self) -> List[Tuple[str, bool]]:
        r = self.relaxations
        return [("bold", r.bold), ("italics", r.italics), ("color", r.color)]

    def _dispatch(self, parts: List[str]) -> bool:
        """Return True when user accepted (ok)."""
        cmd = parts[0].lower()

        if cmd == "ok":
            return True

        if cmd == "show":
            self.needs_refresh = True
        elif cmd == "help":
            print_interactive_help()
        elif cmd == "why":
            self._cmd_why(parts)
        elif cmd == "thresholds":
            self._cmd_thresholds(parts)
        elif cmd in {"relax", "unrelax", "more", "less"}:
            self._cmd_relax(parts)
        elif cmd == "revert":
            self._cmd_revert()
        elif cmd == "preset":
            self._cmd_preset(parts)
        elif cmd == "filter":
            self._cmd_filter(parts)
        elif cmd == "block":
            self._cmd_block(parts)
        else:
            warn("Unknown command. Type 'help' for available commands.")

        return False  # Continue interactive mode

    def _cmd_why(self, parts: List[str]):
        if len(parts) != 2:
            err("Usage: why <entry_number>")
            return
        try:
            idx = int(parts[1]) - 1
        except ValueError:
            err("Entry number must be an integer.")
            return
        if idx < 0 or idx >= len(self.toc_entries):
            err("Entry number out of range.")
            return
        entry = self.toc_entries[idx]
        reason = entry.level_reason or "n/a"
        print(
            f"{_dim(f'row {idx + 1}:')} {_info(f'L{entry.level}')} {_dim('|')} "
            f"{reason} {_dim('|')} {entry.title} (p. {entry.page})"
        )

    def _cmd_thresholds(self, parts: List[str]):
        if len(parts) < 2:
            err("Usage: thresholds 20.0,16.0,14.5")
            return
        try:
            self.thresholds = parse_thresholds(parts[1])
            self.needs_refresh = True
        except ValueError as exc:
            err(f"Invalid thresholds: {exc}")

    def _cmd_relax(self, parts: List[str]):
        raw_cmd = parts[0].lower()
        if raw_cmd in {"more", "relax"}:
            cmd = "relax"
        elif raw_cmd in {"less", "unrelax"}:
            cmd = "unrelax"
        else:
            cmd = raw_cmd
        if len(parts) == 2 and parts[1] == "list":
            rel = ", ".join(f"{name}={'on' if enabled else 'off'}" for name, enabled in self._relax_items())
            print(f"{_lbl('Relaxations:')} {_info(rel)}")
            return
        if len(parts) < 2:
            print(
                _err(
                    f"Usage: {raw_cmd} <bold|italics|color>... or '{raw_cmd} list' "
                    f"(relax|more / unrelax|less)"
                )
            )
            return
        attrs = set(parts[1:])
        valid = {"bold", "italics", "color"}
        if not attrs.issubset(valid):
            err("Only bold, italics, color are supported.")
            return
        self.relax_stack.append(
            Relaxations(bold=self.relaxations.bold, italics=self.relaxations.italics, color=self.relaxations.color)
        )
        for attr in attrs:
            if attr == "bold":
                self.relaxations.bold = cmd == "relax"
            elif attr == "italics":
                self.relaxations.italics = cmd == "relax"
            else:
                self.relaxations.color = cmd == "relax"
        self.needs_refresh = True

    def _cmd_revert(self):
        if not self.relax_stack:
            warn("No relaxation history to revert.")
            return
        self.relaxations = self.relax_stack.pop()
        self.needs_refresh = True

    def _apply_preset(self, name: str) -> None:
        if name not in self.preset_registry:
            err(f"Unknown preset. Supported: {', '.join(sorted(self.preset_registry))}")
            return
        try:
            rule = make_filter(
                self.next_filter_id,
                "+",
                "regex",
                self.preset_registry[name],
                preset=name,
            )
            self.filters.append(rule)
            self.next_filter_id += 1
            self.needs_refresh = True
            ok(f"Applied preset {name} whitelist filter.")
        except ValueError as exc:
            err(f"Failed to apply preset: {exc}")

    def _resolve_preset_choice(self, raw: str) -> Optional[str]:
        """Map user input (1-based index or preset name) to a registry key."""
        raw = raw.strip()
        if not raw:
            return None
        names = sorted(self.preset_registry.keys())
        if raw.isdigit():
            n = int(raw)
            if 1 <= n <= len(names):
                return names[n - 1]
            return None
        norm = "-".join(raw.lower().split())
        if norm in self.preset_registry:
            return norm
        return None

    def _interactive_preset_use(self) -> None:
        names = sorted(self.preset_registry.keys())
        if not names:
            warn("No presets available.")
            return
        lbl("Choose a preset (enter number or name):")
        for i, name in enumerate(names, start=1):
            print(f"  {_cmd(str(i))}. {_info(name)}")
            dim(f"      {self.preset_registry[name]}")
        choice = read_prompt_input("Preset number or name: ")
        if choice is None:
            return
        resolved = self._resolve_preset_choice(choice)
        if not resolved:
            err("Unknown preset.")
            return
        self._apply_preset(resolved)

    def _cmd_preset(self, parts: List[str]):
        if len(parts) == 2 and parts[1].lower() == "list":
            hdr("Available presets:")
            for name, pattern in self.preset_registry.items():
                print(f"  {_cmd(name)}: {_dim(pattern)}")
            return
        if len(parts) >= 2 and parts[1].lower() == "use":
            if len(parts) == 2:
                self._interactive_preset_use()
                return
            name = "-".join(p.lower() for p in parts[2:])
            self._apply_preset(name)
            return
        err("Usage: preset list | preset use [<name>]")

    def _cmd_filter(self, parts: List[str]):
        if len(parts) < 2:
            err("Usage: filter list|add|update|del ...")
            return
        sub = parts[1].lower()
        if sub == "list":
            if not self.filters:
                dim("No filters configured.")
                return
            hdr("Filters:")
            for f in self.filters:
                preset_hint = _dim(f" (preset:{f.preset})") if f.preset else ""
                print(f"  {_info(f'#{f.id}')}: {_cmd(f.action)} {f.mode} {f.pattern}{preset_hint}")
            return
        if sub == "add" and len(parts) >= 5:
            action, mode = parts[2], parts[3]
            pattern = " ".join(parts[4:])
            try:
                self.filters.append(make_filter(self.next_filter_id, action, mode, pattern))
                self.next_filter_id += 1
                self.needs_refresh = True
            except ValueError as exc:
                err(f"Invalid filter: {exc}")
            return
        if sub == "update" and len(parts) >= 6:
            try:
                filter_id = int(parts[2])
            except ValueError:
                err("Filter id must be an integer.")
                return
            action, mode = parts[3], parts[4]
            pattern = " ".join(parts[5:])
            idx = next((i for i, f in enumerate(self.filters) if f.id == filter_id), None)
            if idx is None:
                err(f"Filter #{filter_id} not found.")
                return
            try:
                updated = make_filter(filter_id, action, mode, pattern)
                self.filters[idx] = updated
                self.needs_refresh = True
            except ValueError as exc:
                err(f"Invalid filter: {exc}")
            return
        if sub == "del" and len(parts) == 3:
            try:
                filter_id = int(parts[2])
            except ValueError:
                err("Filter id must be an integer.")
                return
            before = len(self.filters)
            self.filters = [f for f in self.filters if f.id != filter_id]
            if len(self.filters) == before:
                err(f"Filter #{filter_id} not found.")
            else:
                self.needs_refresh = True
            return
        err("Usage: filter add|update|del|list ...")

    def _cmd_block(self, parts: List[str]):
        if len(parts) != 2:
            err("Usage: block <entry_number>")
            return
        try:
            idx = int(parts[1]) - 1
        except ValueError:
            err("Entry number must be an integer.")
            return
        if idx < 0 or idx >= len(self.toc_entries):
            err("Entry number out of range.")
            return
        title = self.toc_entries[idx].title
        try:
            self.filters.append(make_filter(self.next_filter_id, "-", "exact", title))
            self.next_filter_id += 1
            self.needs_refresh = True
            ok(f"Blocked exact title: {title}")
        except ValueError as exc:
            err(f"Could not block entry: {exc}")


def interactive_threshold_selection(
    records,
    initial_thresholds,
    initial_entries=None,
    show_initial_preview=True,
):
    """
    Interactive tuning loop. Returns (accepted_thresholds, toc_entries) or (None, None).
    """
    spans = list(records)
    return CommandHandler(spans, initial_thresholds, initial_entries, show_initial_preview).run()


def parse_args():
    parser = argparse.ArgumentParser(
        description="Detect PDF headings and inject a bookmark TOC using PyMuPDF."
    )
    parser.add_argument("input_pdf", help="Path to the source PDF.")
    parser.add_argument(
        "output_pdf",
        nargs="?",
        help="Optional path to save output PDF with TOC (defaults to '<input>.with-toc.pdf').",
    )
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Print TOC preview and exit without writing an output file.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip confirmation prompt and save immediately.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing TOC outlines if they already exist.",
    )
    parser.add_argument(
        "--thresholds",
        help="Optional descending thresholds, e.g. '20.0,16.0,14.5'.",
    )
    parser.add_argument(
        "--dump-font-groups",
        action="store_true",
        help="Dump grouped font-size/style/indent stats for debugging.",
    )
    return parser.parse_args()


def derive_default_output_path(input_path):
    """Derive default output path in the same directory."""
    return input_path.with_name(f"{input_path.stem}.with-toc.pdf")


def main():
    args = parse_args()
    input_path = Path(args.input_pdf)
    output_path = Path(args.output_pdf) if args.output_pdf else derive_default_output_path(input_path)

    records = collect_spans(str(input_path))
    if args.dump_font_groups:
        dump_font_groups(records)
    auto_thresholds = SpanRecord.auto_calculate_thresholds(records)
    if not auto_thresholds:
        warn("No heading-like font groups found above body text.")
        return 1

    if args.thresholds:
        try:
            current_thresholds = parse_thresholds(args.thresholds)
        except ValueError as exc:
            err(f"Invalid --thresholds value: {exc}")
            return 1
    else:
        current_thresholds = auto_thresholds

    thr_line = ", ".join(f"{v:.1f}" for v in auto_thresholds)
    print(f"{_lbl('Auto-calculated thresholds:')} {_info(thr_line)}")

    if args.yes:
        accepted_thresholds = current_thresholds
        toc_entries = build_toc_entries(records, accepted_thresholds)
        lbl("Detected TOC preview:")
        print(render_toc_preview(toc_entries, width=80))
        info(f"\nDetected {len(toc_entries)} potential TOC entries.")
    else:
        accepted_thresholds, toc_entries = interactive_threshold_selection(records, current_thresholds)
        if accepted_thresholds is None:
            dim("Cancelled. No output PDF written.")
            return 0

    if args.preview_only:
        return 0

    while True:
        if not toc_entries:
            warn("No headers detected with current thresholds.")
            return 1

        entries_to_save = toc_entries
        hierarchy_issues = validate_toc_hierarchy(toc_entries)
        if hierarchy_issues:
            realigned_entries, adjustments = realign_toc_entries_for_save(toc_entries)
            warn("Warning: hierarchy gaps detected at save time; auto-realigned levels:")
            for adj in adjustments[:10]:
                print(
                    f"  {_dim(f'row {adj.row}:')} {_info(f'L{adj.from_level}')} {_dim('->')} "
                    f"{_info(f'L{adj.to_level}')} {_dim('|')} {adj.title}"
                )
            if len(adjustments) > 10:
                dim(f"  ... {len(adjustments) - 10} more rows adjusted")
            lbl("Auto-realigned TOC preview:")
            print(render_toc_realign_preview(realigned_entries, adjustments, width=80))

            if args.yes:
                info("Auto-accepting realigned TOC because --yes was provided.")
                entries_to_save = realigned_entries
            else:
                accept_aligned = read_prompt_input("Accept auto-realigned TOC for save? [y/N]: ")
                if accept_aligned is None or accept_aligned.lower() not in {"y", "yes"}:
                    warn("Returning to interactive mode to adjust settings before retry.")
                    accepted_thresholds, toc_entries = interactive_threshold_selection(
                        records,
                        accepted_thresholds,
                        initial_entries=toc_entries,
                        show_initial_preview=False,
                    )
                    if accepted_thresholds is None:
                        dim("Cancelled. No output PDF written.")
                        return 1
                    args.yes = False
                    continue
                entries_to_save = realigned_entries

        toc = build_toc_for_save(entries_to_save)
        print(f"{_lbl('Accepted thresholds:')} {_info(', '.join(f'{v:.1f}' for v in accepted_thresholds))}")
        if not args.output_pdf:
            info(f"No output path provided; using default output path: {output_path}")
        answer = "y" if args.yes else read_prompt_input("Save this TOC to the output PDF? [y/N]: ")
        if answer is None or answer.lower() not in {"y", "yes"}:
            dim("Cancelled. No output PDF written.")
            return 0

        try:
            inject_toc(str(input_path), str(output_path), toc, force=args.force)
            ok(f"Saved output PDF with TOC: {output_path}")
            return 0
        except Exception as exc:
            err(f"Save failed: {exc}")
            print_hierarchy_diagnostics(toc_entries, max_rows=25)
            warn("Returning to interactive mode to adjust settings before retry.")
            accepted_thresholds, toc_entries = interactive_threshold_selection(
                records,
                accepted_thresholds,
                initial_entries=toc_entries,
                show_initial_preview=False,
            )
            if accepted_thresholds is None:
                dim("Cancelled. No output PDF written.")
                return 1
            args.yes = False


if __name__ == "__main__":
    raise SystemExit(main())
