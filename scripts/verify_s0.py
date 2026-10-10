"""S0 一键验收：原始证据留在唯一目录，任何必需检查失败返回非零。"""

import argparse
import importlib.metadata
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.import_s0 import SOURCE_SHA
from scripts.s0_checks import (
    Json,
    check_junit,
    check_lock,
    check_results,
    digest,
    fingerprint,
    read_json,
    validate_config,
    verify_source,
)
from scripts.s0_process import create_run, run_command

REQUIRED = [
    "environment",
    "source",
    "lock",
    "ruff",
    "format",
    "types",
    "unit",
    "unit_results",
    "import_path",
    "upstream",
    "upstream_results",
    "regression",
    "numerical",
    "pip_check",
    "source_after",
    "fingerprint_after",
]


def write_json(path: Path, value: Json) -> None:
    """以 UTF-8 保存结构化运行证据；写入失败不吞异常。"""
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def check_environment() -> Json:
    """S0 本机门槛要求 Windows/Python 3.12，GPU 不作为 CPU 仿真门槛。"""
    if platform.system() != "Windows" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("需要 Windows / Python 3.12，当前环境不能代替目标验收")
    return {"os": platform.platform(), "python": sys.version, "processor": platform.processor()}


def environment_metadata() -> Json:
    """只采集相关运行元数据，GPU 名称读取不代表 CUDA 仿真测试。"""
    cpu_name = platform.processor()
    if platform.system() == "Windows":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            cpu_name = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    gpu_info = "NOT_AVAILABLE"
    if shutil.which("nvidia-smi"):
        try:
            gpu_info = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
                timeout=10,
                text=True,
            ).strip()
        except (OSError, subprocess.SubprocessError) as error:
            gpu_info = f"NOT_AVAILABLE: {type(error).__name__}"
    dependencies = {str(d.metadata["Name"]): d.version for d in importlib.metadata.distributions()}
    return {
        "os": platform.platform(),
        "python": sys.version,
        "executable": sys.executable,
        "processor": cpu_name,
        "device": "CPU",
        "gpu_info": gpu_info,
        "gpu_validation": "NOT_RUN",
        "dependencies": dependencies,
    }


def isolated_copy(root: Path, run: Path) -> Path:
    """只复制清单中的冻结文件；入口按 __file__ 输出，从物理路径隔离历史。"""
    isolated = run / "baseline"
    isolated.mkdir()
    for entry in read_json(root / "docs/provenance/source_manifest.json")["files"]:
        relative = entry["target_path"]
        target = isolated / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / relative, target)
    return isolated


def assert_import(command: Json, isolated: Path) -> Json:
    """确认实际 Python 导入来源位于隔离副本；避免 editable 指向其他仓库。"""
    if command["exit_code"] != 0:
        raise ValueError("模块导入失败")
    actual = Path(Path(command["stdout"]).read_text().strip()).resolve()
    expected = isolated / "PythonVehicleSimulator/src/python_vehicle_simulator/vehicles/remus100.py"
    if actual != expected.resolve():
        raise ValueError(f"错误的实际模块路径: {actual}")
    return {"module_path": str(actual)}


def validate_report(report: Json, root: Path) -> None:
    """结构及门槛一致性校验；PASS 必须覆盖所有必需项且命令均成功。"""
    import jsonschema

    jsonschema.validate(report, read_json(root / "docs/schemas/gate_report.schema.json"))
    statuses = {item["name"]: item["status"] for item in report["checks"]}
    if set(statuses) != set(REQUIRED) or len(report["checks"]) != len(REQUIRED):
        raise ValueError("报告必需检查缺失或重复")
    if report["status"] == "PASS" and (
        any(value != "PASS" for value in statuses.values())
        or any(command["exit_code"] != 0 for command in report["commands"])
        or report["unresolved_issues"]
    ):
        raise ValueError("报告存在失败或未执行检查，不能标 PASS")
    if report["status"] == "PASS":
        names = {c["name"] for c in report["commands"]}
        required_commands = {
            "ruff",
            "format",
            "types",
            "unit",
            "module_probe",
            "upstream",
            "regression",
            "pip_check",
        }
        if names != required_commands or len(report["commands"]) != len(required_commands):
            raise ValueError("报告缺少必需命令证据或命令重复")
        if not report["artifacts"]:
            raise ValueError("报告缺少原始产物证据")
    for artifact in report["artifacts"]:
        path = root / artifact["path"]
        if digest(path) != artifact["sha256"]:
            raise ValueError("报告产物哈希不符")


