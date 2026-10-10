"""验收器自检；合成 CSV 只用于测试检查逻辑，不是仿真证据。"""

import copy
import csv
import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.s0_checks as checks_module
import scripts.verify_s0 as verifier
from scripts.import_s0 import SOURCE_SHA, destination, migrate
from scripts.publish_s0 import redact
from scripts.s0_checks import (
    CSV_COLUMNS,
    angle_distance_deg,
    check_junit,
    check_lock,
    check_results,
    digest,
    read_json,
    validate_config,
    verify_source,
)
from scripts.s0_process import create_run, run_command
from scripts.verify_s0 import REQUIRED, assert_import, isolated_copy, validate_report

ROOT = Path(__file__).resolve().parents[1]
CONFIG = read_json(ROOT / "configs/s0.json")


@pytest.fixture
def synthetic_results(tmp_path: Path) -> Path:
    """使用解析直线轨迹测试距离检查；不调用被测复算函数生成预期。"""
    depth_m = CONFIG["reference"]["final_depth_m"]
    length_m = CONFIG["reference"]["path_length_m"]
    north_m = math.sqrt(length_m**2 - depth_m**2)
    with (tmp_path / "simulation.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(CSV_COLUMNS)
        for index in range(10001):
            fraction = index / 10000
            writer.writerow(
                [
                    index * 0.02,
                    north_m * fraction,
                    0,
                    depth_m * fraction,
                    0,
                    0,
                    math.radians(49.99890),
                    *([0] * 12),
                ]
            )
    summary = {
        "samples": 10001,
        "parameters": CONFIG["parameters"],
        "all_states_finite": True,
        "sample_time_s": 0.02,
        "simulation_duration_s": 200.0,
        **CONFIG["reference"],
    }
    (tmp_path / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    return tmp_path


def test_valid_results(synthetic_results: Path) -> None:
    """解析直线应通过，且结果是从 CSV 计算。"""
    result = check_results(synthetic_results, CONFIG)
    assert result["samples"] == 10001
    assert result["actual"]["path_length_m"] == pytest.approx(453.55089)


@pytest.mark.parametrize("change", ["missing", "nan", "time", "columns", "count", "wrong_depth"])
def test_bad_csv(synthetic_results: Path, change: str) -> None:
    """缺失、非有限、时间/形状错位及超阈值都必须拒绝。"""
    path = synthetic_results / "simulation.csv"
    if change == "missing":
        path.unlink()
    else:
        lines = path.read_text().splitlines()
        if change == "count":
            lines.pop()
        elif change == "columns":
            lines[0] = "wrong," + lines[0]
        else:
            row = lines[-1].split(",")
            row[{"nan": 8, "time": 0, "wrong_depth": 3}[change]] = {
                "nan": "nan",
                "time": "199",
                "wrong_depth": "10",
            }[change]
            lines[-1] = ",".join(row)
        path.write_text("\n".join(lines), encoding="utf-8")
    with pytest.raises((OSError, ValueError)):
        check_results(synthetic_results, CONFIG)


@pytest.mark.parametrize(
    "key,value",
    [
        ("final_depth_m", 1),
        ("path_length_m", float("nan")),
        ("samples", 4),
        ("all_states_finite", False),
        ("sample_time_s", 1),
    ],
)
def test_bad_summary(synthetic_results: Path, key: str, value: object) -> None:
    """不能信任 summary 的布尔标记或指标。"""
    path = synthetic_results / "summary.json"
    summary = read_json(path)
    summary[key] = value
    path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError):
        check_results(synthetic_results, CONFIG)


def test_angle_wrap_and_config() -> None:
    """最小角度差跨边界正确；放松门槛被拒绝。"""
    assert angle_distance_deg(179.999, -180.001) == pytest.approx(0)
    bad = copy.deepcopy(CONFIG)
    bad["tolerance"]["path_length_m"] = 100
    with pytest.raises(ValueError):
        validate_config(bad)


def test_unique_run(tmp_path: Path) -> None:
    """重复名称必须拒绝，自动名称必须互不相同。"""
    first = create_run(tmp_path, "one")
    (first / "keep.txt").write_text("保留", encoding="utf-8")
    with pytest.raises(FileExistsError):
        create_run(tmp_path, "one")
    assert (first / "keep.txt").read_text(encoding="utf-8") == "保留"
    assert create_run(tmp_path) != create_run(tmp_path)
    with pytest.raises(ValueError):
        create_run(tmp_path, "../outside")


@pytest.mark.parametrize("code,expected", [("print('ok')", 0), ("raise SystemExit(7)", 7)])
def test_command_exit(tmp_path: Path, code: str, expected: int) -> None:
    """真实子进程退出码必须逐字传递，失败不可变成 PASS。"""
    result = run_command("child", [sys.executable, "-c", code], tmp_path, tmp_path, 10)
    assert result["exit_code"] == expected
    assert result["status"] == ("PASS" if expected == 0 else "FAIL")


def test_timeout_and_missing_executable(tmp_path: Path) -> None:
    """真实超时与启动失败产生独立失败证据。"""
    result = run_command(
        "slow", [sys.executable, "-c", "import time; time.sleep(10)"], tmp_path, tmp_path, 0.1
    )
    assert result["timed_out"] and result["exit_code"] == 124
    result = run_command("absent", [str(tmp_path / "absent.exe")], tmp_path, tmp_path, 1)
    assert result["exit_code"] == 127


def test_junit(tmp_path: Path) -> None:
    """以结构与数量判断，不采信任意 passed 文本。"""
    path = tmp_path / "tests.xml"
    path.write_text('<testsuite><testcase name="ok"/></testsuite>')
    assert check_junit(path, 1)["tests"] == 1
    with pytest.raises(ValueError):
        check_junit(path, 4)
    path.write_text("<testsuite><testcase><skipped/></testcase></testsuite>")
    with pytest.raises(ValueError):
        check_junit(path)


def test_mapping_and_isolation(tmp_path: Path) -> None:
    """副本没有旧结果，每次冻结入口真实位于新目录。"""
    assert destination(".git/config") is None
    assert destination("README.md") == "docs/provenance/source_readme.md"
    isolated = isolated_copy(ROOT, tmp_path)
    assert digest(isolated / "run_remus.py") == digest(ROOT / "run_remus.py")
    assert not (isolated / "results").exists()
    output = tmp_path / "probe.log"
    output.write_text(str(ROOT / "run_remus.py"))
    with pytest.raises(ValueError):
        assert_import({"exit_code": 0, "stdout": str(output)}, isolated)


def test_false_pass_report(tmp_path: Path) -> None:
    """即便 schema 通过，缺失检查或产物哈希错误仍不允许 PASS。"""
    report = {
        "stage": "S0",
        "task_version": "1.0",
        "protocol_version": "V1",
        "status": "PASS",
        "target_repository": "Whsjbwy/wl-rl-zl",
        "source_commit": SOURCE_SHA,
        "tested_base_commit": "0" * 40,
        "tested_content_sha256": "0" * 64,
        "config_sha256": "0" * 64,
        "working_tree_dirty": True,
        "execution_environment": {"os": "test", "python": "test", "device": "CPU"},
        "run_id": "synthetic-test",
        "commands": [],
        "checks": [],
        "artifacts": [],
        "thresholds": CONFIG,
        "unresolved_issues": [],
        "next_stage": {"name": "S1", "authorized": False},
    }
    with pytest.raises(ValueError):
        validate_report(report, ROOT)
    report["checks"] = [{"name": name, "status": "PASS", "details": {}} for name in REQUIRED]
    report["checks"][0]["status"] = "FAIL"
    with pytest.raises(ValueError):
        validate_report(report, ROOT)


def test_source_failure_and_cli(tmp_path: Path) -> None:
    """缺失源对象会导致核验失败；实际 CLI 必须非零且报告不得 PASS。"""
    with pytest.raises(subprocess.CalledProcessError):
        verify_source(ROOT, tmp_path)
    with pytest.raises(subprocess.CalledProcessError):
        migrate(tmp_path, tmp_path)
    # 用重复目录在进入递归自测之前触发 CLI 失败。
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/verify_s0.py"),
            "--source",
            str(tmp_path),
            "--run-id",
            "../invalid",
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode != 0


def test_utf8_dependency_comments() -> None:
    """Windows 默认 GBK 下也必须读取 UTF-8 中文依赖说明。"""
    assert check_lock(ROOT)["ruff"] == "0.13.3"


def test_source_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """既定树映射下正常内容通过；删清单或改源文件均拒绝。"""
    manifest = read_json(ROOT / "docs/provenance/source_manifest.json")
    objects = {entry["source_path"]: entry["source_blob_sha"] for entry in manifest["files"]}
    objects[".gitignore"] = manifest["gitignore_merge"]["source_blob_sha"]
    monkeypatch.setattr(checks_module, "source_objects", lambda source: objects)
    isolated = isolated_copy(ROOT, tmp_path)
    manifest_path = isolated / "docs/provenance/source_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (isolated / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
    assert verify_source(isolated, tmp_path)["files_verified"] == len(manifest["files"])
    entry_path = isolated / "run_remus.py"
    entry_path.write_bytes(entry_path.read_bytes() + b"\n# drift\n")
    with pytest.raises(ValueError, match="漂移"):
        verify_source(isolated, tmp_path)
    manifest["files"].pop()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="遗漏"):
        verify_source(isolated, tmp_path)


