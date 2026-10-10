"""确定性开阔水域海流；所有数组返回副本，查询不推进时钟或 RNG。"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import numpy as np

from auv_env.config import finite_scalar
from auv_env.state import Vector, vector


@dataclass(slots=True)
class CurrentSample:
    """NED速度(3,) m/s、J[i,j]=∂c_i/∂p_j (3,3) 1/s、定点偏导(3,) m/s²。"""

    velocity_ned_mps: Vector
    jacobian_per_s: Vector
    partial_t_mps2: Vector
    field_id: str
    version: int
    config_hash: str
    purpose: str


class CurrentField:
    """冻结解析配置；四类场均为纯函数，不存在隐藏时间/随机相位。

    输入位置为NED米、时间为秒；非法有限值、形状、类型抛ValueError。
    仿射场仅用于导数制造解，不代表真实海域。
    """

    def __init__(
        self,
        kind: str = "zero",
        *,
        background: object = (0, 0, 0),
        amplitude: object = (0, 0, 0),
        omega_radps: float = 0.0,
        phase_rad: float = 0.0,
        jacobian: object = ((0, 0, 0),) * 3,
        partial_t: object = (0, 0, 0),
        origin_m: object = (0, 0, 0),
        origin_time_s: float = 0.0,
    ) -> None:
        if kind not in {"zero", "uniform", "sinusoidal", "affine"}:
            raise ValueError("未知场类型")
        matrix = np.asarray(jacobian)
        if (
            matrix.shape != (3, 3)
            or matrix.dtype.kind not in "fiu"
            or not np.isfinite(matrix).all()
        ):
            raise ValueError("Jacobian必须为有限实数(3,3)")
        data = dict(
            kind=kind,
            background=vector(background, 3, "background").tolist(),
            amplitude=vector(amplitude, 3, "amplitude").tolist(),
            omega_radps=finite_scalar(omega_radps, "omega_radps"),
            phase_rad=finite_scalar(phase_rad, "phase_rad"),
            jacobian=matrix.astype(float).tolist(),
            partial_t=vector(partial_t, 3, "partial_t").tolist(),
            origin_m=vector(origin_m, 3, "origin_m").tolist(),
            origin_time_s=finite_scalar(origin_time_s, "origin_time_s"),
        )
        if data["omega_radps"] < 0:
            raise ValueError("频率必须非负")
        self._json = json.dumps(data, sort_keys=True, allow_nan=False)

    @property
    def config_hash(self) -> str:
        """解析配置内容SHA256，无副作用。"""
        return hashlib.sha256(self._json.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """返回独立可序列化场状态，不共享数组。"""
        return {"version": 1, "config": json.loads(self._json), "config_hash": self.config_hash}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CurrentField":
        """严格恢复版本和内容哈希，不消费RNG；非法配置抛ValueError。"""
        if set(data) != {"version", "config", "config_hash"} or type(data["version"]) is not int:
            raise ValueError("场状态字段错误")
        result = cls(**data["config"])
        if data["version"] != 1 or result.config_hash != data["config_hash"]:
            raise ValueError("场版本或哈希错误")
        return result

    def sample(self, position_ned_m: object, time_s: float) -> CurrentSample:
        """查询有限(3,)位置和时刻，返回独立数组；异常不改变场状态。"""
        p = vector(position_ned_m, 3, "position_ned_m")
        t = finite_scalar(time_s, "time_s")
        data = json.loads(self._json)
        velocity, jacobian, partial = np.zeros(3), np.zeros((3, 3)), np.zeros(3)
        kind = data["kind"]
        with np.errstate(over="raise", invalid="raise"):
            if kind != "zero":
                velocity = np.array(data["background"], dtype=float)
            if kind == "sinusoidal":
                angle = data["omega_radps"] * t + data["phase_rad"]
                velocity += np.array(data["amplitude"]) * np.sin(angle)
                partial = np.array(data["amplitude"]) * data["omega_radps"] * np.cos(angle)
            if kind == "affine":
                jacobian = np.array(data["jacobian"], dtype=float)
                partial = np.array(data["partial_t"], dtype=float)
                velocity += jacobian @ (p - data["origin_m"]) + partial * (
                    t - data["origin_time_s"]
                )
        return CurrentSample(
            velocity,
            jacobian,
            partial,
            kind,
            1,
            self.config_hash,
            "manufactured_derivative_test" if kind == "affine" else "openwater",
        )
