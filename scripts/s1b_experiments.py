"""S1-B 解析距离、合成扫掠反例、真实监控轨迹和成本记录。"""

import argparse
import json
import logging
import math
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import numpy as np

from auv_env import ControlReference, RemusAdapter, RemusConfig
from auv_env.collision import query_pose
from auv_env.geometry import Box, Pose, Sphere, segment_box
from auv_env.scene import CollisionPolicy, Scene, generate_scene
from auv_env.swept import query_sweep
from auv_env.terrain import Gaussian, Terrain
from auv_env.world_monitor import MonitorSnapshot, WorldMonitor
from scripts.s1a_experiments import compare_snapshots
from scripts.verify_s0 import write_json


def analytical_measurements() -> dict[str, Any]:
    """独立手算平面/球/盒距离及固定差分梯度，保存每个实际误差。"""
    scene = Scene(
        "analytic",
        "analytic",
        Terrain(80.0),
        lower=(-100.0, -100.0, -100.0),
        upper=(100.0, 100.0, 100.0),
    )
    values: list[dict[str, Any]] = []
    for z in (70.0, 79.905, 81.0):
        actual = query_pose(scene, Pose((0, 0, z))).clearance_value_m
        values.append({"case": f"flat-{z}", "expected": 80 - z - 0.095, "actual": actual})
    slope = replace(scene, terrain=Terrain(20.0, 0.3, 0.4))
    expected = (
        20 + 0.3 * 2 + 0.4 * 3 - 19 - 0.705 * abs(0.3 * math.sqrt(3) / 2 + 0.5)
    ) / math.sqrt(1.25) - 0.095
    values.append(
        {
            "case": "slope-tilted",
            "expected": expected,
            "actual": query_pose(
                slope, Pose.from_eta([2, 3, 19, 0, math.pi / 6, 0])
            ).clearance_value_m,
        }
    )
    sphere = replace(scene, terrain=Terrain(1000.0), spheres=(Sphere("s", (0, 2, 0), 0.1),))
    values.append(
        {
            "case": "sphere-side",
            "expected": 1.805,
            "actual": query_pose(sphere, Pose((0, 0, 0))).clearance_value_m,
        }
    )
    for point, expected_distance in (
        ((2, 2, 2), math.sqrt(3)),
        ((0, 0, 0), 0.0),
        ((0, 2, 2), math.sqrt(2)),
    ):
        p = np.array(point, dtype=float)
        actual = segment_box(p, p, Box("b", (-1, -1, -1), (1, 1, 1)))[0]
        values.append({"case": f"box-{point}", "expected": expected_distance, "actual": actual})
    for item in values:
        item["abs_error_m"] = abs(item["actual"] - item["expected"])
        if item["abs_error_m"] > 1e-6:
            raise ValueError("解析误差超过预登记阈值")
    terrain = Terrain(bumps=(Gaussian(10.0, 0.0, 0.0, 15.0, 20.0),))
    gradient_errors = []
    for x, y in ((0.0, 0.0), (7.0, -3.0), (15.0, 20.0)):
        h = 1e-4
        difference = np.array(
            [
                (terrain.depth_at(x + h, y) - terrain.depth_at(x - h, y)) / (2 * h),
                (terrain.depth_at(x, y + h) - terrain.depth_at(x, y - h)) / (2 * h),
            ]
        )
        gradient_actual = np.array(terrain.gradient_at(x, y))
        if not np.allclose(gradient_actual, difference, atol=1e-6, rtol=1e-6):
            raise ValueError("梯度差分核对失败")
        gradient_errors.append(float(np.max(np.abs(gradient_actual - difference))))
    return {
        "distances": values,
        "max_abs_error_m": max(v["abs_error_m"] for v in values),
        "gradient_max_abs_error": max(gradient_errors),
    }


