#!/usr/bin/env python3
"""Read-only verification of an examiner release's MANIFEST.csv.

Run from any directory: python -I -B /path/to/package/verify.py [--json]
The manifest is an inventory, not a digital signature or a source-authenticity
certificate. This program uses only Python's standard library and writes nothing.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
EXCLUDED = {".git", ".reproduction"}


def safe_path(root: Path, name: str) -> Path:
    """Require a canonical relative POSIX path without links or Windows escapes."""
    if not name or "\\" in name or ":" in name or "\x00" in name:
        raise ValueError("invalid relative path")
    posix, windows = PurePosixPath(name), PureWindowsPath(name)
    if (posix.is_absolute() or windows.is_absolute() or windows.drive
            or any(p in {"", ".", ".."} for p in name.split("/"))
            or posix.parts[0].casefold() in EXCLUDED or name.casefold() == "manifest.csv"):
        raise ValueError("excluded, absolute, or escaping path")
    for part in posix.parts:
        if (part.endswith((".", " ")) or any(ord(char) < 32 or char in '<>"|?*' for char in part)
                or re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise ValueError("ambiguous or reserved Windows path component")
    candidate = root.joinpath(*posix.parts)
    current = root
    for component in posix.parts:
        current = current / component
        if current.is_symlink() or getattr(current, "is_junction", lambda: False)():
            raise ValueError("symbolic links and junctions are not release files")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError("path escapes package")
    return candidate


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(root: Path = ROOT) -> dict:
    root = root.resolve()
    errors, checked, total_bytes, seen = [], 0, 0, set()
    manifest = root / "MANIFEST.csv"
    try:
        if manifest.is_symlink() or getattr(manifest, "is_junction", lambda: False)():
            raise ValueError("MANIFEST.csv must be a regular file")
        with manifest.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != ["path", "bytes", "sha256"]:
                raise ValueError("required MANIFEST.csv header is path,bytes,sha256")
            for line, row in enumerate(reader, 2):
                name = row.get("path", "")
                try:
                    if None in row or any(value is None for value in row.values()):
                        raise ValueError("wrong number of CSV fields")
                    path = safe_path(root, name)
                    # Windows resolves names case-insensitively: reject aliases on all OSes.
                    identity = name.casefold()
                    if identity in seen:
                        raise ValueError("duplicate manifest path")
                    seen.add(identity)
                    if not re.fullmatch(r"0|[1-9][0-9]*", row["bytes"]):
                        raise ValueError("invalid byte count")
                    if not re.fullmatch(r"[a-fA-F0-9]{64}", row["sha256"]):
                        raise ValueError("invalid SHA-256")
                    if not path.is_file():
                        raise ValueError("missing regular file")
                    size = path.stat().st_size
                    if size != int(row["bytes"]):
                        raise ValueError(f"byte count differs: expected {row['bytes']}, got {size}")
                    if digest(path) != row["sha256"].lower():
                        raise ValueError("SHA-256 differs")
                    checked += 1
                    total_bytes += size
                except (OSError, ValueError) as error:
                    errors.append(f"manifest line {line} ({name!r}): {error}")
        if not seen:
            errors.append("manifest has no file entries")
        # Reject unlisted release files, without descending into generated results or Git.
        pending = [root]
        while pending:
            directory = pending.pop()
            for path in directory.iterdir():
                relative = path.relative_to(root)
                if relative.parts[0] in EXCLUDED or relative.as_posix() == "MANIFEST.csv":
                    continue
                if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
                    errors.append(f"link in release: {relative.as_posix()}")
                elif path.is_dir():
                    pending.append(path)
                elif relative.as_posix().casefold() not in seen:
                    errors.append(f"unlisted release file: {relative.as_posix()}")
    except (OSError, ValueError, csv.Error, UnicodeError) as error:
        errors.append(str(error))
    return {"ok": not errors, "files_checked": checked, "bytes_checked": total_bytes,
            "errors": errors, "scope": "release inventory, byte counts and SHA-256; no source reacquisition"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print structured results")
    args = parser.parse_args()
    result = verify()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"{'PASS' if result['ok'] else 'FAIL'}: {result['files_checked']} files, "
              f"{result['bytes_checked']} bytes checked")
        for error in result["errors"]:
            print(f"  {error}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
