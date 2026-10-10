"""S1-B-R1完整验收：保留159项、真实异常/语义故障、全部父阶段和打包器测试。"""

import argparse
import importlib
import logging
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.s0_checks import digest
from scripts.s0_process import create_run, run_command
from scripts.s1b_checks import fingerprint
from scripts.s1b_r1_faults import FAULTS
from scripts.strict_json import finite_tree, read_json, write_json
from scripts.verify_s0 import environment_metadata
from scripts.verify_s1b import HISTORY
from scripts.verify_s1b import validate_report as validate_baseline

HISTORICAL = (
    *HISTORY,
    "docs/reports/S1B_gate_report.json",
    "docs/reports/S1B_验收报告.md",
    "docs/reports/S1B_实施记录.md",
)
COMMANDS = {"baseline", "atomic", "faults"}
CHECKS = COMMANDS | {"tests", "raw_evidence", "fingerprint", "history"}


def required_tests(root: Path, run: Path) -> dict[str, Any]:
    """按审查head原159个真实JUnit ID逐项保留，不能只检查总数。"""
    # 保留旧六模块返回契约；R1在本入口新增导入路径检查，不改变旧测试门槛。
    imported = importlib.import_module("auv_env.monitor_validation")
    if Path(str(imported.__file__)).resolve() != (root / "auv_env/monitor_validation.py").resolve():
        raise ValueError("R1新增模块导入路径错误")
    required = read_json(root / "configs/s1b_r1_required_tests.json")
    cases = list(ElementTree.parse(run / "s1b/s1a/unit.xml").iter("testcase"))
    actual = {(c.attrib["classname"], c.attrib["name"]) for c in cases}
    old = {(c["classname"], c["name"]) for c in required["required"]}
    if len(old) != 159 or not old.issubset(actual) or any(len(c) for c in cases):
        raise ValueError("原159项覆盖缺失或JUnit含失败/跳过")
    for module in ("test_s1b_r1_monitor", "test_s1b_r1_semantics", "test_review_bundle"):
        if not any(module in name for name, _ in actual):
            raise ValueError("新增必需测试模块未收集")
    return {"total": len(cases), "retained": 159, "added": len(cases) - 159}


def raw_evidence(root: Path, run: Path) -> dict[str, Any]:
    """读真实父报告/梯度/完整快照与CLI负例，不信任仅有PASS字样。"""
    parent = read_json(run / "s1b/gate_report.json")
    validate_baseline(parent, root)
    if parent["status"] != "PASS":
        raise ValueError("完整父阶段失败")
    metrics = read_json(run / "s1b/experiments/metrics.json")
    if len(metrics["analytic"].get("gradients", [])) != 3 or any(
        "input" not in r or "case_id" not in r for r in metrics["analytic"]["distances"]
    ):
        raise ValueError("R1解析/梯度原始输入缺失")
    atomic = read_json(run / "atomic/results.json")
    if {r["error"] for r in atomic["cases"]} != {
        "ValueError",
        "FloatingPointError",
        "RuntimeError",
    }:
        raise ValueError("异常类型覆盖缺失")
    for row in atomic["cases"]:
        data = read_json(run / "atomic" / (row["error"] + ".json"))
        if data["before"] != data["after_exception"] or data["before"] != data["other_branch"]:
            raise ValueError("异常后状态/分支变化")
        if data["after_retry"] != data["control"] or data["control"] != data["restored"]:
            raise ValueError("异常后重试不符")
    faults = read_json(run / "fault_injection/results.json")
    if {r["fault"] for r in faults["cases"]} != set(FAULTS) or len(faults["cases"]) != len(FAULTS):
        raise ValueError("语义故障覆盖缺失")
    if faults["positive"]["exit_code"] != 0 or any(r["exit_code"] != 1 for r in faults["cases"]):
        raise ValueError("CLI正向/负向出口不符")
    # 原100组每组四种证据及分支记录必须齐全，父验收已逐步复算。
    snapshots = run / "s1b/s1a/experiments/snapshots"
    for group in range(100):
        for suffix in (
            "snapshot.json",
            "original.jsonl",
            "restored.jsonl",
            "json.jsonl",
            "branches.json",
        ):
            if not (snapshots / f"{group:03}.{suffix}").is_file():
                raise ValueError("100组恢复证据缺失")
    return {
        "atomic_errors": 3,
        "semantic_rejections": len(FAULTS),
        "snapshot_groups": 100,
        "analytic_error_m": metrics["analytic"]["max_abs_error_m"],
        "neutral_error": metrics["neutral"]["max_abs"],
    }


