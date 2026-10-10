"""R2真实--check-report故障注入：仅改临时副本，同步哈希后验证语义拒绝。"""

import argparse
import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from scripts.s0_checks import digest
from scripts.s0_process import run_command
from scripts.strict_json import read_json, write_json

FAULTS = (
    "nan_gradient",
    "negative_gradient",
    "wrong_analytic_values",
    "wrong_counts",
    "bool_counts",
    "wrong_ratio",
    "range_ratio",
    "wrong_mean_subdivisions",
    "wrong_labels",
    "wrong_subdivisions",
    "duplicate_diagnostic",
    "endpoint_label",
    "duplicate_example",
    "report_summary",
    "missing_file",
    "missing_test",
    "invalid_json",
    "nonzero",
    "timeout",
    "negative_timing",
    "infinite_timing",
)


def mutate(name: str, metrics: dict[str, Any], report: dict[str, Any]) -> None:
    """明确登记的错误，不触碰真实记录；调用者同步新的artifact哈希。"""
    if name in {"nan_gradient", "negative_gradient"}:
        metrics["analytic"]["gradient_max_abs_error"] = (
            float("nan") if name.startswith("nan") else -1
        )
    elif name == "wrong_analytic_values":
        for row in metrics["analytic"]["distances"]:
            row.update(actual=123.0, expected=123.0, abs_error_m=0.0)
        metrics["analytic"]["max_abs_error_m"] = 0.0
    elif name in {"wrong_counts", "bool_counts"}:
        metrics["status_counts"]["CLEAR"] = True if name == "bool_counts" else 999
    elif name in {"wrong_ratio", "range_ratio"}:
        metrics["uncertain_ratio"] = 0.5 if name == "wrong_ratio" else 1.2
    elif name == "wrong_mean_subdivisions":
        metrics["mean_subdivisions"] = 0.7
    elif name == "wrong_labels":
        metrics["diagnostics"][0]["labels"][0] = "COLLISION"
    elif name == "wrong_subdivisions":
        metrics["diagnostics"][0]["subdivisions"][0] += 10
    elif name == "duplicate_diagnostic":
        metrics["diagnostics"][1] = copy.deepcopy(metrics["diagnostics"][0])
    elif name == "endpoint_label":
        metrics["counterexamples"][0]["endpoint_only_clear"] = False
    elif name == "duplicate_example":
        metrics["counterexamples"][-1] = copy.deepcopy(metrics["counterexamples"][0])
    elif name in {"nonzero", "timeout"}:
        report["commands"][0]["exit_code"] = 9 if name == "nonzero" else 124


