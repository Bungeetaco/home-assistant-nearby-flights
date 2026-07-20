#!/usr/bin/env python3
"""Builds a categorized, human-readable changelog between the previous and the
current git tag, for use as a GitHub release body.

Expects commit subjects to (loosely) follow a conventional-commit-style prefix,
e.g. "feat: Add X", "fix: Correct Y", "remove: Drop Z", "feat!: Replace W"
(the trailing "!" or a "BREAKING CHANGE: ..." line in the commit body marks a
breaking change). Commits that don't follow the convention aren't dropped --
they land in a catch-all "Other changes" section, so a forgotten prefix never
makes a change silently disappear from the release notes.

Run from a checkout with full history (`fetch-depth: 0`). Reads the tag to
build notes for from $GITHUB_REF_NAME (set automatically on a tag-triggered
workflow run) and the repo slug from $GITHUB_REPOSITORY (for the compare
link), falling back to sensible defaults when run manually for testing.
"""
import os
import re
import subprocess


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True).stdout


def get_tags_desc():
    out = run("git", "tag", "-l", "v*", "--sort=-v:refname")
    return [t for t in out.splitlines() if t.strip()]


CATEGORY_MAP = {
    "feat": "Added",
    "fix": "Fixed",
    "remove": "Removed",
    "refactor": "Changed",
    "perf": "Changed",
    "style": "Changed",
    "chore": "Maintenance",
    "ci": "Maintenance",
    "docs": "Documentation",
    "test": "Tests",
}
CATEGORY_ORDER = [
    "Added", "Changed", "Fixed", "Removed", "Documentation", "Tests",
    "Maintenance", "Other changes",
]
CATEGORY_ICON = {
    "Added": "✨",
    "Changed": "\U0001F6E0️",
    "Fixed": "\U0001F41B",
    "Removed": "\U0001F5D1️",
    "Documentation": "\U0001F4DD",
    "Tests": "\U0001F9EA",
    "Maintenance": "\U0001F9F9",
    "Other changes": "\U0001F4E6",
}

SUBJECT_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?P<breaking>!)?:\s*(?P<desc>.+)$")
BREAKING_FOOTER_RE = re.compile(r"^BREAKING CHANGE:\s*(.+)$", re.IGNORECASE)


def categorize(subject, body):
    breaking_note = None
    for line in body.splitlines():
        m = BREAKING_FOOTER_RE.match(line.strip())
        if m:
            breaking_note = m.group(1)

    m = SUBJECT_RE.match(subject)
    if m:
        ctype = m.group("type").lower()
        desc = m.group("desc").strip()
        is_breaking = bool(m.group("breaking")) or breaking_note is not None
        category = CATEGORY_MAP.get(ctype, "Other changes")
        return category, desc, is_breaking, breaking_note

    is_breaking = breaking_note is not None
    return "Other changes", subject.strip(), is_breaking, breaking_note


def main():
    tags = get_tags_desc()
    new_tag = os.environ.get("GITHUB_REF_NAME") or (tags[0] if tags else None)
    if not new_tag:
        print("_No tag found._")
        return

    try:
        idx = tags.index(new_tag)
    except ValueError:
        idx = 0
    prev_tag = tags[idx + 1] if idx + 1 < len(tags) else None

    rev_range = f"{prev_tag}..{new_tag}" if prev_tag else new_tag
    log = run("git", "log", rev_range, "--no-merges", "--pretty=format:%H%x01%s%x01%b%x02")
    commits = [c for c in log.split("\x02") if c.strip()]

    buckets = {}
    breaking_entries = []
    for c in commits:
        parts = c.split("\x01")
        if len(parts) < 3:
            continue
        sha, subject, body = parts[0].strip(), parts[1].strip(), parts[2]
        if not subject:
            continue
        category, desc, is_breaking, breaking_note = categorize(subject, body)
        short_sha = sha[:7]
        buckets.setdefault(category, []).append(f"- {desc} (`{short_sha}`)")
        if is_breaking:
            breaking_entries.append(f"- {breaking_note or desc} (`{short_sha}`)")

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    lines = [f"## What's new in {new_tag}", ""]

    if breaking_entries:
        lines.append("### ⚠️ Breaking changes")
        lines.append("")
        lines.extend(breaking_entries)
        lines.append("")

    for category in CATEGORY_ORDER:
        entries = buckets.get(category)
        if not entries:
            continue
        lines.append(f"### {CATEGORY_ICON[category]} {category}")
        lines.append("")
        lines.extend(entries)
        lines.append("")

    if not commits:
        lines.append("_No code changes since the previous release._")
        lines.append("")

    if prev_tag and repo:
        lines.append(f"**Full diff:** https://github.com/{repo}/compare/{prev_tag}...{new_tag}")

    print("\n".join(lines).strip() + "\n")


if __name__ == "__main__":
    main()
