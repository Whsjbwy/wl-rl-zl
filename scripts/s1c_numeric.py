"""S1-C独立数值实验：完整输入、差分原值与逐项载荷，不只保存最大误差。"""

from pathlib import Path
from typing import Any

import numpy as np
from python_vehicle_simulator.vehicles import remus100

from auv_env.current_kinematics import current_terms, rotation
from auv_env.currents import CurrentField
from auv_env.l1 import actuators, rhs
from auv_env.l1.parameters import export_parameters
from scripts.s1c_legacy_diagnostics import capture
from scripts.strict_json import write_json


def skew(value: np.ndarray) -> np.ndarray:
    """独立叉乘矩阵，用于Rodrigues轨迹而非积分被测海流导数。"""
    x, y, z = value
    return np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])


def rodrigues(omega: np.ndarray, delta_s: float) -> np.ndarray:
    """恒定体轴角速度的精确旋转指数；零角速度返回单位阵。"""
    norm = float(np.linalg.norm(omega))
    if norm == 0:
        return np.eye(3)
    axis = skew(omega / norm)
    angle = norm * delta_s
    return np.eye(3) + np.sin(angle) * axis + (1 - np.cos(angle)) * (axis @ axis)


def manufactured() -> CurrentField:
    """预登记制造场，非真实海域；各导数项均非零。"""
    return CurrentField(
        "affine",
        background=[0.2, -0.1, 0.03],
        jacobian=[[0.002, -0.001, 0.003], [0.001, 0.002, -0.002], [-0.001, 0.001, 0.002]],
        partial_t=[0.004, -0.003, 0.002],
        origin_m=[1, 2, 30],
    )


def derivative_record(eta: np.ndarray, nu: np.ndarray, time: float) -> dict[str, Any]:
    """100组采样中的一组；两步长中心差分独立计算场J、定点偏导和体轴导数。"""
    field = manufactured()
    sample = field.sample(eta[:3], time)
    terms = current_terms(eta, nu, sample)
    records = []
    matrix, path_velocity = rotation(eta[3:]), rotation(eta[3:]) @ nu[:3]
    for h in (1e-4, 5e-5):
        plus, minus = [], []
        for axis in np.eye(3):
            plus.append(field.sample(eta[:3] + h * axis, time).velocity_ned_mps)
            minus.append(field.sample(eta[:3] - h * axis, time).velocity_ned_mps)
        temporal_plus = field.sample(eta[:3], time + h).velocity_ned_mps
        temporal_minus = field.sample(eta[:3], time - h).velocity_ned_mps
        temporal = (temporal_plus - temporal_minus) / (2 * h)
        body_values = []
        for sign in (-1, 1):
            delta = sign * h
            moved = eta[:3] + delta * path_velocity
            body_values.append(
                (matrix @ rodrigues(nu[3:], delta)).T
                @ field.sample(moved, time + delta).velocity_ned_mps
            )
        difference = (body_values[1] - body_values[0]) / (2 * h)
        records.append(
            dict(
                h_s=h,
                h_m=h,
                spatial_plus=np.array(plus).tolist(),
                spatial_minus=np.array(minus).tolist(),
                jacobian_difference=((np.array(plus) - minus) / (2 * h)).T.tolist(),
                temporal_difference=temporal.tolist(),
                temporal_plus=temporal_plus.tolist(),
                temporal_minus=temporal_minus.tolist(),
                body_minus=body_values[0].tolist(),
                body_plus=body_values[1].tolist(),
                body_difference=difference.tolist(),
                body_error=(difference - terms["nu_current_dot"][:3]).tolist(),
            )
        )
    return dict(
        eta=eta.tolist(),
        nu=nu.tolist(),
        time_s=time,
        field=field.to_dict(),
        jacobian=sample.jacobian_per_s.tolist(),
        partial_t=sample.partial_t_mps2.tolist(),
        analytic={k: v.tolist() for k, v in terms.items()},
        differences=records,
    )


def independent_coriolis(mass: np.ndarray, velocity: np.ndarray) -> np.ndarray:
    """独立块矩阵装配，不调用被测m2c；下右块使用角动量。"""
    momentum = mass @ velocity
    matrix = np.zeros((6, 6))
    matrix[:3, 3:] = -skew(momentum[:3])
    matrix[3:, :3] = -skew(momentum[:3])
    matrix[3:, 3:] = -skew(momentum[3:])
    return matrix


