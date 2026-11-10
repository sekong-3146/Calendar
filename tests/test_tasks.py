import tests  # noqa: F401  (sandbox first: must precede every mdcal import)
import unittest
from datetime import date

from mdcal import tasks


class ParseTest(unittest.TestCase):
    def test_marks(self):
        self.assertTrue(tasks.parse_task("- [ ] a").is_open)
        self.assertTrue(tasks.parse_task("  * [x] a").is_done)
        self.assertTrue(tasks.parse_task("1. [X] a").is_done)
        self.assertTrue(tasks.parse_task("- [>] a (→ 11/10)").is_carried)
        self.assertIsNone(tasks.parse_task("- a"))
        self.assertIsNone(tasks.parse_task("[ ] a"))

    def test_trailing_cr_is_ignored(self):
        self.assertEqual(tasks.parse_task("- [ ] a\r").text, "a")
        self.assertEqual(tasks.set_mark("- [ ] a\r", tasks.DONE), "- [x] a\r")

    def test_fences_and_headings_are_not_tasks(self):
        body = "# - [ ] title\n```\n- [ ] code\n```\n- [ ] real"
        self.assertEqual([t.text for t in tasks.open_tasks(body)], ["real"])
        self.assertEqual(tasks.open_tasks(body)[0].line_no, 4)


class ConvertTest(unittest.TestCase):
    def test_toggle_cycle(self):
        self.assertEqual(tasks.toggle_line("buy milk"), "- [ ] buy milk")
        self.assertEqual(tasks.toggle_line("- [ ] buy milk"), "- [x] buy milk")
        self.assertEqual(tasks.toggle_line("- [x] buy milk"), "- [ ] buy milk")
        self.assertEqual(tasks.toggle_line("- [>] old (→ 1/2)"), "- [>] old (→ 1/2)")

    def test_make_task_keeps_bullet_and_indent(self):
        self.assertEqual(tasks.make_task("  - a"), "  - [ ] a")
        self.assertEqual(tasks.make_task("3. a"), "3. [ ] a")
        self.assertEqual(tasks.make_plain("  - [x] a"), "  - a")

    def test_toggle_done_checks_expected_line(self):
        body = "x\n- [ ] a"
        self.assertEqual(tasks.toggle_done(body, 1, "- [ ] a"), "x\n- [x] a")
        with self.assertRaises(ValueError):
            tasks.toggle_done(body, 1, "- [ ] changed")
        with self.assertRaises(ValueError):
            tasks.toggle_done(body, 0, "x")
        with self.assertRaises(ValueError):
            tasks.toggle_done("```\n- [ ] a\n```", 1, "- [ ] a")

    def test_continuation(self):
        self.assertIsNone(tasks.continuation("plain"))
        self.assertEqual(tasks.continuation("- [ ] "), "")
        self.assertEqual(tasks.continuation("  - [x] a"), "  - [ ] ")
        self.assertEqual(tasks.continuation("2. [ ] a"), "3. [ ] ")


class CarryTextTest(unittest.TestCase):
    def test_carried_line_replaces_old_suffix(self):
        self.assertEqual(tasks.carried_line("- [ ] a (→ 11/9)", date(2026, 11, 10)), "- [>] a (→ 11/10)")

    def test_has_task_text_ignores_mark_and_suffix(self):
        self.assertTrue(tasks.has_task_text("- [x] a", "a (→ 1/2)"))
        self.assertFalse(tasks.has_task_text("a", "a"))

    def test_append_open_tasks(self):
        self.assertEqual(tasks.append_open_tasks("", ["a"]), "- [ ] a\n")
        self.assertEqual(tasks.append_open_tasks("note\n\n", ["a", "b"]), "note\n- [ ] a\n- [ ] b\n")
        self.assertEqual(tasks.append_open_tasks("x", []), "x")

    def test_append_closes_an_open_fence_with_its_own_delimiter(self):
        for fence in ("```", "~~~", "`````", "~~~~"):
            out = tasks.append_open_tasks(f"{fence}\ncode", ["a"])
            self.assertEqual(out, f"{fence}\ncode\n{fence}\n- [ ] a\n", fence)
        self.assertEqual(tasks.append_open_tasks("```\ncode\n```", ["a"]), "```\ncode\n```\n- [ ] a\n")

    def rendered_checkboxes(self, markdown):
        """Texts Qt's Markdown parser (independent of our fence logic) renders as unchecked boxes."""
        from PySide6.QtGui import QTextBlockFormat, QTextDocument, QTextFormat
        doc = QTextDocument()
        doc.setMarkdown(markdown)
        out, block = [], doc.begin()
        while block.isValid():
            fmt = block.blockFormat()
            if fmt.marker() == QTextBlockFormat.MarkerType.Unchecked:
                self.assertFalse(fmt.hasProperty(QTextFormat.BlockCodeFence), block.text())
                out.append(block.text())
            block = block.next()
        return out

    def test_renderer_helper_can_tell_code_from_checkbox(self):
        self.assertEqual(self.rendered_checkboxes("- [ ] a"), ["a"])
        self.assertEqual(self.rendered_checkboxes("```\n- [ ] a\n```"), [])
        self.assertEqual(self.rendered_checkboxes("~~~\n- [ ] a"), [])

    def test_append_result_renders_as_a_checkbox_not_code(self):
        bodies = ["```\ncode", "~~~\ncode", "````\ncode",
                  "````\ncode\n    ````",              # 4-space "closer" does not close
                  "~~~\ncode\n    ~~~",
                  "    ```\n\n- [ ] existing",         # 4-space "opener" is indented code, not a fence
                  "- item\n    ```\n    code",         # real fence inside a list item, left open
                  "- item\n    ```\n    code\n    ```"]
        for body in bodies:
            out = tasks.append_task(body, "real task")
            self.assertIn("real task", self.rendered_checkboxes(out), repr(out))
            self.assertEqual(self.rendered_checkboxes(out),
                             [t.text for t in tasks.open_tasks(out)], repr(out))   # we agree with Qt

    def test_carry_target_with_indented_backticks_is_not_wrapped_in_a_fence(self):
        out = tasks.append_open_tasks("    ```\n\n- [ ] existing", ["carried"])
        self.assertEqual(out, "    ```\n\n- [ ] existing\n- [ ] carried\n")
        self.assertEqual(self.rendered_checkboxes(out), ["existing", "carried"])

    def test_fence_closes_only_with_same_char_and_length(self):
        body = "~~~\n```\n- [ ] inside\n```\n~~~\n- [ ] out\n````\n```\n- [ ] in2\n````\n- [ ] out2"
        self.assertEqual([t.text for t in tasks.open_tasks(body)], ["out", "out2"])

    def test_append_task_is_literal(self):
        self.assertEqual(tasks.append_task("x", " send report (→ 11/12) "), "x\n- [ ] send report (→ 11/12)\n")
        self.assertEqual(tasks.append_task("x", "(→ 11/12)"), "x\n- [ ] (→ 11/12)\n")
        self.assertEqual(tasks.append_task("x", "   "), "x")
        self.assertEqual(tasks.append_open_tasks("x", ["a (→ 11/9)"]), "x\n- [ ] a\n")   # carry still cleans


if __name__ == "__main__":
    unittest.main()
