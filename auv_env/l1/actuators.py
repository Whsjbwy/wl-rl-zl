"""命令先限幅、实际执行器ZOH解析更新；不改变冻结L0行为。"""

import numpy as np

from auv_env.config import finite_scalar
from auv_env.state import Vector, vector

LOWER = np.array([-np.pi / 12, -np.pi / 12, 0.0])
UPPER = np.array([np.pi / 12, np.pi / 12, 1525.0])
TIME_CONSTANT_S = np.array([0.1, 0.1, 1.0])
for _array in (LOWER, UPPER, TIME_CONSTANT_S):
    _array.flags.writeable = False


def actual_state(value: object) -> Vector:
    """复制(3,)实际舵角rad/rad/转速rpm；越界拒绝，不能偷裁剪初态。"""
    result = vector(value, 3, "actuator")
    if np.any(result < LOWER) or np.any(result > UPPER):
        raise ValueError("实际执行器越界")
    return result


def update(
    actual: object, raw: object, dt_s: float, time_constant_s: object = TIME_CONSTANT_S
) -> tuple[Vector, Vector]:
    """返回独立的限幅命令与下一实际状态；非法dt/T/数组抛ValueError。

    当前步载荷必须另用actual计算；此函数不修改任何输入或模拟器。
    """
    previous = actual_state(actual)
    command = np.clip(vector(raw, 3, "raw_command"), LOWER, UPPER)
    constants = vector(time_constant_s, 3, "time_constant_s")
    dt = finite_scalar(dt_s, "dt_s")
    if dt <= 0 or np.any(constants <= 0):
        raise ValueError("dt和时间常数必须为正")
    next_actual = command + (previous - command) * np.exp(-dt / constants)
    return command, actual_state(next_actual)
