"""Check the jq formatter used by the Discord workflow action."""

import json
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("jq"), "jq is required for Discord payload checks")
class DiscordReleaseNotesTests(unittest.TestCase):
    def format_notes(self, notes):
        action = Path(__file__).resolve().parents[1] / ".github/actions/discord-result"
        result = subprocess.run(
            ["jq", "-L", str(action), 'include "format-notes"; . | discord_notes'],
            input=json.dumps(notes),
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(result.stdout)

    def test_legacy_changes_heading_is_removed(self):
        self.assertEqual(
            self.format_notes("### Changes\r\n- Fix reports\r\n- Improve config\r\n"),
            "• Fix reports\n• Improve config",
        )

    def test_categories_and_links_are_readable(self):
        self.assertEqual(
            self.format_notes(
                "## [v5.11.102] - 2026-11-24\n\n### Added\n\n"
                "- A [new option](https://example.com).\n\n### Fixed\n\n* A bug."
            ),
            "**Added**\n\n• A [new option](https://example.com).\n\n**Fixed**\n\n• A bug.",
        )

    def test_long_notes_keep_complete_lines_and_fit_embed(self):
        notes = "### Fixed\n" + "\n".join(f"- Fix {i}: " + "x" * 90 for i in range(30))
        result = self.format_notes(notes)
        self.assertLessEqual(len(result), 1024)
        self.assertTrue(result.endswith("… See the full release notes."))
        for line in result.splitlines():
            if line.startswith("• "):
                self.assertTrue(line.endswith("x" * 90))

    def test_empty_notes_and_plain_commit_fallback(self):
        self.assertEqual(self.format_notes("\n### Changes\n"), "")
        self.assertEqual(self.format_notes("Fix channel lookup"), "Fix channel lookup")
