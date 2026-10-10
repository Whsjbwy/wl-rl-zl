"""独立工程动量方程；忽略额外压力梯度、波浪和环境旋转流载荷。"""

from typing import Any

import numpy as np
from python_vehicle_simulator.lib.gnc import crossFlowDrag, forceLiftDrag, gvect, m2c

from auv_env.current_kinematics import current_terms
from auv_env.currents import CurrentSample
from auv_env.l1.actuators import actual_state
from auv_env.l1.parameters import export_parameters
from auv_env.state import Vector, vector


def applied_loads(
    parameters: dict[str, Any], relative: Vector, actual: Vector
) -> dict[str, Vector]:
    """推进/舵面、升阻、横流三个(6,)载荷，前三N、后三N·m；不含C/D/g。"""
    p = parameters
    speed = float(np.linalg.norm(relative[:3]))
    rudder, plane, rpm = actual
    prop = p["propeller"]
    n = rpm / 60
    diameter = prop["diameter_m"]
    advance = prop["advance_factor"] * speed
    thrust = (
        p["rho"]
        * diameter**4
        * (
            prop["KT_0"] * n * n
            + (prop["KT_max"] - prop["KT_0"]) / prop["Ja_max"] * advance / diameter * n
        )
    )
    torque = (
        p["rho"]
        * diameter**5
        * (
            prop["KQ_0"] * n * n
            + (prop["KQ_max"] - prop["KQ_0"]) / prop["Ja_max"] * advance / diameter * n
        )
    )
    horizontal2 = relative[0] ** 2 + relative[1] ** 2
    vertical2 = relative[0] ** 2 + relative[2] ** 2
    rudder_factor = -0.5 * p["rho"] * horizontal2 * p["A_r"] * p["CL_delta_r"]
    plane_factor = -0.5 * p["rho"] * vertical2 * p["A_s"] * p["CL_delta_s"]
    sway, heave = rudder_factor * rudder, plane_factor * plane
    fins = np.array(
        [
            (1 - prop["thrust_deduction"]) * thrust
            + rudder_factor * rudder**2
            + plane_factor * plane**2,
            sway,
            heave,
            torque * prop["roll_scale"],
            -p["x_s"] * heave,
            p["x_r"] * sway,
        ]
    )
    lift = forceLiftDrag(p["diam"], p["S"], p["CD_0"], np.arctan2(relative[2], relative[0]), speed)
    crossflow = crossFlowDrag(p["L"], p["diam"], p["diam"], relative)
    return dict(prop_fin=fins, lift_drag=np.asarray(lift), crossflow=np.asarray(crossflow))


def momentum_solve(
    mrb: Vector,
    ma: Vector,
    forces: Vector,
    rigid: Vector,
    added: Vector,
    damping: Vector,
    restoring: Vector,
    current_dot: Vector,
) -> Vector:
    """纯函数装配(6,6)质量与(6,)载荷，支持MA=0极限；返回m/s²、rad/s²。

    参数非法或奇异时抛ValueError/LinAlgError，不修改输入。
    """
    for matrix in (mrb, ma):
        if matrix.shape != (6, 6) or not np.isfinite(matrix).all():
            raise ValueError("质量矩阵非法")
    values = [
        vector(v, 6, "momentum_term")
        for v in (forces, rigid, added, damping, restoring, current_dot)
    ]
    force, crb, ca, damp, g, derivative = values
    return np.linalg.solve(mrb + ma, force - crb - ca - damp - g + ma @ derivative)


def evaluate(
    eta: object,
    nu: object,
    actual: object,
    sample: CurrentSample,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Vector]:
    """同状态连续右端及逐项诊断，无积分或控制器副作用。

    eta(6,) m/rad；nu(6,)对地体轴m/s、rad/s；actual(3,)rad/rad/rpm。
    保留八个CA删减位置与推进滚转1/10；有限值非法抛ValueError。
    """
    pose, velocity = vector(eta, 6, "eta"), vector(nu, 6, "nu")
    actuator = actual_state(actual)
    p = export_parameters() if parameters is None else parameters
    terms = current_terms(pose, velocity, sample)
    relative = terms["nu_relative"]
    mrb, ma = np.array(p["MRB"]), np.array(p["MA"])
    mass = mrb + ma
    crb, ca = m2c(mrb, velocity), m2c(ma, relative)
    for i, j in p["CA_zero_indices"]:
        ca[i, j] = 0.0
    diagonal = np.array(
        [
            mass[0, 0] / p["T_surge"],
            mass[1, 1] / p["T_sway"],
            mass[2, 2] / p["T_heave"],
            mass[3, 3] * 2 * p["zeta_roll"] * p["w_roll"],
            mass[4, 4] * 2 * p["zeta_pitch"] * p["w_pitch"],
            mass[5, 5] / p["T_yaw"],
        ]
    )
    diagonal[:2] *= np.exp(-3 * np.linalg.norm(relative[:3]))
    damping = np.diag(diagonal)
    restoring = gvect(p["W"], p["B"], pose[4], pose[3], np.array(p["r_bg"]), np.array(p["r_bb"]))
    loads = applied_loads(p, relative, actuator)
    applied = loads["prop_fin"] + loads["lift_drag"] + loads["crossflow"]
    derivative = momentum_solve(
        mrb,
        ma,
        applied,
        crb @ velocity,
        ca @ relative,
        damping @ relative,
        restoring,
        terms["nu_current_dot"],
    )
    result = dict(
        **terms,
        **loads,
        F_applied=applied,
        MRB=mrb,
        MA=ma,
        M=mass,
        CRB=crb,
        CA_red=ca,
        D=damping,
        g=restoring,
        rigid=crb @ velocity,
        added=ca @ relative,
        damping=damping @ relative,
        current_inertia=ma @ terms["nu_current_dot"],
        nu_dot=derivative,
    )
    if any(not np.isfinite(value).all() for value in result.values()):
        raise FloatingPointError("L1右端非有限")
    return result
