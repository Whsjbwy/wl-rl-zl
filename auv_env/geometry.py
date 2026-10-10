"""NED 胶囊与静态实体几何；只计算，不推进动力学或消费随机数。"""

import math
from dataclasses import dataclass

import numpy as np

from auv_env.config import finite_scalar
from auv_env.state import Vector, vector


def tuple_vector(value: object, size: int) -> tuple[float, ...]:
    """复制有限 shape=(size,) 实向量为不可变元组；错误抛 ValueError。"""
    return tuple(float(x) for x in vector(value, size, "几何向量"))


def quaternion(value: object) -> Vector:
    """规范化 wxyz 四元数并固定 q/-q 符号；零范数非法，无副作用。"""
    result = vector(value, 4, "四元数")
    norm = float(np.linalg.norm(result))
    if not np.isfinite(norm) or norm < 1e-15:
        raise ValueError("四元数范数非法")
    result /= norm
    first = next(x for x in result if abs(x) > 1e-15)
    return -result if first < 0 else result


def rotation(value: object) -> Vector:
    """返回 (3,3) 体坐标到 NED 旋转，输入 wxyz，无副作用。"""
    w, x, y, z = quaternion(value)
    return np.array(
        [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
    )


@dataclass(frozen=True, slots=True)
class Pose:
    """不可变位姿；position 是 NED (3,) m，orientation 是 wxyz (4,)。"""

    position: tuple[float, ...]
    orientation: tuple[float, ...] = (1.0, 0.0, 0.0, 0.0)

    def __post_init__(self) -> None:
        object.__setattr__(self, "position", tuple_vector(self.position, 3))
        object.__setattr__(self, "orientation", tuple(quaternion(self.orientation)))

    @classmethod
    def from_eta(cls, eta: object) -> "Pose":
        """由 NED/ZYX shape=(6,) m/rad 转换；与冻结 Rzyx 约定相同。"""
        values = vector(eta, 6, "eta")
        roll, pitch, yaw = values[3:] / 2
        cr, cp, cy = np.cos([roll, pitch, yaw])
        sr, sp, sy = np.sin([roll, pitch, yaw])
        return cls(
            tuple(values[:3]),
            (
                cr * cp * cy + sr * sp * sy,
                sr * cp * cy - cr * sp * sy,
                cr * sp * cy + sr * cp * sy,
                cr * cp * sy - sr * sp * cy,
            ),
        )


def interpolate(start: Pose, end: Pose, fraction: float) -> Pose:
    """位置线性、姿态最短 SO(3) 插值；比例[0,1]；非动力学中间状态。"""
    fraction = finite_scalar(fraction, "插值比例")
    if not 0 <= fraction <= 1:
        raise ValueError("插值比例越界")
    first, second = quaternion(start.orientation), quaternion(end.orientation)
    dot = float(first @ second)
    # 规范符号使 pi 并列选择可复现；负点积改取等价四元数走短弧。
    if dot < 0:
        second, dot = -second, -dot
    # atan2差/和避免acos(dot)把很小但非零的转角舍入成零。
    angle = 2 * math.atan2(
        float(np.linalg.norm(first - second)), float(np.linalg.norm(first + second))
    )
    if angle == 0:
        result = first.copy()
    else:
        result = (
            math.sin((1 - fraction) * angle) * first + math.sin(fraction * angle) * second
        ) / math.sin(angle)
    position = (1 - fraction) * np.array(start.position) + fraction * np.array(end.position)
    return Pose(tuple(position), tuple(result))


@dataclass(frozen=True, slots=True)
class Capsule:
    """总长含端帽；默认1.6m、半径0.095m，中心线半长0.705m。"""

    total_length_m: float = 1.6
    radius_m: float = 0.095

    def __post_init__(self) -> None:
        for name in ("total_length_m", "radius_m"):
            object.__setattr__(self, name, finite_scalar(getattr(self, name), name))
        if self.radius_m <= 0 or self.total_length_m < 2 * self.radius_m:
            raise ValueError("胶囊总长必须至少为正直径")

    @property
    def half_segment_m(self) -> float:
        """中心线半长 m；零值退化为球。"""
        return self.total_length_m / 2 - self.radius_m

    def endpoints(self, pose: Pose) -> tuple[Vector, Vector]:
        """返回两个独立 shape=(3,) NED/m 端点；不包含额外端帽长度。"""
        offset = self.half_segment_m * rotation(pose.orientation)[:, 0]
        return np.array(pose.position) - offset, np.array(pose.position) + offset


@dataclass(frozen=True, slots=True)
class Sphere:
    """静态闭实体球；稳定 object_id、NED中心 m 和正半径 m。"""

    object_id: str
    center: tuple[float, ...]
    radius_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.object_id, str) or not self.object_id:
            raise ValueError("障碍ID不能为空")
        object.__setattr__(self, "center", tuple_vector(self.center, 3))
        object.__setattr__(self, "radius_m", finite_scalar(self.radius_m, "球半径"))
        if self.radius_m <= 0:
            raise ValueError("球半径必须正")


