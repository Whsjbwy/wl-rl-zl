"""乘积/链式求导；nu为对地速度的体轴表达，R为体轴到NED。"""

import numpy as np
from python_vehicle_simulator.lib.gnc import Rzyx, Tzyx

from auv_env.config import finite_scalar
from auv_env.currents import CurrentSample
from auv_env.state import Vector, vector


def rotation(angles_rad: object) -> Vector:
    """ZYX欧拉角(3,) rad到正交矩阵(3,3)，无副作用。"""
    return np.asarray(Rzyx(*vector(angles_rad, 3, "angles_rad")), dtype=float)


def current_terms(eta: object, nu: object, sample: CurrentSample) -> dict[str, Vector]:
    """返回独立向量：位置导数m/s、流速m/s、三个导数项及总导数m/s²。

    Rdot=R S(omega)，故(R.T)dot=-S(omega) R.T；场沿位置链式导数为
    partial_t+J@(R@nu[:3])，空间项使用艇体对地速度，不用流速或相对速度。
    非有限输入/错误形状抛ValueError，不改变任何输入。
    """
    pose, velocity = vector(eta, 6, "eta"), vector(nu, 6, "nu")
    matrix = rotation(pose[3:])
    current = vector(sample.velocity_ned_mps, 3, "current")
    partial = vector(sample.partial_t_mps2, 3, "partial_t")
    jacobian = np.asarray(sample.jacobian_per_s)
    if jacobian.shape != (3, 3) or not np.isfinite(jacobian).all():
        raise ValueError("海流Jacobian非法")
    p_dot = matrix @ velocity[:3]
    body = matrix.T @ current
    rotating = -np.cross(velocity[3:], body)
    temporal = matrix.T @ partial
    spatial = matrix.T @ jacobian @ p_dot
    return dict(
        p_dot=p_dot,
        c_body=body,
        rotation=rotating,
        temporal=temporal,
        spatial=spatial,
        nu_current=np.r_[body, np.zeros(3)],
        nu_relative=velocity - np.r_[body, np.zeros(3)],
        nu_current_dot=np.r_[rotating + temporal + spatial, np.zeros(3)],
    )


def advance_pose(eta: object, nu_next: object, dt_s: float) -> Vector:
    """登记的半隐式一阶姿态更新；nu_next为(6,)，位置不另加海流。

    接近欧拉奇点cos(pitch)<1e-6时拒绝，不裁剪角度。返回副本。
    """
    pose, nu = vector(eta, 6, "eta"), vector(nu_next, 6, "nu_next")
    dt_s = finite_scalar(dt_s, "dt_s")
    if dt_s <= 0:
        raise ValueError("姿态步长必须为正")
    if abs(np.cos(pose[4])) < 1e-6:
        raise ValueError("ZYX欧拉姿态接近奇点")
    result = pose + dt_s * np.r_[rotation(pose[3:]) @ nu[:3], Tzyx(*pose[3:5]) @ nu[3:]]
    if abs(np.cos(result[4])) < 1e-6:
        raise ValueError("更新姿态接近奇点")
    return vector(result, 6, "eta_next")
