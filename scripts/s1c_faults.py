"""临时证据副本中的真实CLI故障注入；先正向对照，原始记录不修改。"""

import copy
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from scripts.s0_checks import digest
from scripts.s0_process import run_command
from scripts.strict_json import read_json, write_json

FAULTS = (
    "nan",
    "both_wrong",
    "rotation_term",
    "temporal_term",
    "spatial_term",
    "field_hash",
    "parameter_hash",
    "missing_trajectory",
    "missing_tests",
    "summary",
    "nonzero",
    "timeout",
    "invalid_json",
)


def execute(root: Path, run: Path) -> dict[str, Any]:
    """每个错误通过verify_s1c.py --check-report非零拒绝，同步产物哈希。"""
    output = run / "fault_injection"
    output.mkdir()
    fixture = run / "fault_fixture"
    fixture.mkdir()
    for name in ("numeric", "trajectories", "atomic", "parent"):
        shutil.copytree(run / name, fixture / name)
    original = read_json(run / "gate_report.json")
    # 正向夹具必须含完整产物表，包括顶层日志、配置和图，不能只复制数值目录。
    for item in original["artifacts"]:
        destination = fixture / item["path"]
        if not destination.resolve().is_relative_to(fixture.resolve()):
            raise ValueError("夹具产物路径越界")
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run / item["path"], destination)
    original["run_directory"] = fixture.relative_to(root).as_posix()
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    report_path = fixture / "gate_report.json"
    write_json(report_path, original)
    argv = [sys.executable, "scripts/verify_s1c.py", "--check-report", str(report_path)]
    positive = run_command("positive", argv, root, output, 300, env)
    if positive["exit_code"] != 0:
        raise ValueError("未改临时副本不能通过，负例无效")
    results = []
    for fault in FAULTS:
        report = copy.deepcopy(original)
        changed: Path | None = None
        backup: bytes | None = None
        if fault in {
            "nan",
            "both_wrong",
            "rotation_term",
            "temporal_term",
            "spatial_term",
            "field_hash",
        }:
            changed = fixture / "numeric/derivatives.json"
            backup = changed.read_bytes()
            data = read_json(changed)
            row = data["samples"][0]
            if fault == "nan":
                row["analytic"]["nu_current_dot"][0] = float("nan")
            elif fault == "both_wrong":
                row["analytic"]["nu_current_dot"][0] = 9.0
                for difference in row["differences"]:
                    difference["body_difference"][0] = 9.0
            elif fault == "field_hash":
                row["field"]["config_hash"] = "0" * 64
            else:
                term = fault.removesuffix("_term")
                row["analytic"][term] = [0, 0, 0]
            changed.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        elif fault == "parameter_hash":
            changed = fixture / "trajectories/convergence-0-0.02.jsonl"
            backup = changed.read_bytes()
            changed.write_bytes(
                backup.replace(b'"parameter_hash": "', b'"parameter_hash": "wrong', 1)
            )
        elif fault in {"missing_trajectory", "missing_tests"}:
            changed = (
                fixture
                / (
                    {
                        "missing_trajectory": "trajectories/smoke-0.jsonl",
                        "missing_tests": "parent/s1b/s1a/unit.xml",
                    }[fault]
                )
            )
            backup = changed.read_bytes()
            changed.unlink()
        elif fault == "summary":
            report["details"]["numeric"]["derivative_max"] = 99.0
        elif fault == "nonzero":
            probe = run_command(
                "actual_nonzero",
                [sys.executable, "-c", "raise SystemExit(7)"],
                root,
                output,
                5,
                env,
            )
            report["commands"][0]["exit_code"] = probe["exit_code"]
        elif fault == "timeout":
            probe = run_command(
                "actual_timeout",
                [sys.executable, "-c", "import time;time.sleep(3)"],
                root,
                output,
                0.05,
                env,
            )
            report["commands"][0]["exit_code"] = probe["exit_code"]
            report["commands"][0]["timed_out"] = probe["timed_out"]
        if changed is not None:
            for item in report["artifacts"]:
                if item["path"] == changed.relative_to(fixture).as_posix() and changed.exists():
                    item["sha256"] = digest(changed)
            if changed.exists():
                (output / (fault + ".input.txt")).write_bytes(changed.read_bytes())
        write_json(report_path, report)
        if fault == "invalid_json":
            report_path.write_text("{broken", encoding="utf-8")
        (output / (fault + ".report.txt")).write_bytes(report_path.read_bytes())
        record = run_command(fault, argv, root, output, 300, env)
        results.append(dict(fault=fault, **record))
        if changed is not None and backup is not None:
            changed.write_bytes(backup)
        if record["exit_code"] != 1:
            raise ValueError(f"故障{fault}未被真实CLI拒绝")
    # 仅删除已验证位于本次目录的临时副本，原始实验/负例输入与日志均保留。
    if fixture.resolve().parent != run.resolve():
        raise ValueError("临时副本路径越界")
    shutil.rmtree(fixture)
    result = dict(positive=positive, cases=results)
    write_json(output / "results.json", result)
    return result
