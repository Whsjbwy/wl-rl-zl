"""可透明 JSON 往返的完整步边界快照，不使用 pickle。"""

import copy
import json
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from auv_env.config import ControlReference, RemusConfig, finite_scalar, nonnegative_int

Vector = NDArray[np.float64]
MODEL_ID = "remus100:c2dfbd4f4425d29bdcba98294563cda19890cdb1:boundary-v1"
CONTROLLER_FIELDS = ("psi_d", "r_d", "a_d", "e_psi_int", "z_int", "z_d", "theta_int")


def vector(value: object, length: int, name: str) -> Vector:
    """复制 shape=(length,) 的有限 float64 向量；不接受复数或字符串。"""
    raw = np.asarray(value)
    if raw.shape != (length,) or raw.dtype.kind not in "fiu" or not np.isfinite(raw).all():
        raise ValueError(f"{name} 必须是 shape=({length},) 的有限实向量")
    return np.array(raw, dtype=np.float64, copy=True)


def validate_rng(state: dict[str, Any]) -> dict[str, Any]:
    """验证 PCG64 完整状态的字段与整数范围，再深复制；不消耗随机数。"""
    if not isinstance(state, dict) or set(state) != {
        "bit_generator",
        "state",
        "has_uint32",
        "uinteger",
    }:
        raise ValueError("随机数状态字段不完整")
    inner = state["state"]
    if (
        state["bit_generator"] != "PCG64"
        or not isinstance(inner, dict)
        or set(inner) != {"state", "inc"}
    ):
        raise ValueError("仅支持完整 PCG64 状态")
    for name, limit in (("state", 2**128), ("inc", 2**128)):
        if nonnegative_int(inner[name], name) >= limit:
            raise ValueError("PCG64 状态整数越界")
    if inner["inc"] % 2 != 1:
        raise ValueError("PCG64 inc 必须为奇数")
    if nonnegative_int(state["has_uint32"], "has_uint32") > 1:
        raise ValueError("has_uint32 越界")
    if nonnegative_int(state["uinteger"], "uinteger") >= 2**32:
        raise ValueError("uinteger 越界")
    return copy.deepcopy(state)


@dataclass(slots=True)
class RemusSnapshot:
    """已完成 k 次积分、下一次控制器尚未调用的边界状态。

    eta: (6,) 为 N/E/D 米与 roll/pitch/yaw 弧度；nu: (6,) 为 m/s、rad/s。
    actuator: (3,) 为实际 rudder/plane 弧度和 rpm；last_command 同单位。
    controller 为七个独立标量，单位见方法映射；rng_state 为完整 PCG64 字典。
    返回值可编辑，但不会共享环境引用；set_state 会再次验证并复制。
    """

    version: int
    model_id: str
    config: RemusConfig
    config_hash: str
    step_count: int
    eta: Vector
    nu: Vector
    actuator: Vector
    controller: dict[str, float]
    reference: ControlReference
    last_command: Vector
    rng_state: dict[str, Any]

    @property
    def time_s(self) -> float:
        """物理时间直接由整数步数乘步长得到，不做累加。"""
        return self.step_count * self.config.dt_s

    def validated_copy(self, expected_config: RemusConfig) -> "RemusSnapshot":
        """完整校验后返回深副本；任何失败抛 ValueError，不改变原快照或环境。"""
        if type(self.version) is not int or self.version != 1 or self.model_id != MODEL_ID:
            raise ValueError("快照版本或模型标识不一致")
        if self.config != expected_config or self.config_hash != expected_config.config_hash:
            raise ValueError("快照配置不一致")
        nonnegative_int(self.step_count, "step_count")
        try:
            time_s = self.time_s
        except OverflowError as error:
            raise ValueError("步数对应时间溢出") from error
        if not np.isfinite(time_s):
            raise ValueError("步数对应时间非有限")
        if not isinstance(self.reference, ControlReference):
            raise ValueError("快照控制参考类型不符")
        reference = ControlReference(**asdict(self.reference))
        if not isinstance(self.controller, dict) or set(self.controller) != set(CONTROLLER_FIELDS):
            raise ValueError("控制器状态字段不完整")
        return RemusSnapshot(
            1,
            MODEL_ID,
            expected_config,
            expected_config.config_hash,
            self.step_count,
            vector(self.eta, 6, "eta"),
            vector(self.nu, 6, "nu"),
            vector(self.actuator, 3, "actuator"),
            {name: finite_scalar(self.controller[name], name) for name in CONTROLLER_FIELDS},
            reference,
            vector(self.last_command, 3, "last_command"),
            validate_rng(self.rng_state),
        )

    def to_json(self) -> str:
        """返回可读 JSON，保留 float64 往返精度；非法快照抛 ValueError，无推进。"""
        valid = self.validated_copy(self.config)
        data = asdict(valid)
        for name in ("eta", "nu", "actuator", "last_command"):
            data[name] = data[name].tolist()
        return json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False)

    @classmethod
    def from_json(cls, text: str) -> "RemusSnapshot":
        """严格读取完整 JSON 快照；拒绝缺失/额外字段和非法值，不执行外部对象。"""
        try:
            data = json.loads(text)
            if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
                raise ValueError("快照 JSON 字段不完整")
            config_data = data["config"]
            config_data["initial_reference"] = ControlReference(**config_data["initial_reference"])
            data["config"] = RemusConfig(**config_data)
            data["reference"] = ControlReference(**data["reference"])
            result = cls(**data)
            return result.validated_copy(result.config)
        except (KeyError, TypeError, OverflowError) as error:
            raise ValueError("非法快照 JSON") from error
