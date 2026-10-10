"""L1专用完整快照；与原L0格式分离，JSON只保存数据、不执行对象。"""

import copy
import json
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from auv_env.config import ControlReference, finite_scalar, nonnegative_int
from auv_env.currents import CurrentField
from auv_env.l1.actuators import actual_state
from auv_env.l1.parameters import MODEL_ID, parameter_hash
from auv_env.state import CONTROLLER_FIELDS, Vector, validate_rng, vector


@dataclass(slots=True)
class L1Snapshot:
    """步边界状态；eta(6,)m/rad，nu(6,)m/s/rad/s，命令/实际(3,)rad/rad/rpm。

    controller含七项记忆，field保存全部解析配置，rng_state为完整PCG64。
    数组允许外部编辑，恢复时复制验证；读取与序列化不推进物理。
    """

    version: int
    model_id: str
    parameter_hash: str
    field: dict[str, Any]
    dt_s: float
    step_count: int
    time_s: float
    eta: Vector
    nu: Vector
    actuator: Vector
    controller: dict[str, float]
    reference: ControlReference
    raw_command: Vector
    clipped_command: Vector
    rng_state: dict[str, Any]

    def validated_copy(self) -> "L1Snapshot":
        """完整字段校验后深复制；非法标识/非有限/越界抛ValueError。"""
        if type(self.version) is not int or self.version != 1 or self.model_id != MODEL_ID:
            raise ValueError("L1模型/版本不符")
        if self.parameter_hash != parameter_hash():
            raise ValueError("L1参数哈希不符")
        field = CurrentField.from_dict(self.field)
        dt = finite_scalar(self.dt_s, "dt_s")
        count = nonnegative_int(self.step_count, "step_count")
        time = finite_scalar(self.time_s, "time_s")
        if dt <= 0 or time != count * dt:
            raise ValueError("L1时间与步计数不符")
        if set(self.controller) != set(CONTROLLER_FIELDS):
            raise ValueError("控制器记忆不完整")
        pose = vector(self.eta, 6, "eta")
        if abs(np.cos(pose[4])) < 1e-6:
            raise ValueError("ZYX姿态接近奇点")
        return L1Snapshot(
            1,
            MODEL_ID,
            self.parameter_hash,
            field.to_dict(),
            dt,
            count,
            time,
            pose,
            vector(self.nu, 6, "nu"),
            actual_state(self.actuator),
            {n: finite_scalar(self.controller[n], n) for n in CONTROLLER_FIELDS},
            ControlReference(**asdict(self.reference)),
            vector(self.raw_command, 3, "raw_command"),
            actual_state(self.clipped_command),
            validate_rng(self.rng_state),
        )

    def to_json(self) -> str:
        """严格JSON，拒绝NaN/Infinity；无副作用，返回全部状态。"""
        data = asdict(self.validated_copy())
        for name in ("eta", "nu", "actuator", "raw_command", "clipped_command"):
            data[name] = data[name].tolist()
        return json.dumps(data, ensure_ascii=False, sort_keys=True, allow_nan=False)

    @classmethod
    def from_json(cls, text: str) -> "L1Snapshot":
        """拒绝重复/额外/缺失键和非法数值；不支持跨模型格式转换。"""

        def pairs(rows: list[tuple[str, Any]]) -> dict[str, Any]:
            result = dict(rows)
            if len(result) != len(rows):
                raise ValueError("重复JSON键")
            return result

        data = json.loads(text, object_pairs_hook=pairs)
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("L1快照字段不完整")
        data = copy.deepcopy(data)
        data["reference"] = ControlReference(**data["reference"])
        return cls(**data).validated_copy()