def execute(root: Path, source: Path, run: Path) -> Json:
    """执行全部 S0 门槛，记录异常堆栈并保留后续可运行检查，不静默成功。"""
    config = read_json(root / "configs/s0.json")
    validate_config(config)
    write_json(run / "resolved_config.json", config)
    before = fingerprint(root)
    write_json(run / "tested_content.json", before)
    checks: list[Json] = []
    commands: list[Json] = []

    def check(name: str, operation: Callable[[], Json]) -> bool:
        started = time.perf_counter()
        try:
            details = operation()
            checks.append({"name": name, "status": "PASS", "details": details})
            return True
        except (
            OSError,
            ValueError,
            RuntimeError,
            KeyError,
            TypeError,
            subprocess.SubprocessError,
            importlib.metadata.PackageNotFoundError,
        ) as error:
            logging.exception("检查失败: %s", name)
            (run / f"{name}.exception.log").write_text(traceback.format_exc(), encoding="utf-8")
            checks.append({"name": name, "status": "FAIL", "details": {"error": str(error)}})
            return False
        finally:
            checks[-1]["duration_s"] = time.perf_counter() - started

    def command(name: str, args: list[str], cwd: Path, env: dict[str, str]) -> Json:
        record = run_command(name, [sys.executable, *args], cwd, run, config["timeout_s"], env)
        commands.append(record)
        checks.append(
            {
                "name": name,
                "status": record["status"],
                "details": {"exit_code": record["exit_code"]},
            }
        )
        return record

    environment_ok = check("environment", check_environment)
    source_ok = check("source", lambda: verify_source(root, source))
    lock_ok = check("lock", lambda: check_lock(root))
    env = os.environ.copy()
    env.update({"MPLBACKEND": "Agg", "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    for name, args in [
        ("ruff", ["-m", "ruff", "check", "scripts", "tests"]),
        ("format", ["-m", "ruff", "format", "--check", "scripts", "tests"]),
        ("types", ["-m", "mypy", "scripts"]),
        ("unit", ["-m", "pytest", "tests", "-q", f"--junitxml={run / 'unit.xml'}"]),
    ]:
        command(name, args, root, env)
    check("unit_results", lambda: check_junit(run / "unit.xml"))
    if source_ok and environment_ok and lock_ok:
        isolated = isolated_copy(root, run)
        env["PYTHONPATH"] = str(isolated / "PythonVehicleSimulator/src")
        probe = run_command(
            "module_probe",
            [
                sys.executable,
                "-c",
                "import importlib; "
                "print(importlib.import_module('python_vehicle_simulator.vehicles.remus100').__file__)",
            ],
            isolated,
            run,
            config["timeout_s"],
            env,
        )
        commands.append(probe)
        if check("import_path", lambda: assert_import(probe, isolated)):
            command(
                "upstream",
                [
                    "-m",
                    "pytest",
                    "PythonVehicleSimulator/tests/test_propeller_advance_speed.py",
                    "PythonVehicleSimulator/tests/test_crossflow_drag.py",
                    "PythonVehicleSimulator/tests/test_simulate.py::test_remus100",
                    "-q",
                    f"--junitxml={run / 'upstream.xml'}",
                ],
                isolated,
                env,
            )
            check(
                "upstream_results",
                lambda: check_junit(run / "upstream.xml", config["expected_upstream_tests"]),
            )
            command("regression", ["run_remus.py", "--no-animation"], isolated, env)
            check("numerical", lambda: check_results(isolated / "results/remus100", config))
    command("pip_check", ["-m", "pip", "check"], root, env)
    check("source_after", lambda: verify_source(root, source))

    def unchanged() -> Json:
        after = fingerprint(root)
        if before != after:
            raise ValueError("测试期间代码或配置发生变化")
        return {"sha256": after["sha256"]}

    check("fingerprint_after", unchanged)
    for name in REQUIRED:
        if name not in {c["name"] for c in checks}:
            checks.append(
                {"name": name, "status": "NOT_RUN", "details": {"reason": "前置检查未通过"}}
            )
    issues = [c["name"] for c in checks if c["status"] != "PASS"]
    blocked = not (source_ok and environment_ok and lock_ok)
    artifacts = [
        {"path": p.relative_to(root).as_posix(), "sha256": digest(p)}
        for p in sorted(run.rglob("*"))
        if p.is_file() and "baseline" not in p.parts
    ]
    results = run / "baseline/results/remus100"
    artifacts += [
        {"path": p.relative_to(root).as_posix(), "sha256": digest(p)}
        for p in sorted(results.glob("*"))
        if p.is_file()
    ]
    return {
        "stage": "S0",
        "task_version": "1.0",
        "protocol_version": "仓库审计版_V1",
        "status": "BLOCKED" if blocked else "FAIL" if issues else "PASS",
        "target_repository": "Whsjbwy/wl-rl-zl",
        "source_commit": SOURCE_SHA,
        "tested_base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root)
        .decode()
        .strip(),
        "tested_content_sha256": before["sha256"],
        "config_sha256": digest(root / "configs/s0.json"),
        "working_tree_dirty": bool(
            subprocess.check_output(["git", "status", "--porcelain"], cwd=root)
        ),
        "execution_environment": environment_metadata(),
        "run_id": run.name,
        "commands": commands,
        "checks": checks,
        "artifacts": artifacts,
        "thresholds": config,
        "unresolved_issues": issues,
        "windows_validation": "PASS" if not issues else "NOT_RUN",
        "remote_sync": {"status": "PENDING", "ci_status": "NOT_RUN"},
        "next_stage": {"name": "S1", "authorized": False},
    }


def main() -> int:
    """命令行入口；重复目录或配置错误返回 1，不覆盖历史报告。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path, help="含固定提交对象的来源临时仓库")
    parser.add_argument("--run-id")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        run = create_run(root / "results/s0", args.run_id)
        report = execute(root, args.source.resolve(), run)
        validate_report(report, root)
        write_json(run / "gate_report.json", report)
        logging.info("验收结束: %s；证据目录 %s", report["status"], run)
        return 0 if report["status"] == "PASS" else 1
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        logging.exception("验收入口失败；不得判定 PASS")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
