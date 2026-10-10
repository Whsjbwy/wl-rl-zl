"""从完整原始记录独立复算；不接受摘要自洽、双列同错或仅哈希正确。"""

import json
from pathlib import Path
from typing import Any

import numpy as np

from auv_env.current_kinematics import current_terms, rotation
from auv_env.currents import CurrentField
from auv_env.l1.adapter import L1Adapter
from auv_env.l1.parameters import export_parameters
from auv_env.l1.state import L1Snapshot
from scripts.s1c_legacy_diagnostics import capture
from scripts.s1c_numeric import derivative_record, rhs_record
from scripts.s1c_trajectories import command, field_for
from scripts.strict_json import loads, number, read_json


def close(actual: Any, expected: Any, atol: float = 1e-12, rtol: float = 1e-12) -> None:
    """严格形状与有限数值对照，bool不能冒充物理数据。"""
    a, b = np.asarray(actual), np.asarray(expected)
    if a.shape != b.shape or a.dtype.kind not in "fiu" or not np.isfinite(a).all():
        raise ValueError("数值类型/形状/有限值不符")
    if not np.allclose(a, b, atol=atol, rtol=rtol):
        raise ValueError("独立数值复算不符")


def numeric_checks(run: Path) -> dict[str, Any]:
    """重新计算解析/差分以及冻结独立RHS；不是只比较报告中的两列。"""
    derivative_error, rhs_error, residual = 0.0, np.zeros(6), 0.0
    rhs_squared = np.zeros(6)
    samples = read_json(run / "numeric/derivatives.json")["samples"]
    if len(samples) != 100:
        raise ValueError("100组导数缺失")
    rng = np.random.default_rng(20261011)
    registered = []
    for _ in range(100):
        pose = np.r_[rng.uniform([-20, -20, 10], [20, 20, 70]), rng.uniform(-0.4, 0.4, 3)]
        velocity = np.r_[rng.uniform([0.5, -0.2, -0.2], [2, 0.2, 0.2]), rng.uniform(-0.15, 0.15, 3)]
        actuator = np.r_[rng.uniform(-0.15, 0.15, 2), rng.uniform(100, 1400)]
        registered.append((pose, velocity, actuator, float(rng.uniform(0, 60))))
    for row, (pose, velocity, _, time) in zip(samples, registered, strict=True):
        close(row["eta"], pose, 0, 0)
        close(row["nu"], velocity, 0, 0)
        close(row["time_s"], time, 0, 0)
    for row in samples:
        fresh = derivative_record(np.array(row["eta"]), np.array(row["nu"]), row["time_s"])
        if row["field"] != fresh["field"]:
            raise ValueError("制造场配置/哈希错误")
        for key in ("jacobian", "partial_t"):
            close(row[key], fresh[key])
        for key, value in fresh["analytic"].items():
            close(row["analytic"][key], value)
        if len(row["differences"]) != 2:
            raise ValueError("差分半步缺失")
        for recorded, reference in zip(row["differences"], fresh["differences"], strict=True):
            for key in reference:
                close(recorded[key], reference[key])
            close(recorded["body_difference"], row["analytic"]["nu_current_dot"][:3], 1e-6, 1e-6)
            close(recorded["jacobian_difference"], row["jacobian"], 1e-6, 1e-6)
            close(recorded["temporal_difference"], row["partial_t"], 1e-6, 1e-6)
            derivative_error = max(derivative_error, float(np.max(np.abs(recorded["body_error"]))))
    momentum = read_json(run / "numeric/momentum.json")
    if momentum["parameters"] != export_parameters():
        raise ValueError("参数导出与冻结源码不符")
    if len(momentum["samples"]) != 100:
        raise ValueError("100组右端缺失")
    for row, (pose, velocity, actuator, time) in zip(momentum["samples"], registered, strict=True):
        close(row["eta"], pose, 0, 0)
        close(row["nu"], velocity, 0, 0)
        close(row["actual"], actuator, 0, 0)
        close(row["time_s"], time, 0, 0)
        fresh = rhs_record(
            np.array(row["eta"]),
            np.array(row["nu"]),
            np.array(row["actual"]),
            CurrentField(),
            row["time_s"],
        )
        for key in ("no_current_reference", "momentum_expected", "residual", "normalized_residual"):
            close(row[key], fresh[key])
        for key, value in fresh["terms"].items():
            close(row["terms"][key], value)
        terms = row["terms"]
        rhs_squared += (np.array(terms["nu_dot"]) - fresh["no_current_reference"]) ** 2
        close(terms["nu_dot"], fresh["no_current_reference"], 1e-10, 1e-10)
        rhs_error = np.maximum(
            rhs_error, np.abs(np.array(terms["nu_dot"]) - fresh["no_current_reference"])
        )
        residual = max(residual, float(np.max(np.abs(row["normalized_residual"]))))
        if residual > 1e-10:
            raise ValueError("动量装配残差超标")
        for name in ("CRB", "CA_red"):
            matrix = np.array(terms[name])
            close(matrix + matrix.T, np.zeros((6, 6)))
        relative = np.array(terms["nu_relative"])
        if relative @ np.array(terms["D"]) @ relative < -1e-12:
            raise ValueError("阻尼非耗散")
    actuator_error = np.zeros(3)
    cases = read_json(run / "numeric/actuators.json")["cases"]
    if len(cases) != 9:
        raise ValueError("ZOH九组缺失")
    for row in cases:
        clipped = np.clip(row["raw"], [-np.pi / 12, -np.pi / 12, 0], [np.pi / 12, np.pi / 12, 1525])
        if len(row["rows"]) != 201:
            raise ValueError("ZOH原始阶跃缺失")
        for k, state in enumerate(row["rows"]):
            expected = clipped + (np.array(row["initial"]) - clipped) * np.exp(
                -k * row["dt_s"] / np.array([0.1, 0.1, 1.0])
            )
            close(state["expected"], expected)
            close(state["actual"], expected, 1e-10, 0)
            actuator_error = np.maximum(
                actuator_error, np.abs(np.array(state["actual"]) - expected)
            )
    current_cases = read_json(run / "numeric/l0_l1_current.json")["cases"]
    if len(current_cases) != 4:
        raise ValueError("四类有流项级记录缺失")
    for row in current_cases:
        fresh = rhs_record(
            np.array(row["eta"]),
            np.array(row["nu"]),
            np.array(row["actual"]),
            CurrentField.from_dict(row["field"]),
            row["time_s"],
        )
        for key, value in fresh["terms"].items():
            close(row["terms"][key], value)
        close(row["normalized_residual"], fresh["normalized_residual"])
        current = CurrentField.from_dict(row["field"]).sample(row["eta"][:3], row["time_s"])
        legacy = capture(
            np.array(row["eta"]),
            np.array(row["nu"]),
            np.array(row["actual"]),
            current.velocity_ned_mps,
        )
        close(row["L0_rhs"], legacy["nu_dot"], 1e-10, 1e-10)
        for key, value in legacy.items():
            close(row["L0_terms"][key], value)
            close(row["L1_minus_L0"][key], np.array(row["terms"][key]) - value)
        net = sum(
            (
                np.array(legacy[n]) * sign
                for n, sign in (
                    ("F_applied", 1),
                    ("rigid", -1),
                    ("added", -1),
                    ("damping", -1),
                    ("g", -1),
                    ("current_inertia", 1),
                )
            ),
            start=np.zeros(6),
        )
        close(np.array(fresh["terms"]["M"]) @ legacy["nu_dot"], net, 1e-10, 1e-10)
    return dict(
        derivative_max=derivative_error,
        rhs_max_by_channel=rhs_error.tolist(),
        rhs_rms_by_channel=np.sqrt(rhs_squared / 100).tolist(),
        momentum_normalized_max=residual,
        actuator_max_by_channel=actuator_error.tolist(),
    )


