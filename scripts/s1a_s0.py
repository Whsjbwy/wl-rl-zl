"""在 S1-A 独立目录调用原 S0 验收，保留历史根报告不覆盖。"""

import argparse
from pathlib import Path

from scripts.verify_s0 import execute, validate_report, write_json


def main() -> int:
    """同一解释器完整执行原 S0，报告与原始轨迹放在 --output；失败返回1。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[1]
    report = execute(root, args.source.resolve(), args.output.resolve())
    validate_report(report, root)
    write_json(args.output / "gate_report.json", report)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
