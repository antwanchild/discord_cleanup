"""Roll curated Unreleased notes into releases and extract publication notes."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re

SECTIONS = {
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
    "Requirements",
    "Performance",
    "Docs",
}


def validate_version(version: str) -> tuple[int, ...]:
    if not re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", version):
        raise ValueError(f"Invalid release version: {version}")
    return tuple(map(int, version.split(".")))


def section(text: str, heading: str) -> tuple[int, int, str]:
    headings = list(re.finditer(r"^## .+$", text, re.MULTILINE))
    matches = [i for i, match in enumerate(headings) if match.group() == heading]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {heading} section")
    index = matches[0]
    start = headings[index].start()
    end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
    return start, end, text[headings[index].end() : end].strip()


def validate_notes(notes: str) -> None:
    category = None
    found = False
    for line in notes.splitlines():
        if line.startswith("### "):
            category = line[4:]
            if category not in SECTIONS:
                raise ValueError(f"Unknown changelog category: {category}")
        elif line.startswith("- "):
            if category is None:
                raise ValueError("Changelog entries must be under a category")
            if line[2:].strip():
                found = True
    if not found:
        raise ValueError("Release notes must contain at least one categorized entry")


def prepare(root: Path, version: str, date: str) -> None:
    current = (root / "VERSION").read_text().strip()
    if validate_version(version) <= validate_version(current):
        raise ValueError("Release version must be greater than VERSION")
    path = root / "CHANGELOG.md"
    text = path.read_text()
    if re.search(
        rf"^## (?:\[v?{re.escape(version)}\]|{re.escape(version)}) - ",
        text,
        re.MULTILINE,
    ):
        raise ValueError(f"Changelog already contains version {version}")
    start, end, notes = section(text, "## [Unreleased]")
    validate_notes(notes)
    updated = (
        text[:start]
        + "## [Unreleased]\n\n"
        + f"## [v{version}] - {date}\n\n{notes}\n\n"
        + text[end:]
    )
    path.write_text(updated)
    (root / "VERSION").write_text(version + "\n")


def release_notes(root: Path, version: str) -> str:
    validate_version(version)
    text = (root / "CHANGELOG.md").read_text()
    headings = re.findall(
        rf"^## \[v{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", text, re.MULTILINE
    )
    if len(headings) != 1:
        raise ValueError(f"Expected exactly one dated changelog entry for {version}")
    _, _, notes = section(text, headings[0])
    validate_notes(notes)
    return notes + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "notes"])
    parser.add_argument("version")
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            prepare(
                Path.cwd(), args.version, datetime.now(timezone.utc).date().isoformat()
            )
        else:
            print(release_notes(Path.cwd(), args.version), end="")
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
