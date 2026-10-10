"""S1-B递归内容指纹、必需测试收集、独立原始记录复核和导入路径。"""

import hashlib
import importlib
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import numpy as np

from auv_env import RemusAdapter
from auv_env.geometry import Pose
from auv_env.scene import Scene, generate_scene
from auv_env.swept import query_sweep
from auv_env.world_monitor import MonitorSnapshot, WorldMonitor
from scripts.s0_checks import digest, read_json
from scripts.s1a_checks import compare_jsonl
from scripts.verify_s1a import check_tests


def fingerprint(root: Path) -> dict[str, Any]:
    """递归覆盖全部新旧代码/测试/场景配置/任务卡/模式/CI，不改变旧指纹定义。"""
    paths: list[Path] = []
    for directory, pattern in (
        ("auv_env", "*.py"),
        ("scripts", "*.py"),
        ("tests", "*.py"),
        ("configs", "*.json"),
        ("docs/schemas", "*.json"),
        ("docs/tasks", "*.md"),
        (".github", "*.yml"),
    ):
        paths.extend((root / directory).rglob(pattern))
    paths += [
        root / name
        for name in (
            "AGENTS.md",
            "pyproject.toml",
            ".gitattributes",
            "requirements-lock.txt",
            "requirements-dev.txt",
            "docs/provenance/source_manifest.json",
            "docs/method_code_map.md",
        )
    ]
    files = {path.relative_to(root).as_posix(): digest(path) for path in sorted(paths)}
    return {
        "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "files": files,
    }


def imports(root: Path) -> dict[str, str]:
    """记录实际新增模块导入位置，拒绝editable指向其他工作区。"""
    result = {}
    for name in ("terrain", "geometry", "scene", "collision", "swept", "world_monitor"):
        path = Path(str(importlib.import_module(f"auv_env.{name}").__file__)).resolve()
        if path != (root / f"auv_env/{name}.py").resolve():
            raise ValueError("导入路径不符")
        result[name] = str(path)
    return result


def test_collection(path: Path) -> dict[str, Any]:
    """旧必需测试保持；实际展开的新用例>=30且B01—B22每组至少一项通过。"""
    old = check_tests(path)
    cases: dict[str, list[str]] = {f"B{i:02}": [] for i in range(1, 23)}
    for test in ElementTree.parse(path).iter("testcase"):
        name = test.attrib.get("name", "")
        match = re.search(r"B\d{2}", name)
        if match and match.group() in cases:
            cases[match.group()].append(name)
    if any(not values for values in cases.values()) or sum(map(len, cases.values())) < 30:
        raise ValueError("S1-B必需测试缺失或新增用例不足30")
    return {**old, "new_cases": sum(map(len, cases.values())), "B01_B22": cases}