def states(path: Path) -> list[dict[str, Any]]:
    """严格读取每行完整状态并核对模型/场/计时；不消耗RNG。"""
    rows = [loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        L1Snapshot.from_json(json.dumps(row))
    return rows


def geodesic(left: np.ndarray, right: np.ndarray) -> float:
    """SO(3)主转角，atan2避免小角acos舍入失真，处理角度环绕。"""
    matrix = rotation(left).T @ rotation(right)
    sine = (
        np.linalg.norm(
            [matrix[2, 1] - matrix[1, 2], matrix[0, 2] - matrix[2, 0], matrix[1, 0] - matrix[0, 1]]
        )
        / 2
    )
    return float(np.arctan2(sine, (np.trace(matrix) - 1) / 2))


def replay_trajectory(rows: list[dict[str, Any]], scene: int, dt: float, closed: bool) -> None:
    """按预登记初态/场/动作重放每个真实物理步，不能仅比对相邻步数或末值。"""
    field = field_for(scene if closed else (2 if scene == 2 else 0))
    env = L1Adapter(field, dt)
    if rows[0] != json.loads(env.get_state().to_json()):
        raise ValueError("轨迹初态/场/模型不是登记配置")
    for k, row in enumerate(rows[1:]):
        result = env.step() if closed else env.step(raw_command=command(k * dt, scene))
        if row != json.loads(result.state.to_json()):
            raise ValueError("逐步实际重放与轨迹不同")


def convergence_checks(run: Path) -> list[dict[str, Any]]:
    """三场景三步长共同0.02秒时刻，分别复算位置/姿态/线角速度误差。"""
    output = run / "trajectories"
    summary = read_json(output / "convergence.json")
    if len(summary["cases"]) != 9:
        raise ValueError("三步长完整轨迹缺失")
    results = []
    for scene in range(3):
        trajectories = []
        for dt in (0.02, 0.01, 0.005):
            data = states(output / f"convergence-{scene}-{dt}.jsonl")
            if len(data) != round(20 / dt) + 1 or data[-1]["time_s"] != 20:
                raise ValueError("20秒完整轨迹缺失")
            replay_trajectory(data, scene, dt, False)
            trajectories.append(data[:: round(0.02 / dt)])
        differences = []
        for a, b in ((0, 1), (1, 2), (0, 2)):
            left, right = trajectories[a], trajectories[b]
            pose_a, pose_b = np.array([r["eta"] for r in left]), np.array([r["eta"] for r in right])
            nu_error = np.array([r["nu"] for r in left]) - np.array([r["nu"] for r in right])
            values = np.column_stack(
                [
                    np.linalg.norm(pose_a[:, :3] - pose_b[:, :3], axis=1),
                    [geodesic(x[3:], y[3:]) for x, y in zip(pose_a, pose_b, strict=True)],
                    nu_error,
                ]
            )
            differences.append(
                dict(
                    rms=np.sqrt(np.mean(values**2, axis=0)).tolist(),
                    max_abs=np.max(np.abs(values), axis=0).tolist(),
                    common_time_errors=values.tolist(),
                )
            )
        coarse, fine = np.array(differences[0]["rms"]), np.array(differences[1]["rms"])
        active = coarse > 1e-8
        ratios = np.divide(fine, coarse, out=np.zeros_like(fine), where=active)
        if np.any(ratios[active] > 0.8):
            raise ValueError(f"场景{scene}收敛比不满足0.8：{ratios}")
        limits = np.array([0.1, 0.01, 0.03, 0.03, 0.03, 0.01, 0.01, 0.01])
        if np.any(np.array(differences[2]["max_abs"]) > limits):
            raise ValueError(f"场景{scene}工程误差超标")
        results.append(
            dict(
                scene=scene,
                differences=differences,
                ratios=ratios.tolist(),
                observed_order=[float(-np.log2(r)) if r > 0 else None for r in ratios],
            )
        )
    return results


def snapshot_checks(run: Path) -> dict[str, Any]:
    """每组完整100步内存/JSON/分支逐字段复核，RNG及离散字段精确比较。"""
    output = run / "trajectories/snapshots"
    durations: list[float] = []
    integration_steps = 0
    for group in range(100):
        initial = L1Snapshot.from_json(
            (output / f"{group:03}.snapshot.json").read_text(encoding="utf-8")
        )
        reference = states(output / f"{group:03}.original.jsonl")
        if len(reference) != 100 or reference[-1]["step_count"] != initial.step_count + 100:
            raise ValueError("100组各100步证据不完整")
        replay = L1Adapter(CurrentField.from_dict(initial.field), initial.dt_s)
        replay.set_state(initial)
        for row in reference:
            if json.loads(replay.step().state.to_json()) != row:
                raise ValueError("从完整初态实际重放与100步原记录不同")
        for suffix in ("memory", "json", "branch"):
            rows = states(output / f"{group:03}.{suffix}.jsonl")
            if rows != reference:
                raise ValueError("完整快照恢复/分支不同")
        values = read_json(output / f"{group:03}.rng.json")
        if set(values) != {"original", "memory", "json", "branch"} or any(
            v != values["original"] for v in values.values()
        ):
            raise ValueError("RNG恢复不同")
        costs = read_json(output / f"{group:03}.cost.json")
        if (
            costs["group"] != group
            or costs["dt_s"] != 0.02
            or costs["field"] != initial.field
            or costs["integration_steps"] != 401 + group % 11
            or costs["prediction_branch_steps"] != 101
            or len(costs["warmup_duration_s"]) != group % 11
        ):
            raise ValueError("快照成本配置或积分步数不符")
        number(costs["integration_steps"], integer=True, minimum=401)
        number(costs["prediction_branch_steps"], integer=True, minimum=101)
        integration_steps += costs["integration_steps"]
        for key in ("total_duration_s", "branch_probe_duration_s", "three_clones_duration_s"):
            number(costs[key], minimum=0)
        for value in costs["warmup_duration_s"]:
            number(value, minimum=0)
        if set(costs["step_duration_s"]) != {"original", "memory", "json", "branch"}:
            raise ValueError("四分支计时缺失")
        for name, times in costs["step_duration_s"].items():
            if len(times) != 100:
                raise ValueError("快照逐步计时缺失")
            for value in times:
                durations.append(number(value, minimum=0))
            for key, value in (
                ("p50_s", np.percentile(times, 50)),
                ("p95_s", np.percentile(times, 95)),
                ("max_s", max(times)),
            ):
                close(costs["statistics"][name][key], value, 0, 0)
    for scene in range(3):
        rows = states(run / f"trajectories/smoke-{scene}.jsonl")
        if len(rows) != 3001 or rows[-1]["time_s"] != 60:
            raise ValueError("60秒闭环原始轨迹不完整")
        replay_trajectory(rows, scene, 0.02, True)
        last = L1Snapshot.from_json(json.dumps(rows[-1]))
        env = L1Adapter(CurrentField.from_dict(last.field), last.dt_s)
        env.set_state(last)
        if env.get_state().to_json() != last.to_json():
            raise ValueError("闭环末态不可恢复")
    for name in ("convergence", "smoke"):
        cases = read_json(run / f"trajectories/{name}.json")["cases"]
        expected_pairs = {
            (s, d)
            for s in range(3)
            for d in ((0.02, 0.01, 0.005) if name == "convergence" else (0.02,))
        }
        if (
            len(cases) != len(expected_pairs)
            or {(r["scenario"], r["dt_s"]) for r in cases} != expected_pairs
        ):
            raise ValueError("诊断场景/步长组合缺失或重复")
        for row in cases:
            number(row["steps"], minimum=1, integer=True)
            number(row["scenario"], minimum=0, maximum=2, integer=True)
            closed = name == "smoke"
            duration = 60 if closed else 20
            scene = row["scenario"]
            field = field_for(scene if closed else (2 if scene == 2 else 0))
            if (
                row["closed_loop"] is not closed
                or row["duration_s"] != duration
                or row["steps"] != round(duration / row["dt_s"])
                or row["field"] != field.to_dict()
            ):
                raise ValueError("诊断配置摘要不符")
            if len(row["step_duration_s"]) != row["steps"]:
                raise ValueError("计时原值缺失")
            for value in row["step_duration_s"]:
                number(value, minimum=0)
            for key, expected in (
                ("p50_s", np.percentile(row["step_duration_s"], 50)),
                ("p95_s", np.percentile(row["step_duration_s"], 95)),
                ("max_s", max(row["step_duration_s"])),
            ):
                number(row[key], minimum=0)
                close(row[key], expected, 0, 0)
    return dict(
        groups=100,
        steps_per_group=100,
        full_state_max_error=0.0,
        smoke_seconds=[60, 60, 60],
        integration_steps=integration_steps,
        prediction_branch_steps=10100,
        timed_steps=len(durations),
        p50_s=float(np.percentile(durations, 50)),
        p95_s=float(np.percentile(durations, 95)),
        max_s=max(durations),
    )


def counterexample_checks(run: Path) -> dict[str, Any]:
    """四类反例必须唯一；独立激活项与沿Rodrigues轨迹差分一致。"""
    records = read_json(run / "numeric/derivative_counterexamples.json")["cases"]
    labels = {"rotation", "temporal", "spatial", "joint"}
    if len(records) != 4 or {r["label"] for r in records} != labels:
        raise ValueError("三导数项反例缺失或重复")
    norms = {}
    for row in records:
        field = CurrentField.from_dict(row["field"])
        sample = field.sample(row["eta"][:3], row["time_s"])
        expected = current_terms(row["eta"], row["nu"], sample)
        for key, value in expected.items():
            close(row["terms"][key], value)
        difference = (np.array(row["body_plus"]) - row["body_minus"]) / (2 * row["h_s"])
        close(row["difference"], difference)
        close(difference, expected["nu_current_dot"][:3], 1e-6, 1e-6)
        label = row["label"]
        if label != "joint":
            close(expected["nu_current_dot"][:3], expected[label])
            if np.linalg.norm(expected[label]) < 1e-3:
                raise ValueError("反例未激活对应项")
        norms[label] = float(np.linalg.norm(expected["nu_current_dot"]))
    return norms
