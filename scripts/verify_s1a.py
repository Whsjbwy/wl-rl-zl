"""S1-A 一键验收；所有故障保留日志，S1-A通过不等于整个S1通过。"""

import argparse
import logging
import os
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import jsonschema

from scripts.import_s0 import SOURCE_SHA
from scripts.s0_checks import check_junit, digest, read_json, verify_source
from scripts.s0_process import create_run, run_command
from scripts.s1a_checks import (
    content_fingerprint,
    import_paths,
    inspect_experiments,
    validate_config,
)
from scripts.verify_s0 import environment_metadata, write_json
from scripts.verify_s0 import validate_report as validate_s0

Json = dict[str, Any]
COMMAND_NAMES = {"ruff", "format", "types", "unit", "s0", "experiments", "demo"}
CHECK_NAMES = COMMAND_NAMES | {
    "config",
    "source",
    "imports",
    "unit_collection",
    "s0_results",
    "numerical",
    "fingerprint",
    "history",
}
HISTORY = (
    "gate_report.json",
    "docs/reports/S0_验收报告.md",
    "docs/provenance/source_manifest.json",
)


def check_tests(path: Path) -> Json:
    """检查 JUnit 无失败/跳过，并要求全部五类测试模块实际出现。"""
    result = check_junit(path)
    classes = {
        case.attrib.get("classname", "") for case in ElementTree.parse(path).iter("testcase")
    }
    required = {
        "tests.test_s0",
        "tests.test_remus_adapter",
        "tests.test_state_snapshot",
        "tests.test_s1a_regression",
        "tests.test_s1a_gate",
    }
    if not required.issubset(classes):
        raise ValueError("必需测试模块缺失")
    return {**result, "modules": sorted(classes)}


def build_commands(root: Path, source: Path, run: Path) -> list[tuple[str, list[str]]]:
    """明确登记每条实际执行的 argv；不经 shell 管道，不隐藏失败。"""
    return [
        ("ruff", ["-m", "ruff", "check", "auv_env", "scripts", "tests"]),
        ("format", ["-m", "ruff", "format", "--check", "auv_env", "scripts", "tests"]),
        ("types", ["-m", "mypy", "auv_env", "scripts"]),
        ("unit", ["-m", "pytest", "tests", "-q", f"--junitxml={run / 'unit.xml'}"]),
        ("s0", ["-m", "scripts.s1a_s0", "--source", str(source), "--output", str(run / "s0")]),
        ("experiments", ["-m", "scripts.s1a_experiments", "--output", str(run / "experiments")]),
        (
            "demo",
            [
                "-m",
                "scripts.demo_s1a_replay",
                "--input",
                str(run / "experiments/snapshots"),
                "--output",
                str(run / "replay.png"),
            ],
        ),
    ]


def validate_report(report: Json, root: Path, *, inspect: bool = True) -> None:
    """校验结构、必需命令/测试、文件哈希及原始轨迹；缺失证据不能放行。"""
    jsonschema.validate(report, read_json(root / "docs/schemas/s1a_report.schema.json"))
    checks = report["checks"]
    if {c["name"] for c in checks} != CHECK_NAMES or len(checks) != len(CHECK_NAMES):
        raise ValueError("必需检查缺失或重复")
    if {c["name"] for c in report["commands"]} != COMMAND_NAMES or len(report["commands"]) != len(
        COMMAND_NAMES
    ):
        raise ValueError("必需命令缺失或重复")
    for artifact in report["artifacts"]:
        path = (root / artifact["path"]).resolve()
        if not path.is_relative_to(root.resolve()) or digest(path) != artifact["sha256"]:
            raise ValueError("证据哈希不符或路径越界")
    if report["status"] == "PASS":
        if (
            any(c["status"] != "PASS" for c in checks)
            or any(c["exit_code"] != 0 for c in report["commands"])
            or report["unresolved_issues"]
        ):
            raise ValueError("存在失败/缺失检查，禁止 PASS")
        run = root / report["run_directory"]
        config = read_json(run / "resolved_config.json")
        validate_config(config)
        if digest(run / "resolved_config.json") != report["config_sha256"]:
            raise ValueError("配置哈希不符")
        check_tests(run / "unit.xml")
        validate_s0(read_json(run / "s0/gate_report.json"), root)
        if read_json(run / "s0/gate_report.json")["status"] != "PASS":
            raise ValueError("S0 回归未通过")
        if inspect:
            inspect_experiments(run / "experiments", config)


