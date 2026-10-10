"""S1-A 独立文件复核、门槛校验、导入路径和覆盖新增目录的内容指纹。"""

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from auv_env import RemusSnapshot
from scripts.s0_checks import digest, fingerprint, read_json
from scripts.s1a_experiments import compare_snapshots, trajectory_errors


def validate_config(config: dict[str, Any]) -> None:
    """检查预登记的固定时长和误差门槛；任何降级、缺失或不合法范围均失败。"""
    expected = {
        "dt_s": 0.02,
        "duration_s": 200.0,
        "n_steps": 10000,
        "atol": 1e-10,
        "rtol": 1e-10,
        "time_atol_s": 1e-9,
        "replay_atol": 1e-10,
        "snapshot_groups": 100,
        "replay_steps": 100,
        "task_version": "1.0",
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("S1-A 配置与预登记门槛不符")
    names = [case["name"] for case in config["cases"]]
    if names != ["default", "no_current", "east_current", "west_current", "vary_reference"]:
        raise ValueError("缺少必需回归场景")


def content_fingerprint(root: Path) -> dict[str, Any]:
    """扩展 S0 定义而不更改历史定义；含 auv_env、模式、CI、任务卡与所有相关脚本。"""
    files = fingerprint(root)["files"].copy()
    paths = list((root / "auv_env").glob("*.py"))
    paths += [
        root / "docs/schemas/s1a_report.schema.json",
        root / ".gitattributes",
        root / "docs/tasks/S1A_单步仿真与完整状态快照.md",
    ]
    for path in paths:
        files[path.relative_to(root).as_posix()] = digest(path)
    return {
        "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "files": files,
    }


def import_paths(root: Path) -> dict[str, str]:
    """校验实际适配器、配置、快照及上游模型/积分/控制导入路径，无物理推进。"""
    expected = {
        "auv_env.remus_adapter": "auv_env/remus_adapter.py",
        "auv_env.config": "auv_env/config.py",
        "auv_env.state": "auv_env/state.py",
    }
    for suffix in ("vehicles.remus100", "lib.gnc", "lib.control", "lib.guidance", "lib.mainLoop"):
        expected[f"python_vehicle_simulator.{suffix}"] = (
            "PythonVehicleSimulator/src/python_vehicle_simulator/"
            + suffix.replace(".", "/")
            + ".py"
        )
    actual = {}
    for name, relative in expected.items():
        path = Path(str(importlib.import_module(name).__file__)).resolve()
        if path != (root / relative).resolve():
            raise ValueError(f"实际导入路径错误: {name}={path}")
        actual[name] = str(path)
    return actual


def compare_jsonl(first: Path, second: Path, count: int, atol: float) -> float:
    """读取完整序列逐字段复核，不能仅采信摘要中的 PASS。"""
    left, right = (
        first.read_text(encoding="utf-8").splitlines(),
        second.read_text(encoding="utf-8").splitlines(),
    )
    if len(left) != count or len(right) != count:
        raise ValueError("快照序列长度不足")
    return max(
        compare_snapshots(RemusSnapshot.from_json(a), RemusSnapshot.from_json(b), atol)
        for a, b in zip(left, right, strict=True)
    )


def check_branch_evidence(path: Path, atol: float) -> float:
    """对照分支操作前后的全量原始快照及继续运行序列。"""
    data = read_json(path)
    original = RemusSnapshot.from_json(data["main_before"])
    for name in ("main_after_a", "b_after_a", "main_after_b", "main_after_reads"):
        compare_snapshots(original, RemusSnapshot.from_json(data[name]), 0)
    compare_snapshots(
        RemusSnapshot.from_json(data["a_before_b"]), RemusSnapshot.from_json(data["a_after_b"]), 0
    )
    first, second = data["continued_main"].splitlines(), data["continued_control"].splitlines()
    if len(first) != 10 or len(second) != 10:
        raise ValueError("分支后继续运行证据不足")
    return max(
        compare_snapshots(RemusSnapshot.from_json(a), RemusSnapshot.from_json(b), atol)
        for a, b in zip(first, second, strict=True)
    )


def inspect_experiments(folder: Path, config: dict[str, Any]) -> dict[str, Any]:
    """独立重读全部轨迹、100组全量状态序列、分支/重置证据并核对摘要。"""
    validate_config(config)
    metrics = read_json(folder / "metrics.json")
    if set(metrics) != {"regressions", "snapshots", "resets"}:
        raise ValueError("实验 JSON 不完整")
    if (
        len(metrics["regressions"]) != 5
        or len(metrics["snapshots"]) != 100
        or len(metrics["resets"]) != 5
    ):
        raise ValueError("必需实验组缺失")
    for case, summary in zip(config["cases"], metrics["regressions"], strict=True):
        data = [
            np.loadtxt(folder / case["name"] / f"{name}.csv", delimiter=",", skiprows=1)
            for name in ("upstream", "adapter")
        ]
        expected = {
            "case": case["name"],
            "status": "PASS",
            **trajectory_errors(data[0], data[1], config),
        }
        if summary != expected:
            raise ValueError("轨迹摘要与原始文件不一致")
    for index, summary in enumerate(metrics["snapshots"]):
        prefix = folder / "snapshots" / f"{index:03d}"
        saved = RemusSnapshot.from_json(
            prefix.with_suffix(".snapshot.json").read_text(encoding="utf-8")
        )
        if saved.step_count <= 0 or not any(saved.controller.values()):
            raise ValueError("恢复实验不是非零历史")
        errors = [
            compare_jsonl(
                prefix.with_suffix(".original.jsonl"),
                prefix.with_suffix(f".{name}.jsonl"),
                100,
                config["replay_atol"],
            )
            for name in ("restored", "json")
        ]
        branch_error = check_branch_evidence(
            prefix.with_suffix(".branches.json"), config["replay_atol"]
        )
        if (
            summary["group"] != index
            or summary["steps"] != 100
            or summary["status"] != "PASS"
            or summary["replay_max_abs"] != errors[0]
            or summary["json_max_abs"] != errors[1]
            or summary["branch_max_abs"] != branch_error
            or not summary["rng_and_identifiers_equal"]
        ):
            raise ValueError("快照摘要不符合实际证据")
    for case, summary in zip(config["cases"], metrics["resets"], strict=True):
        error = compare_jsonl(
            folder / f"{case['name']}.reset.jsonl",
            folder / f"{case['name']}.fresh.jsonl",
            100,
            config["replay_atol"],
        )
        if summary != {"case": case["name"], "steps": 100, "max_abs": error, "status": "PASS"}:
            raise ValueError("重置摘要不符合实际证据")
    return metrics
