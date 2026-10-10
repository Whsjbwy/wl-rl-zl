"""B22：真实子进程和入口故障传播；合成失败夹具不充当实验结果。"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest

import scripts.s1b_checks as checks
import scripts.verify_s1b as gate
from scripts.s0_checks import read_json
from scripts.s0_process import create_run

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("fault", ["nonzero", "timeout"])
def test_B22_child_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str) -> None:
    """真实退出9/超时驱动总入口，必须保留FAIL报告且返回1。"""
    run = create_run(ROOT / "results/selftests_s1b")
    monkeypatch.setattr(gate, "create_run", lambda output, run_id: run)
    code = "raise SystemExit(9)" if fault == "nonzero" else "import time; time.sleep(5)"
    monkeypatch.setattr(
        gate,
        "build_commands",
        lambda source, output: [(name, ["-c", code]) for name in sorted(gate.COMMANDS)],
    )
    runner = gate.run_command

    def short(
        name: str, argv: list[str], cwd: Path, logs: Path, timeout: float, env: dict[str, str]
    ) -> dict:
        return runner(name, argv, cwd, logs, 0.1 if fault == "timeout" else 10, env)

    monkeypatch.setattr(gate, "run_command", short)
    monkeypatch.setattr(sys, "argv", ["verify_s1b", "--source", str(tmp_path)])
    assert gate.main() == 1
    report = read_json(run / "gate_report.json")
    assert report["status"] == "FAIL"
    assert all(c["exit_code"] == (9 if fault == "nonzero" else 124) for c in report["commands"])


@pytest.mark.parametrize(
    "fault", ["missing_test", "missing_artifact", "hash", "incomplete_json", "missing_check"]
)
def test_B22_validator_failure_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """使用实际校验函数，经CLI异常路径返回非零，不能仅口头声明失败。"""
    file = tmp_path / "data.txt"
    file.write_bytes(b"fixture")
    schema = tmp_path / "docs/schemas"
    schema.mkdir(parents=True)
    shutil.copyfile(
        ROOT / "docs/schemas/S1B_gate_report.schema.json", schema / "S1B_gate_report.schema.json"
    )
    report = {
        "stage": "S1-B",
        "parent_stage": "S1",
        "parent_status": "PENDING",
        "task_version": "1.0",
        "status": "FAIL",
        "base_sha": "0" * 40,
        "run_id": "synthetic-only",
        "run_directory": ".",
        "tested_content_sha256": "0" * 64,
        "config_sha256": "0" * 64,
        "environment": {},
        "commands": [{"name": name, "exit_code": 1} for name in sorted(gate.COMMANDS)],
        "checks": [{"name": name, "status": "FAIL"} for name in sorted(gate.CHECKS)],
        "artifacts": [{"path": "data.txt", "sha256": hashlib.sha256(b"fixture").hexdigest()}],
        "unresolved_issues": ["synthetic"],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    original = gate.validate_report

    def failing(record: dict, root: Path) -> None:
        if fault == "missing_test":
            classes = [
                "tests.test_s0",
                "tests.test_remus_adapter",
                "tests.test_state_snapshot",
                "tests.test_s1a_regression",
                "tests.test_s1a_gate",
            ]
            (tmp_path / "unit.xml").write_text(
                "<testsuite>"
                + "".join(f'<testcase classname="{name}" name="only"/>' for name in classes)
                + "</testsuite>"
            )
            checks.test_collection(tmp_path / "unit.xml")
        elif fault == "missing_artifact":
            checks.required_artifacts(tmp_path)
        elif fault == "incomplete_json":
            (tmp_path / "metrics.json").write_text("{}")
            checks.inspect_results(tmp_path)
        else:
            if fault == "hash":
                file.write_bytes(b"changed")
            else:
                record["checks"].pop()
            original(record, tmp_path)

    monkeypatch.setattr(gate, "validate_report", failing)
    monkeypatch.setattr(sys, "argv", ["verify_s1b", "--check-report", str(path)])
    assert gate.main() == 1


def test_B22_fingerprint_recursive_and_thresholds(tmp_path: Path) -> None:
    """所有递归模块/场景/模式/任务卡在指纹中；阈值不允许放宽。"""
    files = checks.fingerprint(ROOT)["files"]
    for path in (
        "auv_env/terrain.py",
        "auv_env/world_monitor.py",
        "configs/scenes/s1b_hills.json",
        "docs/schemas/S1B_gate_report.schema.json",
        "docs/tasks/S1B_三维地形与艇体碰撞监控.md",
    ):
        assert path in files
    assert len(checks.imports(ROOT)) == 6
    config = read_json(ROOT / "configs/s1b.json")
    config["analytic_atol_m"] = 1
    with pytest.raises(ValueError):
        gate.validate_config(config)