def execute(root: Path, source: Path, run: Path) -> Json:
    """执行全部门槛，错误状态逐项归档；原始 CSV、完整状态与 S0 输出均独立保留。"""
    config = read_json(root / "configs/s1a.json")
    write_json(run / "resolved_config.json", config)
    before = content_fingerprint(root)
    write_json(run / "tested_content.json", before)
    history_before = {name: digest(root / name) for name in HISTORY}
    checks: list[Json] = []
    commands: list[Json] = []

    def check(name: str, operation: Callable[[], Any]) -> None:
        started = time.perf_counter()
        logging.info("检查 %s", name)
        try:
            details = operation()
            checks.append({"name": name, "status": "PASS", "details": details})
        except Exception as error:
            # 门槛失败显式进入报告与非零退出；不将异常当成成功或跳过。
            logging.exception("检查失败 %s", name)
            (run / f"{name}.exception.log").write_text(traceback.format_exc(), encoding="utf-8")
            checks.append({"name": name, "status": "FAIL", "details": {"error": str(error)}})
        checks[-1]["duration_s"] = time.perf_counter() - started

    check("config", lambda: validate_config(config))
    check("source", lambda: verify_source(root, source))
    check("imports", lambda: import_paths(root))
    env = os.environ.copy()
    env.update(
        PYTHONUTF8="1",
        MPLBACKEND="Agg",
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=os.pathsep.join([str(root), str(root / "PythonVehicleSimulator/src")]),
    )
    for name, arguments in build_commands(root, source, run):
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
    check("unit_collection", lambda: check_tests(run / "unit.xml"))

    def s0_results() -> Json:
        report = read_json(run / "s0/gate_report.json")
        validate_s0(report, root)
        if report["status"] != "PASS":
            raise ValueError("S0 本次复跑失败")
        return {"status": report["status"], "checks": report["checks"]}

    check("s0_results", s0_results)
    check("numerical", lambda: inspect_experiments(run / "experiments", config))

    def unchanged() -> Json:
        if before != content_fingerprint(root):
            raise ValueError("被测代码发生变化")
        return {"sha256": before["sha256"]}

    def history() -> Json:
        after = {name: digest(root / name) for name in HISTORY}
        if history_before != after:
            raise ValueError("S0 历史证据被修改")
        return after

    check("fingerprint", unchanged)
    check("history", history)
    issues = [c["name"] for c in checks if c["status"] != "PASS"]
    artifacts = [
        {"path": p.relative_to(root).as_posix(), "sha256": digest(p)}
        for p in sorted(run.rglob("*"))
        if p.is_file() and "baseline" not in p.parts
    ]
    # 冻结副本源码不重复归档；其中真实 S0 数值输出由其子报告独立列出并校验。
    return {
        "stage": "S1-A",
        "repository": "Whsjbwy/wl-rl-zl",
        "parent_stage": "S1",
        "parent_status": "PENDING",
        "task_version": "1.0",
        "status": "FAIL" if issues else "PASS",
        "base_sha": read_json(root / "docs/reports/S0_交付回执.json")["s1a_base_sha"],
        "tested_base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
        .decode()
        .strip(),
        "source_commit": SOURCE_SHA,
        "run_id": run.name,
        "run_directory": run.relative_to(root).as_posix(),
        "tested_content_sha256": before["sha256"],
        "config_sha256": digest(run / "resolved_config.json"),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
        ),
        "environment": environment_metadata(),
        "commands": commands,
        "checks": checks,
        "artifacts": artifacts,
        "unresolved_issues": issues,
        "next_stage_authorized": False,
    }


def main() -> int:
    """执行验收或只核验已有报告；非法输入、子进程和证据失败均返回1。"""
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
            raise ValueError("必须提供 --source 固定源对象仓库")
        run = create_run(root / "results/s1a", args.run_id)
        report = execute(root, args.source.resolve(), run)
        validate_report(report, root, inspect=False)
        write_json(run / "gate_report.json", report)
        logging.info("S1-A=%s；整个 S1=PENDING；目录=%s", report["status"], run)
        return 0 if report["status"] == "PASS" else 1
    except Exception:
        logging.exception("S1-A 验收失败；不生成 PASS")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
