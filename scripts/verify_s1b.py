"""S1-B统一验收；失败非零退出，真实结果和历史报告分开保存。"""

import argparse
import logging
import os
import subprocess
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jsonschema

from scripts.s0_checks import digest, read_json
from scripts.s0_process import create_run, run_command
from scripts.s1b_checks import (
    fingerprint,
    imports,
    inspect_results,
    required_artifacts,
    test_collection,
)
from scripts.verify_s0 import environment_metadata, write_json
from scripts.verify_s1a import validate_report as validate_parent

COMMANDS = {"s1a", "experiments", "demo"}
CHECKS = COMMANDS | {
    "config",
    "imports",
    "parent",
    "B01_B22",
    "numerical",
    "artifacts",
    "fingerprint",
    "history",
}
HISTORY = (
    "gate_report.json",
    "docs/reports/S0_验收报告.md",
    "docs/reports/S1A_gate_report.json",
    "docs/reports/S1A_验收报告.md",
    "docs/provenance/source_manifest.json",
)


def validate_config(config: dict[str, Any]) -> None:
    """锁定预登记阈值和B编号；不允许运行后降低标准。"""
    expected = {
        "analytic_atol_m": 1e-6,
        "gradient_step_m": 1e-4,
        "gradient_atol": 1e-6,
        "gradient_rtol": 1e-6,
        "neutral_atol": 1e-10,
        "neutral_rtol": 1e-10,
        "time_atol_s": 1e-9,
        "contact_tolerance_m": 1e-6,
        "soft_margin_m": 1.0,
        "line_spacing_m": 0.05,
        "min_line_spacing_m": 0.002,
        "max_depth": 12,
        "min_interval_s": 0.02 / 4096,
    }
    if any(config.get(k) != v for k, v in expected.items()) or set(config["cases"]) != {
        f"B{i:02}" for i in range(1, 23)
    }:
        raise ValueError("阈值或必需编号偏离预登记")


def build_commands(source: Path, run: Path) -> list[tuple[str, list[str]]]:
    """原S1-A包含全量静态/新旧测试/嵌套S0；新增几何实验与只读绘图各执行一次。"""
    return [
        (
            "s1a",
            ["-m", "scripts.s1b_parent", "--source", str(source), "--output", str(run / "s1a")],
        ),
        ("experiments", ["-m", "scripts.s1b_experiments", "--output", str(run / "experiments")]),
        (
            "demo",
            [
                "-m",
                "scripts.demo_s1b_scene",
                "--input",
                str(run / "experiments"),
                "--output",
                str(run / "figures"),
            ],
        ),
    ]


def validate_report(report: dict[str, Any], root: Path, *, numerical: bool = True) -> None:
    """核对结构、全部必需门槛、产物哈希/存在性及实际测试；不接受空清单PASS。"""
    jsonschema.validate(report, read_json(root / "docs/schemas/S1B_gate_report.schema.json"))
    if {c["name"] for c in report["checks"]} != CHECKS or len(report["checks"]) != len(CHECKS):
        raise ValueError("必需检查缺失")
    if {c["name"] for c in report["commands"]} != COMMANDS or len(report["commands"]) != len(
        COMMANDS
    ):
        raise ValueError("必需命令缺失")
    for item in report["artifacts"]:
        path = (root / item["path"]).resolve()
        if not path.is_relative_to(root.resolve()) or digest(path) != item["sha256"]:
            raise ValueError("产物哈希错误或路径越界")
    if report["status"] == "PASS":
        if (
            any(c["status"] != "PASS" for c in report["checks"])
            or any(c["exit_code"] != 0 for c in report["commands"])
            or report["unresolved_issues"]
        ):
            raise ValueError("存在失败检查，不准PASS")
        run = root / report["run_directory"]
        required_artifacts(run)
        test_collection(run / "s1a/unit.xml")
        parent = read_json(run / "s1a/gate_report.json")
        validate_parent(parent, root, inspect=numerical)
        if parent["status"] != "PASS":
            raise ValueError("S1-A未通过")
        validate_config(read_json(run / "resolved_config.json"))
        if digest(run / "resolved_config.json") != report["config_sha256"]:
            raise ValueError("配置哈希错误")
        if numerical:
            inspect_results(run / "experiments")