def execute(root: Path, source_run: Path, output: Path) -> dict[str, Any]:
    """先正向CLI复核，再21类篡改；保留输入副本、退出码、stdout/stderr，不覆盖原run。"""
    output.mkdir(parents=True, exist_ok=False)
    original = read_json(source_run / "gate_report.json")
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    positive = run_command(
        "positive",
        [
            sys.executable,
            "scripts/verify_s1b.py",
            "--check-report",
            str(source_run / "gate_report.json"),
        ],
        root,
        output,
        300,
        env,
    )
    if positive["exit_code"] != 0:
        raise ValueError("正向真实证据未通过，不能用坏夹具制造负向通过")
    results = []
    # 临时目录受控于本项目results；退出时只清理该次创建的副本，原始运行始终只读。
    with tempfile.TemporaryDirectory(prefix="fault-copy-", dir=output) as temporary:
        target = Path(temporary)
        shutil.copytree(source_run / "experiments", target / "experiments")
        shutil.copytree(source_run / "figures", target / "figures")
        shutil.copyfile(source_run / "resolved_config.json", target / "resolved_config.json")
        (target / "s1a").mkdir()
        for filename in ("unit.xml", "gate_report.json"):
            shutil.copyfile(source_run / "s1a" / filename, target / "s1a" / filename)
        pristine = {p.relative_to(target): p.read_bytes() for p in target.rglob("*") if p.is_file()}
        for fault in ("unchanged", *FAULTS):
            case = output / fault
            case.mkdir()
            for name, data in pristine.items():
                (target / name).write_bytes(data)
            report = copy.deepcopy(original)
            report["run_directory"] = target.relative_to(root).as_posix()
            for item in report["artifacts"]:
                old = root / item["path"]
                relative = old.relative_to(source_run)
                if relative in pristine:
                    item["path"] = (target / relative).relative_to(root).as_posix()
            metrics = read_json(target / "experiments/metrics.json")
            mutate(fault, metrics, report)
            if fault in {"nonzero", "timeout"}:
                code = "raise SystemExit(9)" if fault == "nonzero" else "import time; time.sleep(5)"
                child = run_command(
                    "s1a",
                    [sys.executable, "-c", code],
                    root,
                    case,
                    10 if fault == "nonzero" else 0.1,
                    env,
                )
                if child["exit_code"] != (9 if fault == "nonzero" else 124):
                    raise ValueError("真实子进程故障未复现")
                report["commands"][0] = child
            (target / "experiments/metrics.json").write_text(
                json.dumps(metrics, allow_nan=True), encoding="utf-8"
            )
            (target / "experiments/counterexamples.json").write_text(
                json.dumps({"cases": metrics["counterexamples"]}), encoding="utf-8"
            )
            if fault == "missing_file":
                (target / "figures/hills.png").unlink()
            elif fault == "missing_test":
                (target / "s1a/unit.xml").write_text("<testsuite/>", encoding="utf-8")
            elif fault == "invalid_json":
                (target / "experiments/metrics.json").write_text("{invalid", encoding="utf-8")
            elif fault in {"negative_timing", "infinite_timing"}:
                timing = read_json(target / "experiments/timings.json")
                timing["static"][0] = -1 if fault.startswith("negative") else float("inf")
                (target / "experiments/timings.json").write_text(
                    json.dumps(timing), encoding="utf-8"
                )
            details = next(c for c in report["checks"] if c["name"] == "numerical")
            details["details"] = copy.deepcopy(metrics)
            if fault == "report_summary":
                details["details"]["status_counts"]["CLEAR"] += 1
            for item in report["artifacts"]:
                if (root / item["path"]).is_file():
                    item["sha256"] = digest(root / item["path"])
            # 非标准JSON故障有意输出到.txt证据；正式报告仍严格拒绝NaN/Infinity。
            candidate = target / "gate_report.json"
            candidate.write_text(json.dumps(report, allow_nan=True), encoding="utf-8")
            shutil.copyfile(candidate, case / "fault_report.txt")
            changed = []
            for name, data in pristine.items():
                path = target / name
                if not path.exists() or path.read_bytes() != data:
                    changed.append(name.as_posix())
                    if path.exists():
                        (case / (name.as_posix().replace("/", "__") + ".txt")).write_bytes(
                            path.read_bytes()
                        )
            record = run_command(
                fault,
                [sys.executable, "scripts/verify_s1b.py", "--check-report", str(candidate)],
                root,
                case,
                300,
                env,
            )
            record.update(
                fault=fault,
                changed=changed,
                artifact_hashes_updated=True,
                fixture_kind="fault_injection",
                source_run_id=source_run.name,
            )
            write_json(case / "execution.json", record)
            if fault == "unchanged":
                if record["exit_code"] != 0:
                    raise ValueError("临时副本正向CLI失败，禁止伪负向通过")
                continue
            results.append(record)
            if record["exit_code"] == 0:
                raise ValueError(f"坏证据被CLI接受:{fault}")
    result = {"positive": positive, "cases": results, "count": len(results), "all_rejected": True}
    write_json(output / "results.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    execute(Path(__file__).resolve().parents[1], args.run.resolve(), args.output.resolve())
