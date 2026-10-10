"""独立上游参考循环；仅直接调用冻结函数，不依赖 auv_env 生成期望轨迹。"""

from typing import Any

import numpy as np
from numpy.typing import NDArray
from python_vehicle_simulator.lib.gnc import attitudeEuler
from python_vehicle_simulator.lib.mainLoop import simulate
from python_vehicle_simulator.vehicles import remus100


def scheduled_reference(step: int, schedule: list[dict[str, Any]], varying: bool) -> dict[str, Any]:
    """按预登记分段选择上游深度 m、航向 deg、rpm；无控制器副作用。"""
    selected = schedule[0]
    if varying:
        for item in schedule:
            if step >= item["start_step"]:
                selected = item
    return selected.copy()


def upstream_trajectory(config: dict[str, Any], case: dict[str, Any]) -> NDArray[np.float64]:
    """返回 (N+1,19) 时间与原始日志；固定参考调用原 simulate，变化参考透明展开。

    保持原控制器先更新记忆、记录步前物理量、更新速度/执行器、更新姿态顺序。
    本参考也保留原末行后额外更新；只比较它已记录的行，不暴露其末次内部状态。
    """
    initial = config["reference_schedule"][0]
    vehicle = remus100(
        "depthHeadingAutopilot",
        initial["depth_m"],
        initial["heading_deg"],
        initial["propeller_rpm"],
        case["current_mps"],
        case["current_direction_deg"],
    )
    dt_s, n_steps = config["dt_s"], config["n_steps"]
    if not case["vary_reference"]:
        times, states = simulate(n_steps, dt_s, vehicle)
        return np.column_stack([times.ravel(), states])
    eta, nu, actuator = np.zeros(6), vehicle.nu, vehicle.u_actual
    rows = np.empty((n_steps + 1, 19), dtype=np.float64)
    for index in range(n_steps + 1):
        reference = scheduled_reference(index, config["reference_schedule"], True)
        vehicle.ref_z = reference["depth_m"]
        vehicle.ref_psi = reference["heading_deg"]
        vehicle.ref_n = reference["propeller_rpm"]
        command = vehicle.depthHeadingAutopilot(eta, nu, dt_s)
        rows[index] = np.concatenate(([index * dt_s], eta, nu, command, actuator))
        nu, actuator = vehicle.dynamics(eta, nu, actuator, command, dt_s)
        eta = attitudeEuler(eta, nu, dt_s)
    return rows