def counterexamples(output: Path, costs: dict[str, list]) -> list[dict[str, Any]]:
    """合成几何端态反例不冒充动力学轨迹；每项期望标签预先写明。"""
    base = Scene(
        "counterexample",
        "counterexample",
        Terrain(1000.0),
        lower=(-100.0, -100.0, -100.0),
        upper=(100.0, 100.0, 100.0),
    )
    thin = replace(base, boxes=(Box("thin", (-0.01, -2, -2), (0.01, 2, 2)),))
    rotating = replace(base, spheres=(Sphere("target", (0.75, 0, 0), 0.06),))
    budget = replace(
        base, spheres=(Sphere("near", (0, 1, 0), 0.1),), policy=CollisionPolicy(max_depth=0)
    )
    uncertain = replace(base, terrain=Terrain(bumps=(Gaussian(50.0, 50.0, 50.0, 1.0, 1.0),)))
    cases = [
        ("thin", thin, Pose((-2, 0, 0)), Pose((2, 0, 0)), "COLLISION"),
        (
            "rotation",
            rotating,
            Pose.from_eta([0, 0, 0, 0, 0, -math.pi / 2]),
            Pose.from_eta([0, 0, 0, 0, 0, math.pi / 2]),
            "COLLISION",
        ),
        ("safe", base, Pose((-2, 0, 0)), Pose((2, 0, 0)), "CLEAR"),
        ("budget", budget, Pose((-2, 0, 0)), Pose((2, 0, 0)), "UNCERTAIN"),
        ("negative_bound", uncertain, Pose((0, 0, 79.8)), Pose((0, 0, 79.8)), "UNCERTAIN"),
        ("terrain_witness", uncertain, Pose((0, 0, 79.95)), Pose((0, 0, 79.95)), "COLLISION"),
    ]
    rows = []
    for name, scene, start, end, label in cases:
        began = time.perf_counter()
        first = query_pose(scene, start)
        costs["static"].append(time.perf_counter() - began)
        began = time.perf_counter()
        last = query_pose(scene, end)
        costs["static"].append(time.perf_counter() - began)
        began = time.perf_counter()
        result = query_sweep(scene, start, end)
        costs["swept"].append(time.perf_counter() - began)
        if result.query.status != label:
            raise ValueError(f"{name}期望{label}，实际{result.query.status}")
        if name in {"thin", "rotation"} and (first.status != "CLEAR" or last.status != "CLEAR"):
            raise ValueError("反例端点不安全，无法检验中途漏检")
        row = {
            "name": name,
            "kind": "synthetic_geometry_test",
            "scene": json.loads(scene.to_json()),
            "start": asdict(start),
            "end": asdict(end),
            "expected": label,
            "actual": asdict(result),
            "endpoint_only_clear": first.status == last.status == "CLEAR",
        }
        rows.append(row)
    write_json(output / "counterexamples.json", {"cases": rows})
    return rows


