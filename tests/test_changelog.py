"""Regression coverage for release preparation and publication notes."""

import pytest

from scripts.changelog import prepare, release_notes

HISTORY = "## 5.11.101 - 2026-07-02\n\n### Changes\n- Historical note\n"


def seed(tmp_path, notes="### Fixed\n\n- Preserve channel settings."):
    (tmp_path / "VERSION").write_text("5.11.101\n")
    (tmp_path / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [Unreleased]\n\n{notes}\n\n{HISTORY}"
    )


def test_prepare_preserves_history_and_extracts_only_release(tmp_path):
    seed(tmp_path)
    prepare(tmp_path, "5.11.102", "2026-11-24")
    text = (tmp_path / "CHANGELOG.md").read_text()
    assert text.startswith(
        "# Changelog\n\n## [Unreleased]\n\n## [v5.11.102] - 2026-11-24"
    )
    assert text.endswith(HISTORY)
    assert (tmp_path / "VERSION").read_text() == "5.11.102\n"
    assert (
        release_notes(tmp_path, "5.11.102")
        == "### Fixed\n\n- Preserve channel settings.\n"
    )
    # A second release must not consume the preceding release's notes.
    with pytest.raises(ValueError, match="at least one"):
        prepare(tmp_path, "5.11.103", "2026-11-25")


@pytest.mark.parametrize(
    "notes", ["", "### Fixed", "- Uncategorized", "### Unknown\n- Note"]
)
def test_invalid_notes_do_not_modify_files(tmp_path, notes):
    seed(tmp_path, notes)
    original = (tmp_path / "CHANGELOG.md").read_text()
    with pytest.raises(ValueError):
        prepare(tmp_path, "5.11.102", "2026-11-24")
    assert (tmp_path / "CHANGELOG.md").read_text() == original
    assert (tmp_path / "VERSION").read_text() == "5.11.101\n"


@pytest.mark.parametrize("version", ["5.11.101", "5.11.100", "invalid", "05.12.0"])
def test_invalid_or_non_increasing_version(tmp_path, version):
    seed(tmp_path)
    with pytest.raises(ValueError):
        prepare(tmp_path, version, "2026-11-24")


def test_missing_release_notes_fail(tmp_path):
    seed(tmp_path)
    with pytest.raises(ValueError, match="dated changelog"):
        release_notes(tmp_path, "5.11.102")


def test_duplicate_version_is_rejected(tmp_path):
    seed(tmp_path)
    with (tmp_path / "CHANGELOG.md").open("a") as stream:
        stream.write("\n## [v5.11.102] - 2026-11-23\n\n### Fixed\n- Existing\n")
    with pytest.raises(ValueError, match="already contains"):
        prepare(tmp_path, "5.11.102", "2026-11-24")
