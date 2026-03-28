import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from addtoc import (
    TOC_DEST_TOP_MARGIN_PT,
    FilterRule,
    LevelAdjustment,
    Relaxations,
    SpanRecord,
    TocEntry,
    TocSaveItem,
    _bookmark_y_from_span,
    build_toc_for_save,
    build_toc_entries,
    derive_default_output_path,
    find_headers,
    parse_thresholds,
    realign_toc_entries_for_save,
    render_toc_preview,
    render_toc_realign_preview,
    validate_toc_hierarchy,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_PDF = PROJECT_ROOT / "samples" / "Pintos Projects Introduction.pdf"


class TestAddToc(unittest.TestCase):
    def test_bookmark_y_uses_min_bbox_y_minus_margin_y_down_coords(self):
        # y-down: top of span is min(y0,y1). Subtract margin (do not add—add moves down).
        span = {
            "origin": (100.0, 50.0),
            "size": 12.0,
            "bbox": (100.0, 40.0, 200.0, 60.0),
        }
        self.assertAlmostEqual(
            _bookmark_y_from_span(span),
            40.0 - TOC_DEST_TOP_MARGIN_PT,
        )

    def test_render_toc_preview_uses_indentation_and_truncation(self):
        toc_entries = [
            TocEntry(
                level=1,
                title="Top Level Title",
                page=1,
                size=20.0,
                x=72.0,
                y=36.0,
                style="B  ",
                source_level=1,
                level_reason="",
            ),
            TocEntry(
                level=2,
                title="Nested Subtitle With A Long Name That Should Be Truncated In Preview",
                page=2,
                size=16.2,
                x=72.0,
                y=36.0,
                style=" IU",
                source_level=2,
                level_reason="",
            ),
        ]

        preview = render_toc_preview(toc_entries, width=45)
        lines = preview.splitlines()

        self.assertEqual(lines[0], "  1. [20.0 B  ] L1 * Top Level Title (p. 1)")
        self.assertTrue(lines[1].startswith("  2. [16.2  IU] L2   * "))
        self.assertLessEqual(len(lines[1]), 45)
        self.assertTrue(lines[1].endswith("..."))

    def test_derive_default_output_path_uses_with_toc_suffix(self):
        input_path = Path("/tmp/example.pdf")
        self.assertEqual(
            derive_default_output_path(input_path),
            Path("/tmp/example.with-toc.pdf"),
        )

    def test_find_headers_detects_entries_in_pintos_sample(self):
        self.assertTrue(SAMPLE_PDF.exists(), "Sample PDF missing for test.")
        toc = find_headers(str(SAMPLE_PDF))
        self.assertGreater(len(toc), 0)
        self.assertTrue(all(entry[0] >= 1 for entry in toc))

    def test_build_toc_entries_uses_indent_for_deeper_same_size_bold(self):
        records = [
            SpanRecord(
                page=1,
                text="Body paragraph starts here",
                size=10.3,
                font="Helvetica",
                flags=0,
                color=0,
                x=60.0,
                y=700.0,
                line_key=(1, 0, 0),
                order=(0, 0, 0, 0),
            ),
            SpanRecord(
                page=1,
                text="1.2.2 Design",
                size=10.3,
                font="Helvetica-Bold",
                flags=0,
                color=0,
                x=64.5,
                y=680.0,
                line_key=(1, 0, 1),
                order=(0, 0, 1, 0),
            ),
            SpanRecord(
                page=1,
                text="1.2.2.1 Design Document",
                size=10.3,
                font="Helvetica-Bold",
                flags=0,
                color=0,
                x=69.7,
                y=660.0,
                line_key=(1, 0, 2),
                order=(0, 0, 2, 0),
            ),
        ]
        entries = build_toc_entries(
            records,
            thresholds=[15.4, 12.8],
            relaxations=Relaxations(bold=True, italics=False, color=False),
        )
        self.assertEqual(entries[0].y, 680.0)
        self.assertEqual(entries[1].y, 660.0)
        self.assertEqual(entries[0].level + 1, entries[1].level)

    def test_whitelist_then_blacklist_filters(self):
        records = [
            SpanRecord(
                page=1,
                text="1.2.2.1 Design Document",
                size=10.3,
                font="Helvetica-Bold",
                flags=0,
                color=0,
                x=69.7,
                y=0.0,
                line_key=(1, 0, 0),
                order=(0, 0, 0, 0),
            ),
            SpanRecord(
                page=1,
                text="Data Structures",
                size=10.3,
                font="Helvetica-Bold",
                flags=0,
                color=0,
                x=69.7,
                y=0.0,
                line_key=(1, 0, 1),
                order=(0, 0, 1, 0),
            ),
        ]
        filters = [
            FilterRule(id=1, action="+", mode="regex", pattern=r".+"),
            FilterRule(id=2, action="-", mode="exact", pattern="Data Structures"),
        ]
        entries = build_toc_entries(
            records,
            thresholds=[15.4, 12.8],
            relaxations=Relaxations(bold=True, italics=False, color=False),
            filters=filters,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].title, "1.2.2.1 Design Document")

    def test_deep_numbering_preset_allows_trailing_dot_formats(self):
        records = [
            SpanRecord(
                page=1,
                text="1. Introduction",
                size=20.0,
                font="Helvetica-Bold",
                flags=0,
                color=0,
                x=60.0,
                y=0.0,
                line_key=(1, 0, 0),
                order=(0, 0, 0, 0),
            ),
            SpanRecord(
                page=1,
                text="1.1. Overview",
                size=20.0,
                font="Helvetica-Bold",
                flags=0,
                color=0,
                x=60.0,
                y=0.0,
                line_key=(1, 0, 1),
                order=(0, 0, 1, 0),
            ),
            SpanRecord(
                page=1,
                text="1.1.1 Deep Topic",
                size=20.0,
                font="Helvetica-Bold",
                flags=0,
                color=0,
                x=60.0,
                y=0.0,
                line_key=(1, 0, 2),
                order=(0, 0, 2, 0),
            ),
        ]
        deep_numbering_pattern = r"^\d+(?:\.\d+){0,6}\.?(?:\s+.*)?$"
        filters = [FilterRule(id=1, action="+", mode="regex", pattern=deep_numbering_pattern)]
        entries = build_toc_entries(records, thresholds=[15.4, 12.8], filters=filters)
        self.assertEqual(len(entries), 3)

    def test_validate_toc_hierarchy_detects_invalid_jump(self):
        entries = [
            TocEntry(
                level=1,
                title="A",
                page=1,
                size=12.0,
                x=72.0,
                y=36.0,
                style="   ",
                source_level=1,
                level_reason="",
            ),
            TocEntry(
                level=3,
                title="B",
                page=1,
                size=12.0,
                x=72.0,
                y=36.0,
                style="   ",
                source_level=3,
                level_reason="",
            ),
        ]
        issues = validate_toc_hierarchy(entries)
        self.assertTrue(any(issue.reason == "level_jump_too_large" for issue in issues))

    def test_realign_toc_entries_for_save_uses_font_tiers_and_valid_outline(self):
        """Save realign derives tiers from font size (largest = shallowest), then caps jumps."""
        entries = [
            TocEntry(
                level=3,
                title="A",
                page=1,
                size=20.0,
                x=72.0,
                y=36.0,
                style="B  ",
                source_level=3,
                level_reason="",
            ),
            TocEntry(
                level=5,
                title="B",
                page=1,
                size=16.0,
                x=72.0,
                y=36.0,
                style="   ",
                source_level=5,
                level_reason="",
            ),
            TocEntry(
                level=2,
                title="C",
                page=1,
                size=14.0,
                x=72.0,
                y=36.0,
                style="   ",
                source_level=2,
                level_reason="",
            ),
            TocEntry(
                level=6,
                title="D",
                page=2,
                size=12.0,
                x=72.0,
                y=36.0,
                style="   ",
                source_level=6,
                level_reason="",
            ),
        ]
        aligned, adjustments = realign_toc_entries_for_save(entries)
        self.assertEqual([e.level for e in aligned], [1, 2, 3, 4])
        self.assertEqual(
            [(a.row, a.from_level, a.to_level) for a in adjustments],
            [(1, 3, 1), (2, 5, 2), (3, 2, 3), (4, 6, 4)],
        )

    def test_render_toc_realign_preview_marks_cut_levels_with_arrow(self):
        """Cut: one ``<`` plus one hyphen per level dropped, between ``*`` and title."""
        entries = [
            TocEntry(3, "A", 1, 20.0, 72.0, 36.0, "B  ", 3, ""),
            TocEntry(5, "B", 1, 16.0, 72.0, 36.0, "   ", 5, ""),
            TocEntry(2, "C", 1, 14.0, 72.0, 36.0, "   ", 2, ""),
            TocEntry(6, "D", 2, 12.0, 72.0, 36.0, "   ", 6, ""),
        ]
        aligned, adjs = realign_toc_entries_for_save(entries)
        preview = render_toc_realign_preview(aligned, adjs, width=120)
        lines = preview.splitlines()
        # Outline indent is 2 spaces per level after L1; markers sit next to * as documented.
        self.assertIn("* <-- A", lines[0])
        self.assertIn("* <--- B", lines[1])
        self.assertIn("L3", lines[2])
        self.assertIn("-> * C", lines[2])
        self.assertIn("* <-- D", lines[3])

    def test_render_toc_realign_preview_deepen_marker_before_star(self):
        """Deepen: (gain+1) hyphens then ``>`` immediately before ``*`` (realign rarely deepens)."""
        entry = TocEntry(3, "Deep", 1, 12.0, 72.0, 36.0, "   ", 3, "")
        adjs = [LevelAdjustment(row=1, from_level=1, to_level=3, title="Deep")]
        preview = render_toc_realign_preview([entry], adjs, width=120)
        self.assertIn("L3", preview)
        self.assertIn("---> * Deep", preview)

    def test_realign_keeps_consecutive_same_font_at_same_outline_level(self):
        """Same font size in a row maps to the same tier; repair keeps consecutive equals aligned."""
        entries = [
            TocEntry(
                level=4,
                title="Self-contained",
                page=5,
                size=14.2,
                x=72.0,
                y=36.0,
                style="B  ",
                source_level=4,
                level_reason="",
            ),
            TocEntry(
                level=4,
                title="Define Guard",
                page=5,
                size=14.2,
                x=72.0,
                y=36.0,
                style="B  ",
                source_level=4,
                level_reason="",
            ),
        ]
        aligned, _ = realign_toc_entries_for_save(entries)
        self.assertEqual(aligned[0].level, aligned[1].level)

    def test_build_toc_for_save_includes_exact_coordinates(self):
        entries = [
            TocEntry(
                level=1,
                title="Heading",
                page=2,
                size=20.0,
                x=123.4,
                y=567.8,
                style="   ",
                source_level=1,
                level_reason="",
            ),
            TocEntry(
                level=2,
                title="Sub",
                page=2,
                size=16.0,
                x=72.0,
                y=36.0,
                style="   ",
                source_level=2,
                level_reason="",
            ),
        ]
        toc = build_toc_for_save(entries)
        self.assertEqual(
            toc[0],
            TocSaveItem(level=1, title="Heading", page=2, x=123.4, y=567.8),
        )
        self.assertEqual(
            toc[1],
            TocSaveItem(level=2, title="Sub", page=2, x=72.0, y=36.0),
        )

    def test_parse_thresholds_requires_descending_order(self):
        thresholds = parse_thresholds("20.0, 16.0,14.5")
        self.assertEqual(thresholds, [20.0, 16.0, 14.5])

        with self.assertRaises(ValueError):
            parse_thresholds("14.0,16.0")

    def test_preview_only_does_not_write_output_file(self):
        self.assertTrue(SAMPLE_PDF.exists(), "Sample PDF missing for test.")

        with tempfile.TemporaryDirectory() as tmp_dir:
            output_pdf = Path(tmp_dir) / "out.pdf"
            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "addtoc.py"),
                    str(SAMPLE_PDF),
                    str(output_pdf),
                    "--preview-only",
                    "--yes",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertIn("Detected TOC preview:", result.stdout)
            self.assertFalse(output_pdf.exists())


if __name__ == "__main__":
    unittest.main()
