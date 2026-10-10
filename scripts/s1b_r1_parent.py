"""复用加强后的S1-B完整入口，嵌套原S1-A/S0；结果仅写R1目录。"""

import argparse
from pathlib import Path

from scripts.strict_json import write_json
from scripts.verify_s1b import execute, validate_report

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    args.output.mkdir(parents=True, exist_ok=False)
    result = execute(root, args.source, args.output)
    write_json(args.output / "gate_report.json", result)
    validate_report(result, root)
    raise SystemExit(0 if result["status"] == "PASS" else 1)