def validate_report(report: dict[str, Any], root: Path) -> None:
    """严格R1结构、哈希和真实原始证据；摘要必须与复算完全对应。"""
    finite_tree(report)
    if report["stage"] != "S1-B-R1" or report["parent_status"] != "PENDING":
        raise ValueError("阶段标识非法")
    if {r["name"] for r in report["commands"]} != COMMANDS or len(report["commands"]) != len(
        COMMANDS
    ):
        raise ValueError("必需命令缺失")
    if {r["name"] for r in report["checks"]} != CHECKS or len(report["checks"]) != len(CHECKS):
        raise ValueError("必需检查缺失")
    for row in report["artifacts"]:
        path = (root / row["path"]).resolve()
        if not path.is_relative_to(root.resolve()) or digest(path) != row["sha256"]:
            raise ValueError("R1产物哈希/路径非法")
    if report["status"] == "PASS":
        if (
            report["unresolved_issues"]
            or any(r["exit_code"] != 0 for r in report["commands"])
            or any(r["status"] != "PASS" for r in report["checks"])
        ):
            raise ValueError("失败不能PASS")
        run = root / report["run_directory"]
        actual = {"tests": required_tests(root, run), "raw_evidence": raw_evidence(root, run)}
        for row in report["checks"]:
            if row["name"] in actual and row["details"] != actual[row["name"]]:
                raise ValueError("R1报告摘要不符")


def execute(root: Path, source: Path, run: Path) -> dict[str, Any]:
    """每次新目录；异常/非零保留日志，不影响历史报告。"""
    before = fingerprint(root)
    write_json(run / "tested_content.json", before)
    (run / "resolved_config.json").write_bytes((root / "configs/s1b.json").read_bytes())
    history = {n: digest(root / n) for n in HISTORICAL}
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", MPLBACKEND="Agg")
    commands = []
    checks = []
    for name, args in (
        (
            "baseline",
            ["-m", "scripts.s1b_r1_parent", "--source", str(source), "--output", str(run / "s1b")],
        ),
        ("atomic", ["-m", "scripts.s1b_r1_atomic", "--output", str(run / "atomic")]),
        (
            "faults",
            [
                "-m",
                "scripts.s1b_r1_faults",
                "--run",
                str(run / "s1b"),
                "--output",
                str(run / "fault_injection"),
            ],
        ),
    ):
        record = run_command(name, [sys.executable, *args], root, run, 300, env)
        commands.append(record)
        checks.append(
            {
                "name": name,
                "status": record["status"],
                "details": {"exit_code": record["exit_code"]},
            }
        )
    for name, operation in (
        ("tests", lambda: required_tests(root, run)),
        ("raw_evidence", lambda: raw_evidence(root, run)),
        ("fingerprint", lambda: before == fingerprint(root)),
        ("history", lambda: history == {n: digest(root / n) for n in HISTORICAL}),
    ):
        try:
            result = operation()
            if result is False:
                raise ValueError("被测或历史内容变化")
            checks.append({"name": name, "status": "PASS", "details": result})
        except Exception as error:
            (run / f"{name}.exception.log").write_text(traceback.format_exc(), encoding="utf-8")
            checks.append({"name": name, "status": "FAIL", "details": str(error)})
    issues = [c["name"] for c in checks if c["status"] != "PASS"]
    return {
        "stage": "S1-B-R1",
        "task_version": "1.0",
        "repository": "Whsjbwy/wl-rl-zl",
        "source_commit": "c2dfbd4f4425d29bdcba98294563cda19890cdb1",
        "reviewed_head": "2bac576fbca5bd55b55c68da5755fbb17b02737e",
        "tested_base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
        .decode()
        .strip(),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
        ),
        "parent_status": "PENDING",
        "external_review": "PENDING",
        "status": "FAIL" if issues else "PASS",
        "run_id": run.name,
        "run_directory": run.relative_to(root).as_posix(),
        "tested_content_sha256": before["sha256"],
        "config_sha256": digest(root / "configs/s1b.json"),
        "environment": environment_metadata(),
        "commands": commands,
        "checks": checks,
        "unresolved_issues": issues,
        "artifacts": [
            {"path": p.relative_to(root).as_posix(), "sha256": digest(p)}
            for p in sorted(run.rglob("*"))
            if p.is_file() and "baseline" not in p.parts
        ],
    }


def main() -> int:
    """真实入口；异常报告非零，原始结果保留。"""
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
        else:
            if args.source is None:
                raise ValueError("必须提供来源检出")
            run = create_run(root / "results/s1b_r1", args.run_id)
            report = execute(root, args.source.resolve(), run)
            write_json(run / "gate_report.json", report)
        validate_report(report, root)
        logging.info("S1-B-R1=%s；S1=PENDING", report["status"])
        return 0 if report["status"] == "PASS" else 1
    except Exception:
        logging.exception("R1验收失败")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