@dataclass(frozen=True, slots=True)
class Box:
    """轴对齐闭实体盒；两个 NED (3,) m 角点，每轴 lower<upper。"""

    object_id: str
    lower: tuple[float, ...]
    upper: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.object_id, str) or not self.object_id:
            raise ValueError("障碍ID不能为空")
        object.__setattr__(self, "lower", tuple_vector(self.lower, 3))
        object.__setattr__(self, "upper", tuple_vector(self.upper, 3))
        if np.any(np.array(self.lower) >= self.upper):
            raise ValueError("盒体每轴必须有正长度")


def segment_point(start: Vector, end: Vector, point: Vector) -> Vector:
    """返回闭线段离点最近位置 m；支持退化中心线，不修改输入。"""
    direction = end - start
    length2 = float(direction @ direction)
    fraction = 0.0 if length2 == 0 else float(np.clip((point - start) @ direction / length2, 0, 1))
    return start + fraction * direction


def segment_box(start: Vector, end: Vector, box: Box) -> tuple[float, Vector, Vector]:
    """闭线段到实体盒精确最小距离 m 及最近点；分段二次最小化，包含中部穿盒。"""
    direction = end - start
    lower, upper = np.array(box.lower), np.array(box.upper)
    breaks = {0.0, 1.0}
    for axis in range(3):
        if direction[axis] != 0:
            for bound in (lower[axis], upper[axis]):
                value = float((bound - start[axis]) / direction[axis])
                if 0 < value < 1:
                    breaks.add(value)
    knots = sorted(breaks)
    candidates = knots.copy()
    for left, right in zip(knots[:-1], knots[1:], strict=True):
        middle = start + (left + right) / 2 * direction
        active = (middle < lower) | (middle > upper)
        offset = start - np.clip(middle, lower, upper)
        slope = direction[active]
        denominator = float(slope @ slope)
        if denominator > 0:
            candidates.append(float(np.clip(-offset[active] @ slope / denominator, left, right)))
    points = start + np.array(candidates)[:, None] * direction
    on_box = np.clip(points, lower, upper)
    distances = np.linalg.norm(points - on_box, axis=1)
    index = int(np.argmin(distances))
    return float(distances[index]), points[index].copy(), on_box[index].copy()


def motion_bound(start: Pose, end: Pose, body: Capsule) -> float:
    """中点锚定的最大点移动界 m：D/2+2*rho*sin(theta/4)。"""
    distance = float(np.linalg.norm(np.array(end.position) - start.position))
    first, second = quaternion(start.orientation), quaternion(end.orientation)
    if first @ second < 0:
        second = -second
    quarter_angle = math.atan2(
        float(np.linalg.norm(first - second)), float(np.linalg.norm(first + second))
    )
    bound = distance / 2 + body.total_length_m * math.sin(quarter_angle)
    # 向上舍入一个浮点单位，保持真静止时精确为零。
    return float(np.nextafter(bound, math.inf)) if bound > 0 else 0.0
