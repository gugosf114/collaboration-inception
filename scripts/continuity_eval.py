#!/usr/bin/env python3
"""Build and score a blind three-condition continuity evaluation.

The three answer files must come from the same task and model:

* clean: no relationship context
* profile: a static profile or covenant only
* episodes: the task-matched evidence packet produced by Inception

The evaluator sees randomized candidate labels. The mapping is kept separately
so presentation quality can be judged before the conditions are revealed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import shutil
import statistics
from pathlib import Path
from typing import Any, Sequence


DIMENSIONS = (
    "task_success",
    "directness",
    "continuity",
    "correction_use",
    "calibration",
)
CONDITIONS = ("clean", "profile", "episodes")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(args: argparse.Namespace) -> int:
    candidates = [(condition, getattr(args, condition)) for condition in CONDITIONS]
    missing = [str(path) for _, path in candidates if not path.is_file()]
    if missing:
        raise SystemExit(f"Missing answer files: {', '.join(missing)}")

    secrets.SystemRandom().shuffle(candidates)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, dict[str, str]] = {}
    judgments: dict[str, Any] = {
        "rubric": {
            "scale": "Each dimension is an integer from 1 (poor) to 5 (excellent).",
            "dimensions": list(DIMENSIONS),
            "contradictions": "Count claims that conflict with the supplied task evidence.",
            "notes": "Name concrete evidence from the answer. Do not guess its condition.",
        },
        "candidates": {},
    }
    for label, (condition, source) in zip(("A", "B", "C"), candidates):
        destination = args.output_dir / f"candidate-{label}.md"
        shutil.copyfile(source, destination)
        mapping[label] = {
            "condition": condition,
            "source": str(source.resolve()),
            "sha256": sha256(destination),
        }
        judgments["candidates"][label] = {
            "scores": {dimension: None for dimension in DIMENSIONS},
            "contradictions": None,
            "notes": "",
        }

    args.map.parent.mkdir(parents=True, exist_ok=True)
    args.map.write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
    judgment_path = args.output_dir / "judgments.json"
    judgment_path.write_text(json.dumps(judgments, indent=2) + "\n", encoding="utf-8")
    print(f"Blind candidates: {args.output_dir}")
    print(f"Condition map: {args.map}")
    print(f"Judge before opening the map: {judgment_path}")
    return 0


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Cannot read {path}: {exc}") from exc


def require_score(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise SystemExit(f"{name} must be an integer from 1 to 5")
    return value


def score(args: argparse.Namespace) -> int:
    mapping = load_json(args.map)
    judgments = load_json(args.judgments)
    rows: list[dict[str, Any]] = []
    for label in ("A", "B", "C"):
        if label not in mapping or label not in judgments.get("candidates", {}):
            raise SystemExit(f"Candidate {label} is missing from the map or judgments")
        entry = judgments["candidates"][label]
        scores = {
            dimension: require_score(
                entry.get("scores", {}).get(dimension),
                f"{label}.{dimension}",
            )
            for dimension in DIMENSIONS
        }
        contradictions = entry.get("contradictions")
        if (
            isinstance(contradictions, bool)
            or not isinstance(contradictions, int)
            or contradictions < 0
        ):
            raise SystemExit(f"{label}.contradictions must be a non-negative integer")
        mean = statistics.fmean(scores.values())
        rows.append(
            {
                "candidate": label,
                "condition": mapping[label]["condition"],
                "scores": scores,
                "mean": round(mean, 3),
                "contradictions": contradictions,
                "adjusted_score": round(mean - (0.5 * contradictions), 3),
                "notes": entry.get("notes", ""),
            }
        )
    rows.sort(key=lambda row: (-row["adjusted_score"], row["condition"]))
    report = {
        "winner": rows[0]["condition"],
        "ranking": rows,
        "interpretation": (
            "Episodes earn promotion only when they beat the static profile and clean "
            "conditions without adding contradictions."
        ),
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
        print(f"Scored report: {args.report}")
    else:
        print(rendered, end="")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Prepare or score Inception's blind continuity evaluation"
    )
    commands = result.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare")
    for condition in CONDITIONS:
        prepare_parser.add_argument(f"--{condition}", type=Path, required=True)
    prepare_parser.add_argument("--output-dir", type=Path, required=True)
    prepare_parser.add_argument("--map", type=Path, required=True)
    prepare_parser.set_defaults(handler=prepare)

    score_parser = commands.add_parser("score")
    score_parser.add_argument("--map", type=Path, required=True)
    score_parser.add_argument("--judgments", type=Path, required=True)
    score_parser.add_argument("--report", type=Path)
    score_parser.set_defaults(handler=score)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