def rhs_record(
    eta: np.ndarray, nu: np.ndarray, actual: np.ndarray, field: CurrentField, time: float
) -> dict[str, Any]:
    """独立L0零流右端及动量块装配；保留每个N/Nm项及六通道残差。"""
    sample = field.sample(eta[:3], time)
    result = rhs.evaluate(eta, nu, actual, sample)
    reference, _ = remus100().dynamics(eta.copy(), nu.copy(), actual.copy(), actual.copy(), 1.0)
    relative = result["nu_relative"]
    crb = independent_coriolis(result["MRB"], nu)
    ca = independent_coriolis(result["MA"], relative)
    for i, j in ((4, 0), (0, 4), (4, 2), (2, 4), (5, 0), (0, 5), (5, 1), (1, 5)):
        ca[i, j] = 0
    expected_momentum = (
        result["prop_fin"]
        + result["lift_drag"]
        + result["crossflow"]
        - crb @ nu
        - ca @ relative
        - result["D"] @ relative
        - result["g"]
        + result["MA"] @ result["nu_current_dot"]
    )
    residual = result["M"] @ result["nu_dot"] - expected_momentum
    scale = 1 + sum(
        float(np.linalg.norm(result[k]))
        for k in (
            "prop_fin",
            "lift_drag",
            "crossflow",
            "rigid",
            "added",
            "damping",
            "g",
            "current_inertia",
        )
    )
    return dict(
        eta=eta.tolist(),
        nu=nu.tolist(),
        actual=actual.tolist(),
        time_s=time,
        field=field.to_dict(),
        terms={k: v.tolist() for k, v in result.items()},
        no_current_reference=(reference - nu).tolist(),
        reference_difference_step_s=1.0,
        momentum_expected=expected_momentum.tolist(),
        residual=residual.tolist(),
        normalized_residual=(residual / scale).tolist(),
    )


def execute(output: Path) -> None:
    """固定种子、预登记范围产生100组，每组保留完整输入与原值。"""
    output.mkdir(parents=True, exist_ok=False)
    rng = np.random.default_rng(20261011)
    derivatives, momentum = [], []
    for _ in range(100):
        eta = np.r_[rng.uniform([-20, -20, 10], [20, 20, 70]), rng.uniform(-0.4, 0.4, 3)]
        nu = np.r_[rng.uniform([0.5, -0.2, -0.2], [2, 0.2, 0.2]), rng.uniform(-0.15, 0.15, 3)]
        actual = np.r_[rng.uniform(-0.15, 0.15, 2), rng.uniform(100, 1400)]
        time = float(rng.uniform(0, 60))
        derivatives.append(derivative_record(eta, nu, time))
        momentum.append(rhs_record(eta, nu, actual, CurrentField(), time))
    write_json(output / "derivatives.json", {"samples": derivatives})
    write_json(output / "momentum.json", {"samples": momentum, "parameters": export_parameters()})
    actuator_rows = []
    for dt in (0.02, 0.01, 0.005):
        for raw in ([1.0, -1.0, 2000.0], [-1.0, 1.0, -10.0], [0.0, 0.0, 900.0]):
            initial = np.array([-0.15, 0.1, 700.0])
            value, rows = initial.copy(), []
            for k in range(201):
                clipped = np.clip(raw, actuators.LOWER, actuators.UPPER)
                expected = clipped + (initial - clipped) * np.exp(
                    -k * dt / actuators.TIME_CONSTANT_S
                )
                rows.append(dict(time_s=k * dt, actual=value.tolist(), expected=expected.tolist()))
                _, value = actuators.update(value, raw, dt)
            actuator_rows.append(dict(dt_s=dt, raw=raw, initial=initial.tolist(), rows=rows))
    write_json(output / "actuators.json", {"cases": actuator_rows})
    cases = []
    for i in range(4):
        eta = np.array([0, 0, 30, 0.2 if i else 0, 0.3 if i else 0, 0.4])
        nu = np.array([1.2, 0.1, 0.05, 0.04, -0.03, 0.05])
        if i < 2:
            nu[3:] = 0
        field = CurrentField(
            "sinusoidal" if i == 3 else "uniform",
            background=[0, 0.3, 0],
            amplitude=[0, 0.05, 0],
            omega_radps=2 * np.pi / 60,
        )
        row = rhs_record(eta, nu, np.array([0.02, -0.01, 900.0]), field, 10)
        current = field.sample(eta[:3], 10).velocity_ned_mps
        legacy = remus100(
            V_current=float(np.linalg.norm(current)),
            beta_current=float(np.degrees(np.arctan2(current[1], current[0]))),
        )
        after, _ = legacy.dynamics(
            eta.copy(), nu.copy(), np.array(row["actual"]), np.array(row["actual"]), 1.0
        )
        row["L0_rhs"] = (after - nu).tolist()
        row["L0_terms"] = capture(eta, nu, np.array(row["actual"]), current)
        row["L1_minus_L0"] = {
            name: (np.array(row["terms"][name]) - value).tolist()
            for name, value in row["L0_terms"].items()
        }
        cases.append(row)
    write_json(output / "l0_l1_current.json", {"cases": cases})