def test_reference_tolerance(synthetic_results: Path) -> None:
    """CSV 和摘要一致也不能越过参考值容差。"""
    path = synthetic_results / "simulation.csv"
    rows = list(csv.reader(path.read_text(encoding="utf-8").splitlines()))
    for row in rows[1:]:
        row[3] = str(float(row[3]) + 0.0011)
    with path.open("w", newline="", encoding="utf-8") as stream:
        csv.writer(stream).writerows(rows)
    summary_path = synthetic_results / "summary.json"
    summary = read_json(summary_path)
    summary["final_depth_m"] += 0.0011
    summary_path.write_text(json.dumps(summary), encoding="utf-8")
    with pytest.raises(ValueError, match="门槛"):
        check_results(synthetic_results, CONFIG)


def test_path_redaction(tmp_path: Path) -> None:
    """脱敏同时覆盖普通路径与 JSON 转义路径，保留可解析 JSON。"""
    text = json.dumps({"root": str(ROOT), "source": str(tmp_path), "home": str(Path.home())})
    value = json.loads(redact(text, ROOT, tmp_path))
    assert value == {"root": ".", "source": "<source_checkout>", "home": "<user>"}


@pytest.mark.parametrize("missing_source", [False, True])
def test_gate_failure_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing_source: bool
) -> None:
    """通过真实失败子进程驱动整个入口，断言退出码和报告都不可能通过。"""
    if not missing_source:
        monkeypatch.setattr(verifier, "verify_source", lambda root, source: {"test_stub": True})

    def failed_command(
        name: str,
        argv: list[str],
        cwd: Path,
        logs: Path,
        timeout_s: float,
        env: dict[str, str] | None = None,
    ) -> dict:
        return run_command(
            name, [sys.executable, "-c", "raise SystemExit(7)"], cwd, logs, timeout_s, env
        )

    run = create_run(ROOT / "results/selftests")
    monkeypatch.setattr(verifier, "run_command", failed_command)
    monkeypatch.setattr(verifier, "create_run", lambda output, run_id: run)
    monkeypatch.setattr(sys, "argv", ["verify_s0", "--source", str(tmp_path)])
    assert verifier.main() == 1
    report = read_json(run / "gate_report.json")
    assert report["status"] in {"FAIL", "BLOCKED"}
    assert all(command["exit_code"] == 7 for command in report["commands"])
