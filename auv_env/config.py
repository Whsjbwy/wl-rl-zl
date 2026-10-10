"""不可变配置及参考量；SI 单位，NED 深度向下为正。"""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from numbers import Real


def finite_scalar(value: object, name: str) -> float:
    """验证实标量，拒绝布尔、字符串、数组和 NaN/Inf；返回 float。"""
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{name} 必须是有限实标量")
    return float(value)


def nonnegative_int(value: object, name: str) -> int:
    """要求 Python 非负整数，拒绝布尔和小数；不修改输入。"""
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} 必须是非负整数")
    return value


@dataclass(frozen=True, slots=True)
class ControlReference:
    """深度 [0,100] m、航向 rad、转速 [0,1525] rpm；非法值抛 ValueError。"""

    depth_m: float = 30.0
    heading_rad: float = math.radians(50.0)
    propeller_rpm: float = 1525.0

    def __post_init__(self) -> None:
        for name in ("depth_m", "heading_rad", "propeller_rpm"):
            object.__setattr__(self, name, finite_scalar(getattr(self, name), name))
        if not 0 <= self.depth_m <= 100 or not 0 <= self.propeller_rpm <= 1525:
            raise ValueError("参考深度需在 [0,100] m，转速需在 [0,1525] rpm")


@dataclass(frozen=True, slots=True)
class RemusConfig:
    """固定步长 s、固定流速 m/s 与方向 rad、初始参考和 RNG 种子。无共享数组。"""

    dt_s: float = 0.02
    current_mps: float = 0.5
    current_direction_rad: float = math.radians(170.0)
    initial_reference: ControlReference = field(default_factory=ControlReference)
    seed: int = 20261010

    def __post_init__(self) -> None:
        for name in ("dt_s", "current_mps", "current_direction_rad"):
            object.__setattr__(self, name, finite_scalar(getattr(self, name), name))
        if self.dt_s <= 0 or self.current_mps < 0:
            raise ValueError("步长必须大于零，流速必须非负；S1-A 门槛另固定为 0.02 s")
        if not isinstance(self.initial_reference, ControlReference):
            raise ValueError("initial_reference 必须为 ControlReference")
        nonnegative_int(self.seed, "seed")

    @property
    def config_hash(self) -> str:
        """完整配置的稳定 SHA-256；配置更改必须新建实例。"""
        return hashlib.sha256(
            json.dumps(asdict(self), sort_keys=True, allow_nan=False).encode()
        ).hexdigest()
