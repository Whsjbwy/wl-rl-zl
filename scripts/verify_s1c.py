"""S1-C完整物理核验收；父阶段S1仍PENDING，失败保留真实证据。"""

import argparse
import importlib
import json
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
from scripts.s1c_checks import (
    convergence_checks,
    counterexample_checks,
    numeric_checks,
    snapshot_checks,
)
from scripts.strict_json import number, read_json, write_json
from scripts.verify_s0 import environment_metadata
from scripts.verify_s1b_r1 import validate_report as validate_parent

COMMANDS = {"numeric", "trajectories", "atomic", "parent"}


def test_inventory(root: Path, run: Path) -> dict[str, int]:
    """按已审R1的200个ID核对，不只检查测试总数；新模块必须真实收集。"""
    cases = list(ElementTree.parse(run / "parent/s1b/s1a/unit.xml").iter("testcase"))
    actual = {(c.attrib["classname"], c.attrib["name"]) for c in cases}
    required = read_json(root / "configs/s1c_required_tests.json")["required"]
    old = {(c["classname"], c["name"]) for c in required}
    if len(old) != 200 or not old.issubset(actual) or any(len(c) for c in cases):
        raise ValueError("旧200项缺失、失败或跳过")
    for test_module in ("test_s1c_core", "test_s1c_gate", "test_s1c_bundle"):
        if not any(test_module in n for n, _ in actual):
            raise ValueError("S1C新测试未收集")
    for name in (
        "auv_env.currents",
        "auv_env.current_kinematics",
        "auv_env.l1.adapter",
        "auv_env.l1.rhs",
        "auv_env.l1.state",
        "auv_env.l1.actuators",
        "auv_env.l1.parameters",
    ):
        module = importlib.import_module(name)
        if (
            Path(str(module.__file__)).resolve()
            != (root / (name.replace(".", "/") + ".py")).resolve()
        ):
            raise ValueError("L1导入路径错误")
    return {"total": len(cases), "retained": 200}


def recompute(root: Path, run: Path) -> dict[str, Any]:
    """独立重算完整数值、恢复、父报告和原子性；任一缺失不能PASS。"""
    numeric = numeric_checks(run)
    counterexamples = counterexample_checks(run)
    convergence = convergence_checks(run)
    snapshots = snapshot_checks(run)
    atomic = read_json(run / "atomic/results.json")["cases"]
    if len(atomic) != 12 or len({(r["target"], r["error"]) for r in atomic}) != 12:
        raise ValueError("原子故障证据缺失")
    for row in atomic:
        if row["before"] != row["after_exception"] or row["before"] != row["branch"]:
            raise ValueError("异常污染状态")
        if row["retry"] != row["control"] or row["retry"]["step_count"] != 1:
            raise ValueError("异常重试不符")
    parent = read_json(run / "parent/gate_report.json")
    validate_parent(parent, root)
    if parent["status"] != "PASS":
        raise ValueError("R1父验收未通过")
    return dict(
        numeric=numeric,
        derivative_counterexamples=counterexamples,
        convergence=convergence,
        snapshots=snapshots,
        atomic_cases=12,
        tests=test_inventory(root, run),
        parent_status=parent["status"],
    )


def validate_report(report: dict[str, Any], root: Path) -> None:
    """真实--check-report入口校验；数值摘要对应独立复算，命令失败不得放行。"""
    if report["stage"] != "S1-C" or report["parent_status"] != "PENDING":
        raise ValueError("阶段错误")
    schema = read_json(root / "docs/schemas/S1C_gate_report.schema.json")
    if not set(schema["required"]).issubset(report):
        raise ValueError("报告模式必需字段缺失")
    run = (root / report["run_directory"]).resolve()
    if not run.is_relative_to(root.resolve()):
        raise ValueError("报告路径越界")
    if {c["name"] for c in report["commands"]} != COMMANDS or len(report["commands"]) != 4:
        raise ValueError("必需命令缺失")
    for command in report["commands"]:
        number(command["exit_code"], minimum=0, integer=True)
        number(command["duration_s"], minimum=0)
        if command["exit_code"] != 0 or command["timed_out"] is not False:
            raise ValueError("子进程失败/超时")
    # 先语义复核后哈希；故障夹具同步哈希也不能通过。
    actual = recompute(root, run)
    if json.dumps(actual, sort_keys=True) != json.dumps(report["details"], sort_keys=True):
        raise ValueError("S1C摘要与独立复算不符")
    if report["config_sha256"] != digest(root / "configs/s1c.json"):
        raise ValueError("配置哈希不符")
    for item in report["artifacts"]:
        path = (run / item["path"]).resolve()
        if not path.is_relative_to(run) or digest(path) != item["sha256"]:
            raise ValueError("S1C证据哈希不符")
    if report["status"] != "PASS":
        raise ValueError("S1C没有通过")
    if report.get("acceptance_status") == "PASS":
        from scripts.s1c_faults import FAULTS

        faults = report["fault_injection"]
        if (
            len(faults["cases"]) != len(FAULTS)
            or {r["fault"] for r in faults["cases"]} != set(FAULTS)
            or faults["positive"]["exit_code"] != 0
        ):
            raise ValueError("真实CLI故障证据缺失")
        for row in faults["cases"]:
            number(row["exit_code"], minimum=0, integer=True)
            if row["exit_code"] != 1:
                raise ValueError("CLI故障未拒绝")
        if faults != read_json(run / "fault_injection/results.json"):
            raise ValueError("CLI故障摘要与原始结果不符")


