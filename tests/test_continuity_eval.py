import argparse
import json
import tempfile
import unittest
from pathlib import Path

from scripts.continuity_eval import CONDITIONS, DIMENSIONS, prepare, score


class ContinuityEvalTests(unittest.TestCase):
    def test_prepare_and_score_three_conditions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = {}
            for condition in CONDITIONS:
                path = root / f"{condition}.md"
                path.write_text(f"{condition} answer\n", encoding="utf-8")
                sources[condition] = path
            output = root / "blind"
            map_path = root / "private" / "map.json"
            prepare(
                argparse.Namespace(
                    **sources,
                    output_dir=output,
                    map=map_path,
                )
            )
            mapping = json.loads(map_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {item["condition"] for item in mapping.values()},
                set(CONDITIONS),
            )
            judgments_path = output / "judgments.json"
            judgments = json.loads(judgments_path.read_text(encoding="utf-8"))
            for label, entry in judgments["candidates"].items():
                condition = mapping[label]["condition"]
                value = {"clean": 2, "profile": 3, "episodes": 5}[condition]
                entry["scores"] = {dimension: value for dimension in DIMENSIONS}
                entry["contradictions"] = 0
                entry["notes"] = condition
            judgments_path.write_text(
                json.dumps(judgments),
                encoding="utf-8",
            )
            report_path = root / "report.json"
            score(
                argparse.Namespace(
                    map=map_path,
                    judgments=judgments_path,
                    report=report_path,
                )
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(report["winner"], "episodes")
            self.assertEqual(
                [row["condition"] for row in report["ranking"]],
                ["episodes", "profile", "clean"],
            )


if __name__ == "__main__":
    unittest.main()