def execute(root: Path, source: Path, run: Path) -> dict[str, Any]:
    """逐项执行和归档，异常保存堆栈；不让子进程失败被后续产物掩盖。"""
    config = read_json(root / "configs/s1b.json")
    write_json(run / "resolved_config.json", config)
    before = fingerprint(root)
    write_json(run / "tested_content.json", before)
    history = {name: digest(root / name) for name in HISTORY}
    checks = []
    commands = []

    def check(name: str, operation: Callable[[], Any]) -> None:
        logging.info("检查 %s", name)
        try:
            checks.append({"name": name, "status": "PASS", "details": operation()})
        except Exception as error:
            logging.exception("检查失败 %s", name)
            (run / f"{name}.exception.log").write_text(traceback.format_exc(), encoding="utf-8")
            checks.append({"name": name, "status": "FAIL", "details": {"error": str(error)}})

    check("config", lambda: validate_config(config))
    check("imports", lambda: imports(root))
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", MPLBACKEND="Agg", PYTHONDONTWRITEBYTECODE="1")
    for name, arguments in build_commands(source, run):
        record = run_command(
            name, [sys.executable, *arguments], root, run, config["timeout_s"], env
        )
        commands.append(record)
        checks.append(
            {
                "name": name,
                "status": record["status"],
                "details": {"exit_code": record["exit_code"]},
            }
        )

    def parent() -> dict[str, Any]:
        result = read_json(run / "s1a/gate_report.json")
        validate_parent(result, root)
        if result["status"] != "PASS":
            raise ValueError("原S1-A/S0未通过")
        return {
            "status": result["status"],
            "path": (run / "s1a/gate_report.json").relative_to(root).as_posix(),
        }

    check("parent", parent)
    check("B01_B22", lambda: test_collection(run / "s1a/unit.xml"))
    check("numerical", lambda: inspect_results(run / "experiments"))
    check("artifacts", lambda: required_artifacts(run))

    def unchanged() -> str:
        if before != fingerprint(root):
            raise ValueError("被测内容在运行中改变")
        return before["sha256"]

    def historical() -> dict[str, str]:
        if history != {name: digest(root / name) for name in HISTORY}:
            raise ValueError("历史报告被修改")
        return history

    check("fingerprint", unchanged)
    check("history", historical)
    issues = [c["name"] for c in checks if c["status"] != "PASS"]
    return {
        "stage": "S1-B",
        "parent_stage": "S1",
        "parent_status": "PENDING",
        "task_version": "1.0",
        "status": "FAIL" if issues else "PASS",
        "repository": "Whsjbwy/wl-rl-zl",
        "source_commit": "c2dfbd4f4425d29bdcba98294563cda19890cdb1",
        "base_sha": read_json(root / "docs/reports/S1A_交付回执.json")["s1b_base_sha"],
        "tested_base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
        .decode()
        .strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
        ),
        "run_id": run.name,
        "run_directory": run.relative_to(root).as_posix(),
        "tested_content_sha256": before["sha256"],
        "config_sha256": digest(run / "resolved_config.json"),
        "environment": environment_metadata(),
        "commands": commands,
        "checks": checks,
        "artifacts": [
            {"path": p.relative_to(root).as_posix(), "sha256": digest(p)}
            for p in sorted(run.rglob("*"))
            if p.is_file() and "baseline" not in p.parts
        ],
        "unresolved_issues": issues,
        "next_stage_authorized": False,
    }


def main() -> int:
    """唯一目录执行或独立复核已有报告；缺项/超时/错误均返回1。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--check-report", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.check_report:
            report = read_json(args.check_report)
            validate_report(report, root)
            return 0 if report["status"] == "PASS" else 1
        if args.source is None:
            raise ValueError("必须提供固定来源 --source")
        run = create_run(root / "results/s1b", args.run_id)
        report = execute(root, args.source.resolve(), run)
        validate_report(report, root, numerical=False)
        write_json(run / "gate_report.json", report)
        logging.info("S1-B=%s；父阶段S1=PENDING；结果=%s", report["status"], run)
        return 0 if report["status"] == "PASS" else 1
    except Exception:
        logging.exception("S1-B验收失败，原始记录保留")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