def diagnostics(output: Path, costs: dict[str, list]) -> list[dict[str, Any]]:
    """三类起伏地形各一条安全/人为撞击轨迹；全部由真实适配器step产生。"""
    reports = []
    for kind, seed in zip(("hills", "ridge", "channel"), (17, 29, 43), strict=True):
        scene = Scene.from_json(
            (Path(__file__).resolve().parents[1] / f"configs/scenes/s1b_{kind}.json").read_text(
                encoding="utf-8"
            )
        )
        if scene != generate_scene(kind, seed):
            raise ValueError("配置场景偏离已登记的生成参数")
        (output / f"{kind}.scene.json").write_text(scene.to_json(), encoding="utf-8")
        for intent in ("safe", "impact"):
            logging.info("固定开发诊断 %s/%s", kind, intent)
            adapter = RemusAdapter(
                RemusConfig(current_mps=0.0, initial_reference=ControlReference(20, 0, 1000))
            )
            eta = (
                [20.0, 20.0, 20.0, 0.0, 0.0, 0.0]
                if intent == "safe"
                else [96.99, 100.0, 20.0, 0.0, 0.0, 0.0]
            )
            nu = (
                [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
                if intent == "safe"
                else [5.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            )
            adapter.reset(eta=np.array(eta), nu=np.array(nu))
            monitor = WorldMonitor(adapter, scene)
            initial = monitor.get_state().to_json()
            lines = [json.dumps({"snapshot": json.loads(initial), "sweep": None})]
            subdivisions = []
            labels = []
            for _ in range(200):
                began = time.perf_counter()
                state, result = monitor.step(ControlReference(20, 0, 1000))
                costs["monitor"].append(time.perf_counter() - began)
                labels.append(result.query.status)
                subdivisions.append(result.query.subdivisions)
                lines.append(
                    json.dumps({"snapshot": json.loads(state.to_json()), "sweep": asdict(result)})
                )
                if state.terminated:
                    break
            terminal = monitor.get_state()
            if (intent == "safe" and terminal.terminated) or (
                intent == "impact" and terminal.termination_reason != "collision"
            ):
                raise ValueError("诊断轨迹不符合预登记安全/撞击标签")
            replay = WorldMonitor(adapter, scene)
            replay.set_state(MonitorSnapshot.from_json(initial))
            for _ in labels:
                replay.step(ControlReference(20, 0, 1000))
            if replay.get_state().to_json() != terminal.to_json():
                raise ValueError("诊断终止重放不一致")
            (output / f"{kind}.{intent}.jsonl").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
            reports.append(
                {
                    "kind": kind,
                    "intent": intent,
                    "scene_hash": scene.scene_hash,
                    "steps": len(labels),
                    "termination_reason": terminal.termination_reason,
                    "labels": labels,
                    "subdivisions": subdivisions,
                    "terminal_replay_equal": True,
                }
            )
    return reports


def neutral_trace(output: Path) -> dict[str, Any]:
    """1000步完整状态中性对照，保存原始裸/监控快照，误差逐字段实际复算。"""
    scene = Scene(
        "neutral",
        "neutral",
        Terrain(1000.0),
        lower=(-1000.0, -1000.0, -1000.0),
        upper=(1000.0, 1000.0, 1000.0),
    )
    adapter = RemusAdapter()
    adapter.reset(eta=np.array([50.0, 50.0, 20.0, 0.0, 0.0, 0.0]))
    monitor = WorldMonitor(adapter, scene)
    errors = []
    bare = []
    observed = []
    for index in range(1000):
        ref = ControlReference(25.0 if index < 500 else 35.0, 0.3 if index < 500 else -0.4, 1300.0)
        expected = adapter.step(ref).state
        state, result = monitor.step(ref)
        if result.query.status != "CLEAR":
            raise ValueError("中性对照发生几何事件")
        errors.append(compare_snapshots(expected, state.physical, 1e-10))
        bare.append(expected.to_json())
        observed.append(state.physical.to_json())
    (output / "neutral.bare.jsonl").write_text("\n".join(bare) + "\n", encoding="utf-8")
    (output / "neutral.monitor.jsonl").write_text("\n".join(observed) + "\n", encoding="utf-8")
    return {"steps": 1000, "max_abs": max(errors), "rng_and_discrete_equal": True}


def execute(output: Path) -> dict[str, Any]:
    """执行独立几何与真实物理诊断；耗时只是本机观测，不宣称实时性。"""
    output.mkdir(parents=True, exist_ok=False)
    costs: dict[str, list] = {"static": [], "swept": [], "monitor": []}
    analytic = analytical_measurements()
    examples = counterexamples(output, costs)
    traces = diagnostics(output, costs)
    neutral = neutral_trace(output)
    write_json(output / "timings.json", costs)
    performance = {
        name: {
            "count": len(values),
            "p50_s": float(np.percentile(values, 50)),
            "p95_s": float(np.percentile(values, 95)),
            "max_s": float(max(values)),
        }
        for name, values in costs.items()
    }
    queries = [row["actual"]["query"] for row in examples]
    labels = [q["status"] for q in queries] + [label for row in traces for label in row["labels"]]
    subdivisions = [q["subdivisions"] for q in queries] + [
        n for row in traces for n in row["subdivisions"]
    ]
    result = {
        "analytic": analytic,
        "counterexamples": examples,
        "diagnostics": traces,
        "neutral": neutral,
        "performance": performance,
        "uncertain_ratio": labels.count("UNCERTAIN") / len(labels),
        "mean_subdivisions": float(np.mean(subdivisions)),
        "status_counts": {name: labels.count(name) for name in ("CLEAR", "COLLISION", "UNCERTAIN")},
    }
    write_json(output / "metrics.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    execute(args.output)
