"""在S1-B独立目录完整复跑S1-A与嵌套S0，不覆盖历史报告。"""

import argparse
from pathlib import Path

from scripts.verify_s0 import write_json
from scripts.verify_s1a import execute, validate_report


def main() -> int:
    """调用原验收流程；静态、全部新旧测试及原数值门槛均保留。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    report = execute(root, args.source.resolve(), args.output.resolve())
    validate_report(report, root)
    write_json(args.output / "gate_report.json", report)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
