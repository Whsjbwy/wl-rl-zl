"""临时副本故障注入：检查器必须拒绝缺失测试、篡改证据及任何虚假通过。"""

import hashlib
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import pytest

import scripts.verify_s1a as gate
from scripts.s0_checks import read_json
from scripts.s0_process import create_run
from scripts.s1a_checks import (
    content_fingerprint,
    import_paths,
    inspect_experiments,
    validate_config,
)
from scripts.s1a_experiments import trajectory_errors

ROOT = Path(__file__).resolve().parents[1]


def test_fingerprint_and_import_scope() -> None:
    """新模块必须实际出现在指纹与导入检查中，旧 S0 指纹定义没有被改写。"""
    files = content_fingerprint(ROOT)["files"]
    assert {"auv_env/config.py", "auv_env/state.py", "auv_env/remus_adapter.py"}.issubset(files)
    assert "auv_env.remus_adapter" in import_paths(ROOT)
    source = (ROOT / "scripts/s0_checks.py").read_text(encoding="utf-8")
    assert 'for directory in ("scripts", "tests", "configs", ".github")' in source


def test_missing_required_tests(tmp_path: Path) -> None:
    """即使唯一测试通过，缺失新阶段测试模块也不能放行。"""
    path = tmp_path / "unit.xml"
    path.write_text('<testsuite><testcase classname="tests.test_s0" name="one"/></testsuite>')
    with pytest.raises(ValueError, match="缺失"):
        gate.check_tests(path)


@pytest.mark.parametrize("fault", ["nonzero", "timeout"])
def test_actual_child_failure_reaches_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    """通过真实退出7/超时子进程驱动整个验收入口，确保报告失败且返回1。"""
    run = create_run(ROOT / "results/selftests_s1a")
    monkeypatch.setattr(gate, "create_run", lambda output, run_id: run)
    code = "raise SystemExit(7)" if fault == "nonzero" else "import time; time.sleep(5)"
    monkeypatch.setattr(
        gate,
        "build_commands",
        lambda root, source, output: [(name, ["-c", code]) for name in sorted(gate.COMMAND_NAMES)],
    )
    real_runner = gate.run_command

    def run_short(
        name: str, argv: list[str], cwd: Path, logs: Path, timeout_s: float, env: dict[str, str]
    ) -> dict:
        return real_runner(name, argv, cwd, logs, 0.1 if fault == "timeout" else 10, env)

    monkeypatch.setattr(gate, "run_command", run_short)
    monkeypatch.setattr(sys, "argv", ["verify_s1a", "--source", str(tmp_path)])
    assert gate.main() == 1
    report = read_json(run / "gate_report.json")
    assert report["status"] == "FAIL"
    assert all(
        record["exit_code"] == (7 if fault == "nonzero" else 124) for record in report["commands"]
    )


@pytest.fixture
def synthetic_report(tmp_path: Path) -> dict:
    """仅供门槛故障注入的合成报告骨架；绝不作为实验结果或提交证据。"""
    path = tmp_path / "sample.txt"
    path.write_bytes(b"synthetic unit fixture")
    return {
        "stage": "S1-A",
        "parent_stage": "S1",
        "parent_status": "PENDING",
        "task_version": "1.0",
        "status": "FAIL",
        "base_sha": "0" * 40,
        "source_commit": "c2dfbd4f4425d29bdcba98294563cda19890cdb1",
        "run_id": "synthetic-only",
        "run_directory": ".",
        "tested_content_sha256": "0" * 64,
        "config_sha256": "0" * 64,
        "environment": {"os": "test", "python": "test", "device": "CPU"},
        "commands": [
            {
                "name": name,
                "argv": ["fixture"],
                "exit_code": 1,
                "duration_s": 0,
                "stdout": "synthetic",
                "stderr": "synthetic",
            }
            for name in sorted(gate.COMMAND_NAMES)
        ],
        "checks": [
            {"name": name, "status": "FAIL", "details": {"synthetic": True}}
            for name in sorted(gate.CHECK_NAMES)
        ],
        "artifacts": [
            {"path": "sample.txt", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        ],
        "unresolved_issues": ["synthetic unit fixture"],
    }


def test_hash_mismatch(tmp_path: Path, synthetic_report: dict) -> None:
    """即便失败报告也必须验证证据哈希，不能通过修改文件伪装历史证据。"""
    schema = tmp_path / "docs/schemas"
    schema.mkdir(parents=True)
    shutil.copyfile(ROOT / "docs/schemas/s1a_report.schema.json", schema / "s1a_report.schema.json")
    gate.validate_report(synthetic_report, tmp_path)
    (tmp_path / "sample.txt").write_bytes(b"changed")
    with pytest.raises(ValueError, match="哈希"):
        gate.validate_report(synthetic_report, tmp_path)


@pytest.mark.parametrize(
    "fault",
    ["missing_check", "missing_tests", "hash_mismatch", "incomplete_json", "perturbed_trajectory"],
)
def test_validator_failure_returns_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, synthetic_report: dict, fault: str
) -> None:
    """用实际校验函数注入缺项/错误JSON/扰动轨迹，经过 CLI 异常路径必须退出1。"""
    path = tmp_path / "report.json"
    path.write_text(json.dumps(synthetic_report), encoding="utf-8")
    config = read_json(ROOT / "configs/s1a.json")
    real_validate = gate.validate_report
    schema = tmp_path / "docs/schemas"
    schema.mkdir(parents=True)
    shutil.copyfile(ROOT / "docs/schemas/s1a_report.schema.json", schema / "s1a_report.schema.json")

    def inject(report: dict, root: Path) -> None:
        if fault == "missing_check":
            report["checks"].pop()
            real_validate(report, tmp_path)
        elif fault == "missing_tests":
            (tmp_path / "unit.xml").write_text(
                '<testsuite><testcase classname="tests.test_s0" name="only"/></testsuite>'
            )
            gate.check_tests(tmp_path / "unit.xml")
        elif fault == "hash_mismatch":
            (tmp_path / "sample.txt").write_bytes(b"changed")
            real_validate(report, tmp_path)
        elif fault == "incomplete_json":
            (tmp_path / "metrics.json").write_text("{}")
            inspect_experiments(tmp_path, config)
        else:
            expected = np.zeros((10001, 19))
            expected[:, 0] = np.arange(10001) * 0.02
            actual = expected.copy()
            actual[10, 3] = 1.0
            trajectory_errors(expected, actual, config)

    monkeypatch.setattr(gate, "validate_report", inject)
    monkeypatch.setattr(sys, "argv", ["verify_s1a", "--check-report", str(path)])
    assert gate.main() == 1


def test_thresholds_cannot_be_relaxed() -> None:
    """任务门槛不可静默降低。"""
    config = read_json(ROOT / "configs/s1a.json")
    config["atol"] = 1
    with pytest.raises(ValueError):
        validate_config(config)
