#!/usr/bin/env python3
"""Randomize two model answers into a blind A/B pair with an audit map."""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import shutil
from pathlib import Path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("history", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    candidates = [
        ("preserved_history", args.history),
        ("clean_baseline", args.baseline),
    ]
    if any(not path.is_file() for _, path in candidates):
        raise SystemExit("Both answer files must exist")

    secrets.SystemRandom().shuffle(candidates)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, dict[str, str]] = {}
    for label, (condition, source) in zip(("A", "B"), candidates):
        destination = args.output_dir / f"blind-{label}.md"
        shutil.copyfile(source, destination)
        mapping[label] = {
            "condition": condition,
            "source": str(source.resolve()),
            "sha256": sha256(destination),
        }

    map_path = args.output_dir / "blind-map.json"
    map_path.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    print(f"Created blind A/B pair and sealed map in {args.output_dir}")


if __name__ == "__main__":
    main()
