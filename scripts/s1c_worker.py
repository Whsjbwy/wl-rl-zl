"""S1-C子进程边界，保留每类运行stdout/stderr及非零退出。"""

import argparse
from pathlib import Path

from scripts import s1c_atomic, s1c_numeric, s1c_trajectories
from scripts.s1c_diagnostics import counterexamples, plot
from scripts.strict_json import write_json
from scripts.verify_s1b_r1 import execute, validate_report


def main() -> None:
    """明确阶段子任务，不重复调用多个父入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=["numeric", "trajectories", "atomic", "parent"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    args = parser.parse_args()
    if args.kind == "parent":
        args.output.mkdir(parents=True, exist_ok=False)
        root = Path(__file__).resolve().parents[1]
        report = execute(root, args.source, args.output)
        write_json(args.output / "gate_report.json", report)
        validate_report(report, root)
        if report["status"] != "PASS":
            raise ValueError("R1父验收失败")
    else:
        {
            "numeric": s1c_numeric.execute,
            "trajectories": s1c_trajectories.execute,
            "atomic": s1c_atomic.execute,
        }[args.kind](args.output)
        if args.kind == "numeric":
            counterexamples(args.output)
        if args.kind == "trajectories":
            plot(args.output.parent)


if __name__ == "__main__":
    main()
