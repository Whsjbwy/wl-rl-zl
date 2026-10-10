"""完整L1轨迹、100组恢复与闭环烟雾；绘图另从这些原始记录读取。"""

import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from auv_env.currents import CurrentField
from auv_env.l1.adapter import L1Adapter
from auv_env.l1.state import L1Snapshot
from scripts.strict_json import write_json


def field_for(index: int) -> CurrentField:
    """零流/定常侧流0.3/背景0.2+幅0.05周期60s，无垂向NED流。"""
    if index == 0:
        return CurrentField()
    if index == 1:
        return CurrentField("uniform", background=[0, 0.3, 0])
    return CurrentField(
        "sinusoidal", background=[0, 0.2, 0], amplitude=[0, 0.05, 0], omega_radps=2 * np.pi / 60
    )


def command(time_s: float, scenario: int) -> np.ndarray:
    """登记的光滑开环指令，rad/rad/rpm；直航场景保持固定命令。"""
    if scenario == 0:
        return np.array([0, 0, 900.0])
    return np.array(
        [
            0.005 * np.sin(0.2 * time_s),
            0.003 * np.sin(0.15 * time_s),
            900 + 20 * np.sin(0.1 * time_s),
        ]
    )


def trajectory(
    output: Path, scenario: int, dt: float, duration: float, closed: bool
) -> dict[str, Any]:
    """保存每个真实边界的完整快照；不以绘图重跑推进仿真。"""
    field = field_for(scenario if closed else (2 if scenario == 2 else 0))
    env = L1Adapter(field, dt)
    elapsed = []
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(env.get_state().to_json() + "\n")
        for k in range(round(duration / dt)):
            start = time.perf_counter()
            state = (
                env.step() if closed else env.step(raw_command=command(k * dt, scenario))
            ).state
            elapsed.append(time.perf_counter() - start)
            stream.write(state.to_json() + "\n")
    return dict(
        path=output.name,
        scenario=scenario,
        dt_s=dt,
        duration_s=duration,
        steps=round(duration / dt),
        field=field.to_dict(),
        closed_loop=closed,
        step_duration_s=elapsed,
        p50_s=float(np.percentile(elapsed, 50)),
        p95_s=float(np.percentile(elapsed, 95)),
        max_s=max(elapsed),
    )


def snapshots(output: Path) -> None:
    """100组×100步完整主/内存/JSON/分支；每组消费RNG验证独立恢复。"""
    output.mkdir()
    for group in range(100):
        started = time.perf_counter()
        names = ("original", "memory", "json", "branch")
        costs: dict[str, Any] = {
            "step_duration_s": {name: [] for name in names},
            "warmup_duration_s": [],
            "dt_s": 0.02,
            "group": group,
        }
        env = L1Adapter(field_for(group % 3), seed=20261011 + group)
        for _ in range(group % 11):
            start_step = time.perf_counter()
            env.step()
            costs["warmup_duration_s"].append(time.perf_counter() - start_step)
        env.sample_random(group + 1)
        before = env.get_state()
        (output / f"{group:03}.snapshot.json").write_text(before.to_json(), encoding="utf-8")
        start_copy = time.perf_counter()
        memory, disk, branch = env.clone(), env.clone(), env.clone()
        costs["three_clones_duration_s"] = time.perf_counter() - start_copy
        memory.set_state(before)
        disk.set_state(
            L1Snapshot.from_json((output / f"{group:03}.snapshot.json").read_text(encoding="utf-8"))
        )
        # 外部数组修改、预测分支推进不得影响主/恢复实例。
        before.eta[:] = -999
        main_before = env.get_state().to_json()
        start_probe = time.perf_counter()
        branch.step(raw_command=[0.02, -0.01, 700])
        costs["branch_probe_duration_s"] = time.perf_counter() - start_probe
        if main_before != env.get_state().to_json():
            raise ValueError("预测污染主实例")
        branch.set_state(env.get_state())
        streams = [
            (output / f"{group:03}.{name}.jsonl").open("w", encoding="utf-8", newline="\n")
            for name in ("original", "memory", "json", "branch")
        ]
        try:
            for _ in range(100):
                for name, instance, stream in zip(
                    names, (env, memory, disk, branch), streams, strict=True
                ):
                    start_step = time.perf_counter()
                    state = instance.step().state
                    costs["step_duration_s"][name].append(time.perf_counter() - start_step)
                    stream.write(state.to_json() + "\n")
        finally:
            for stream in streams:
                stream.close()
        write_json(
            output / f"{group:03}.rng.json",
            {
                name: instance.sample_random(7).tolist()
                for name, instance in zip(
                    ("original", "memory", "json", "branch"),
                    (env, memory, disk, branch),
                    strict=True,
                )
            },
        )
        costs["total_duration_s"] = time.perf_counter() - started
        costs["integration_steps"] = 401 + group % 11
        costs["prediction_branch_steps"] = 101
        costs["field"] = field_for(group % 3).to_dict()
        costs["statistics"] = {
            name: dict(
                p50_s=float(np.percentile(values, 50)),
                p95_s=float(np.percentile(values, 95)),
                max_s=max(values),
            )
            for name, values in costs["step_duration_s"].items()
        }
        write_json(output / f"{group:03}.cost.json", costs)


def execute(output: Path) -> None:
    """顺序CPU执行三步长20s及三组60s，不包含训练或地形海流。"""
    output.mkdir(parents=True, exist_ok=False)
    convergence = [
        trajectory(output / f"convergence-{scene}-{dt}.jsonl", scene, dt, 20.0, False)
        for scene in range(3)
        for dt in (0.02, 0.01, 0.005)
    ]
    write_json(output / "convergence.json", {"cases": convergence})
    smoke = [
        trajectory(output / f"smoke-{scene}.jsonl", scene, 0.02, 60.0, True) for scene in range(3)
    ]
    write_json(output / "smoke.json", {"cases": smoke})
    snapshots(output / "snapshots")


def read_states(path: Path) -> list[dict[str, Any]]:
    """仅仅读取全部JSONL；实验复核另行检查有限值与完整字段。"""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