def execute(root: Path, source: Path, run: Path) -> dict[str, Any]:
    """唯一新目录执行全部实验和一次嵌套R1，静态与上游检查由父入口执行。"""
    history = {
        p.relative_to(root).as_posix(): digest(p) for p in (root / "docs/reports").glob("S*.md")
    }
    before = fingerprint(root)
    write_json(run / "tested_content.json", before)
    (run / "resolved_config.json").write_bytes((root / "configs/s1c.json").read_bytes())
    env = os.environ.copy()
    env.update(PYTHONUTF8="1", MPLBACKEND="Agg")
    commands = []
    for kind in ("numeric", "trajectories", "atomic", "parent"):
        args = [sys.executable, "-m", "scripts.s1c_worker", kind, "--output", str(run / kind)]
        if kind == "parent":
            args += ["--source", str(source)]
        commands.append(run_command(kind, args, root, run, 900, env))
    details, issues = {}, []
    try:
        details = recompute(root, run)
        if history != {name: digest(root / name) for name in history}:
            raise ValueError("历史报告改变")
        if any(c["exit_code"] != 0 for c in commands) or fingerprint(root) != before:
            raise ValueError("命令失败或被测内容变化")
    except Exception as error:
        issues.append(str(error))
        (run / "failure.log").write_text(traceback.format_exc(), encoding="utf-8")
    return dict(
        stage="S1-C",
        parent_status="PENDING",
        status="FAIL" if issues else "PASS",
        run_id=run.name,
        run_directory=run.relative_to(root).as_posix(),
        base_sha="792cbf746b71792b6ea4e9eba8ba60f525b50be1",
        tested_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
        .decode()
        .strip(),
        working_tree_dirty=bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
        ),
        tested_content_sha256=before["sha256"],
        config_sha256=digest(root / "configs/s1c.json"),
        environment=environment_metadata(),
        commands=commands,
        details=details,
        unresolved_issues=issues,
        acceptance_status="PENDING",
        ci_status="PENDING",
        bundle_status="PENDING",
        artifacts=[
            dict(path=p.relative_to(run).as_posix(), sha256=digest(p))
            for p in sorted(run.rglob("*"))
            if p.is_file() and "baseline" not in p.parts
        ],
    )


def main() -> int:
    """一键执行或只读复核；任何异常返回1并保留堆栈。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--check-report", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    logging.basicConfig(level=logging.INFO)
    try:
        if args.check_report:
            report = read_json(args.check_report)
        else:
            if args.source is None:
                raise ValueError("缺固定来源检出")
            run = create_run(root / "results/s1c", args.run_id)
            report = execute(root, args.source.resolve(), run)
            write_json(run / "gate_report.json", report)
        validate_report(report, root)
        if not args.check_report:
            from scripts.s1c_faults import execute as execute_faults

            try:
                report["fault_injection"] = execute_faults(root, run)
                report["acceptance_status"] = "PASS"
            except Exception as error:
                report["status"] = "FAIL"
                report["acceptance_status"] = "FAIL"
                report["unresolved_issues"].append(str(error))
                write_json(run / "gate_report.json", report)
                raise
            write_json(run / "gate_report.json", report)
        logging.info(
            "S1-C数值报告复核通过；完整验收=%s；S1 PENDING",
            report.get("acceptance_status", "PENDING"),
        )
        return 0
    except Exception:
        logging.exception("S1-C验收失败")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