def inspect_results(folder: Path) -> dict[str, Any]:
    """重读解析指标、反例、真实短轨迹和中性序列；缺失/被扰动时明确失败。"""
    metrics = read_json(folder / "metrics.json")
    required = {
        "analytic",
        "counterexamples",
        "diagnostics",
        "neutral",
        "performance",
        "uncertain_ratio",
        "mean_subdivisions",
        "status_counts",
    }
    if set(metrics) != required:
        raise ValueError("实验JSON不完整")
    analytic = metrics["analytic"]
    errors = []
    for row in analytic["distances"]:
        error = abs(row["actual"] - row["expected"])
        if not np.isfinite(error) or error > 1e-6 or error != row["abs_error_m"]:
            raise ValueError("解析误差不符")
        errors.append(error)
    if (
        len(errors) != 8
        or max(errors) != analytic["max_abs_error_m"]
        or analytic["gradient_max_abs_error"] > 1e-6
    ):
        raise ValueError("解析记录缺失/超阈值")
    examples = read_json(folder / "counterexamples.json")["cases"]
    labels = {
        "thin": "COLLISION",
        "rotation": "COLLISION",
        "safe": "CLEAR",
        "budget": "UNCERTAIN",
        "negative_bound": "UNCERTAIN",
        "terrain_witness": "COLLISION",
    }
    if examples != metrics["counterexamples"] or {r["name"] for r in examples} != set(labels):
        raise ValueError("反例原始数据/摘要不符")
    for row in examples:
        scene = Scene.from_json(json.dumps(row["scene"]))
        result = query_sweep(scene, Pose(**row["start"]), Pose(**row["end"]))
        if (
            result.query.status != labels[row["name"]]
            or json.loads(json.dumps(asdict(result))) != row["actual"]
        ):
            raise ValueError("反例几何复算不符")
    if len(metrics["diagnostics"]) != 6:
        raise ValueError("开发诊断缺失")
    for summary in metrics["diagnostics"]:
        kind, intent = summary["kind"], summary["intent"]
        scene = Scene.from_json((folder / f"{kind}.scene.json").read_text(encoding="utf-8"))
        if scene != generate_scene(kind, {"hills": 17, "ridge": 29, "channel": 43}[kind]):
            raise ValueError("场景内容不是已登记开发种子")
        rows = [
            json.loads(line)
            for line in (folder / f"{kind}.{intent}.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        first = MonitorSnapshot.from_json(json.dumps(rows[0]["snapshot"]))
        bare = RemusAdapter(first.physical.config)
        bare.set_state(first.physical)
        monitor = WorldMonitor(bare, scene)
        for row in rows[1:]:
            recorded = MonitorSnapshot.from_json(json.dumps(row["snapshot"]))
            actual, sweep = monitor.step(recorded.physical.reference)
            if (
                actual.to_json() != recorded.to_json()
                or json.loads(json.dumps(asdict(sweep))) != row["sweep"]
            ):
                raise ValueError("真实物理轨迹或几何事件被扰动")
        end = monitor.get_state()
        if (
            len(rows) - 1 != summary["steps"]
            or end.termination_reason != summary["termination_reason"]
        ):
            raise ValueError("诊断终止摘要不符")
        if (intent == "safe" and (end.terminated or len(rows) != 201)) or (
            intent == "impact" and end.termination_reason != "collision"
        ):
            raise ValueError("诊断实际标签不符合预登记")
    error = compare_jsonl(
        folder / "neutral.bare.jsonl", folder / "neutral.monitor.jsonl", 1000, 1e-10
    )
    if metrics["neutral"] != {"steps": 1000, "max_abs": error, "rng_and_discrete_equal": True}:
        raise ValueError("监控中性摘要不符")
    timings = read_json(folder / "timings.json")
    for name in ("static", "swept", "monitor"):
        values = metrics["performance"][name]
        samples = np.asarray(timings[name], dtype=float)
        actual_cost = {
            "count": len(samples),
            "p50_s": float(np.percentile(samples, 50)),
            "p95_s": float(np.percentile(samples, 95)),
            "max_s": float(samples.max()),
        }
        if not np.isfinite(samples).all() or actual_cost != values:
            raise ValueError("耗时摘要与原始计时不符")
        if not 0 <= values["p50_s"] <= values["p95_s"] <= values["max_s"] or values["count"] <= 0:
            raise ValueError("成本统计不完整")
    return metrics


def required_artifacts(run: Path) -> None:
    """必需原始序列/场景/图片独立检查，不只相信调用者列出的哈希清单。"""
    files = [
        "experiments/metrics.json",
        "experiments/timings.json",
        "experiments/counterexamples.json",
        "experiments/neutral.bare.jsonl",
        "experiments/neutral.monitor.jsonl",
        "s1a/unit.xml",
        "s1a/gate_report.json",
        "figures/clearance.png",
    ]
    for kind in ("hills", "ridge", "channel"):
        files.extend(
            [
                f"experiments/{kind}.scene.json",
                f"experiments/{kind}.safe.jsonl",
                f"experiments/{kind}.impact.jsonl",
                f"figures/{kind}.png",
            ]
        )
    for relative in files:
        path = run / relative
        if not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"缺失必需产物:{relative}")
        if path.suffix == ".png" and path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("无效PNG产物")
