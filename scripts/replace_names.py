#!/usr/bin/env python3
"""Recursively replace a name in file contents, file names and folder names,
with an exact undo.

Matching is case-insensitive and the replacement follows the case of each
match (``foo -> bar`` turns ``FOO`` into ``BAR`` and ``Foo`` into ``Bar``).
Every change is recorded in a manifest, so ``revert`` puts back the exact
original text, case included.

Usage:
    replace_names.py apply  ROOT OLD NEW [-r OLD2 NEW2 ...] [--dry-run]
    replace_names.py revert ROOT [--force]

Examples:
    replace_names.py apply . nearby_flights plane_tracker -r "nearby flights" "plane tracker"
    replace_names.py revert .
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

MANIFEST_NAME = ".replace-names-undo.json"
DEFAULT_EXCLUDES = {".git", ".hg", ".svn", "__pycache__", "node_modules", ".venv", "venv"}
BINARY_SNIFF_BYTES = 8192


# ---------------------------------------------------------------- helpers

def match_case(template: str, word: str) -> str:
    """Return ``word`` with the letter case pattern of ``template``."""
    if template.isupper():
        return word.upper()
    if template.islower():
        return word.lower()
    if template.istitle():
        return word.title()
    if template[:1].isupper() and template[1:].islower():
        return word[:1].upper() + word[1:].lower()
    # Mixed case (e.g. camelCase): copy case position by position, then
    # continue with the case of the last letter.
    out = []
    last_upper = False
    for i, ch in enumerate(word):
        if i < len(template) and template[i].isalpha():
            last_upper = template[i].isupper()
        out.append(ch.upper() if last_upper else ch.lower())
    return "".join(out)


def build_pattern(pairs: list[tuple[str, str]]) -> tuple[re.Pattern, dict[str, str]]:
    # Longest first so "nearby_flights_card" wins over "nearby_flights".
    olds = sorted({old for old, _ in pairs}, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(o) for o in olds), re.IGNORECASE)
    lookup = {old.lower(): new for old, new in pairs}
    return pattern, lookup


def replacer(lookup: dict[str, str], preserve_case: bool):
    def repl(match: re.Match) -> str:
        found = match.group(0)
        new = lookup[found.lower()]
        return match_case(found, new) if preserve_case else new
    return repl


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_binary(data: bytes) -> bool:
    return b"\x00" in data[:BINARY_SNIFF_BYTES]


def walk(root: str, excludes: set[str], manifest_path: str):
    """Yield (dirpath, dirnames, filenames) bottom-up, skipping excludes."""
    manifest_abs = os.path.abspath(manifest_path)
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in excludes]
        filenames[:] = [
            f for f in filenames
            if f not in excludes and os.path.abspath(os.path.join(dirpath, f)) != manifest_abs
        ]
        yield dirpath, list(dirnames), list(filenames)


# ---------------------------------------------------------------- apply

def cmd_apply(args) -> int:
    root = os.path.abspath(args.root)
    manifest_path = args.manifest or os.path.join(root, MANIFEST_NAME)
    if os.path.exists(manifest_path) and not args.dry_run:
        sys.exit(f"Manifest already exists: {manifest_path}\n"
                 "Run 'revert' first, or pass --manifest to use a different file.")

    pairs = [(args.old, args.new)] + [tuple(p) for p in (args.replace or [])]
    for old, _ in pairs:
        if not old:
            sys.exit("Search string must not be empty.")
    pattern, lookup = build_pattern(pairs)
    repl = replacer(lookup, not args.exact_case)
    excludes = DEFAULT_EXCLUDES | set(args.exclude or [])

    tree = list(walk(root, excludes, manifest_path))

    # 1. File contents (done on the original paths, before any rename).
    content_changes = []
    for dirpath, _, filenames in tree:
        for name in filenames:
            path = os.path.join(dirpath, name)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                raw = fh.read()
            if is_binary(raw):
                continue
            text = raw.decode("utf-8", errors="surrogateescape")
            edits = []  # [offset in new text, new string, original string]
            parts = []
            pos = shift = 0
            for m in pattern.finditer(text):
                new = repl(m)
                parts.append(text[pos:m.start()])
                parts.append(new)
                edits.append([m.start() + shift, new, m.group(0)])
                shift += len(new) - len(m.group(0))
                pos = m.end()
            if not edits:
                continue
            parts.append(text[pos:])
            new_raw = "".join(parts).encode("utf-8", errors="surrogateescape")
            content_changes.append({
                "path": os.path.relpath(path, root),
                "sha256": sha256(new_raw),
                "edits": edits,
                "_data": new_raw,
            })

    # 2. Renames, deepest paths first so parent renames don't break children.
    renames = []
    planned_targets = set()
    for dirpath, dirnames, filenames in reversed(tree):
        for name in filenames + dirnames:
            new_name = pattern.sub(repl, name)
            if new_name == name:
                continue
            src = os.path.join(dirpath, name)
            dst = os.path.join(dirpath, new_name)
            same_file = os.path.normcase(src) == os.path.normcase(dst)
            if (os.path.lexists(dst) and not same_file) or dst in planned_targets:
                sys.exit(f"Refusing to rename, target already exists: {os.path.relpath(dst, root)}")
            planned_targets.add(dst)
            renames.append({"from": os.path.relpath(src, root), "to": os.path.relpath(dst, root)})

    total_edits = sum(len(c["edits"]) for c in content_changes)
    print(f"{len(content_changes)} file(s) with {total_edits} replacement(s), "
          f"{len(renames)} rename(s).")
    if args.dry_run or args.verbose:
        for c in content_changes:
            print(f"  edit   {c['path']} ({len(c['edits'])})")
        for r in renames:
            print(f"  rename {r['from']} -> {r['to']}")
    if args.dry_run:
        print("Dry run, nothing changed.")
        return 0
    if not content_changes and not renames:
        return 0

    # Write the manifest first so an interrupted run can still be reverted.
    manifest = {
        "version": 1,
        "pairs": pairs,
        "renames": renames,
        "contents": [{k: v for k, v in c.items() if k != "_data"} for c in content_changes],
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, ensure_ascii=False)

    for c in content_changes:
        with open(os.path.join(root, c["path"]), "wb") as fh:
            fh.write(c["_data"])
    for r in renames:
        os.rename(os.path.join(root, r["from"]), os.path.join(root, r["to"]))

    print(f"Done. Undo with: {os.path.basename(sys.argv[0])} revert {args.root}"
          + (f" --manifest {manifest_path}" if args.manifest else ""))
    return 0


# ---------------------------------------------------------------- revert

def restore_text(text: str, edits: list, exact: bool) -> tuple[str, int]:
    """Undo edits. Returns (text, number restored)."""
    if exact:
        for offset, new, orig in sorted(edits, key=lambda e: e[0], reverse=True):
            text = text[:offset] + orig + text[offset + len(new):]
        return text, len(edits)
    # File was changed after apply: find the written strings in order and put
    # the originals back one by one.
    out, pos, restored = [], 0, 0
    for _, new, orig in edits:
        idx = text.find(new, pos)
        if idx < 0:
            break
        out.append(text[pos:idx])
        out.append(orig)
        pos = idx + len(new)
        restored += 1
    out.append(text[pos:])
    return "".join(out), restored


def cmd_revert(args) -> int:
    root = os.path.abspath(args.root)
    manifest_path = args.manifest or os.path.join(root, MANIFEST_NAME)
    if not os.path.exists(manifest_path):
        sys.exit(f"No manifest found at {manifest_path}")
    with open(manifest_path, encoding="utf-8") as fh:
        manifest = json.load(fh)

    problems = 0

    # 1. Undo renames in reverse order (parents first, then children).
    for r in reversed(manifest["renames"]):
        src = os.path.join(root, r["to"])
        dst = os.path.join(root, r["from"])
        if not os.path.lexists(src):
            if os.path.lexists(dst):
                continue  # already reverted
            print(f"  missing: {r['to']}", file=sys.stderr)
            problems += 1
            continue
        same_file = os.path.normcase(src) == os.path.normcase(dst)
        if os.path.lexists(dst) and not same_file:
            print(f"  cannot rename back, {r['from']} exists", file=sys.stderr)
            problems += 1
            continue
        os.rename(src, dst)

    # 2. Restore file contents at their original paths.
    for c in manifest["contents"]:
        path = os.path.join(root, c["path"])
        if not os.path.isfile(path):
            print(f"  missing: {c['path']}", file=sys.stderr)
            problems += 1
            continue
        with open(path, "rb") as fh:
            raw = fh.read()
        exact = sha256(raw) == c["sha256"]
        text = raw.decode("utf-8", errors="surrogateescape")
        new_text, restored = restore_text(text, c["edits"], exact)
        if restored != len(c["edits"]):
            msg = (f"  {c['path']}: changed since apply, only "
                   f"{restored}/{len(c['edits'])} replacement(s) found")
            if not args.force:
                print(msg + " (skipped, use --force to restore those)", file=sys.stderr)
                problems += 1
                continue
            print(msg + " (restored those)", file=sys.stderr)
        elif not exact:
            print(f"  {c['path']}: changed since apply, restored by search", file=sys.stderr)
        with open(path, "wb") as fh:
            fh.write(new_text.encode("utf-8", errors="surrogateescape"))

    if problems:
        print(f"Reverted with {problems} problem(s); manifest kept at {manifest_path}",
              file=sys.stderr)
        return 1
    os.remove(manifest_path)
    print("Reverted.")
    return 0


# ---------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("apply", help="replace names and strings")
    p.add_argument("root", help="folder to process recursively")
    p.add_argument("old", help="string to find (case-insensitive)")
    p.add_argument("new", help="replacement")
    p.add_argument("-r", "--replace", nargs=2, action="append", metavar=("OLD", "NEW"),
                   help="additional OLD NEW pair (repeatable)")
    p.add_argument("--exact-case", action="store_true",
                   help="insert NEW exactly as typed instead of matching each hit's case")
    p.add_argument("-x", "--exclude", action="append", metavar="NAME",
                   help="file or folder name to skip (repeatable); "
                        f"always skipped: {', '.join(sorted(DEFAULT_EXCLUDES))}")
    p.add_argument("-n", "--dry-run", action="store_true", help="show changes only")
    p.add_argument("-v", "--verbose", action="store_true", help="list every change")
    p.add_argument("--manifest", help=f"undo file (default: ROOT/{MANIFEST_NAME})")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("revert", help="undo a previous apply")
    p.add_argument("root", help="same folder given to apply")
    p.add_argument("--manifest", help=f"undo file (default: ROOT/{MANIFEST_NAME})")
    p.add_argument("--force", action="store_true",
                   help="restore what can be found even in files edited since apply")
    p.set_defaults(func=cmd_revert)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
