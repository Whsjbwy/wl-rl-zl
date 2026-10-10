"""监控提交前的扫掠结果结构与语义检查；非法计算结果不是几何事件。"""

import math

from auv_env.collision import QueryResult
from auv_env.scene import Scene
from auv_env.swept import INTERPOLATION_VERSION, SweepResult


def finite(value: object) -> bool:
    """实数有限且不是bool；距离和时间不接受隐式字符串转换。"""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_sweep(result: object, scene: Scene) -> SweepResult:
    """校验0.02s查询的单位/状态/证据结构；非法返回抛ValueError，无副作用。"""
    if not isinstance(result, SweepResult) or not isinstance(result.query, QueryResult):
        raise ValueError("扫掠返回类型非法")
    query = result.query
    if (
        result.interpolation_version != INTERPOLATION_VERSION
        or result.step_interval_s != (0.0, 0.02)
        or not all(finite(x) for x in result.step_interval_s)
        or result.first_contact_time_interval_s is not None
        or query.status not in {"CLEAR", "COLLISION", "UNCERTAIN"}
        or query.reason not in {"terrain", "sphere", "box", "domain_boundary", "budget_exhausted"}
        or query.object_id
        not in {"terrain", "domain_boundary"}
        | {x.object_id for x in scene.spheres}
        | {x.object_id for x in scene.boxes}
        or query.clearance_kind != "lower_bound"
        or not finite(query.clearance_lower_bound_m)
        or query.clearance_value_m is not None
        or type(query.certified) is not bool
        or query.near_obstacle_status not in {"SAFE_MARGIN", "VIOLATION", "UNKNOWN"}
    ):
        raise ValueError("扫掠返回字段非法")
    for name, minimum in (("query_count", 1), ("subdivisions", 0)):
        value = getattr(query, name)
        if type(value) is not int or value < minimum:
            raise ValueError("扫掠计数非法")
    if query.certified != (query.status != "UNCERTAIN"):
        raise ValueError("认证标志与状态不一致")
    if (
        query.status == "CLEAR"
        and query.clearance_lower_bound_m <= scene.policy.contact_tolerance_m
    ):
        raise ValueError("CLEAR缺少有效正下界")
    if query.status == "COLLISION":
        witness = query.contact_witness
        if witness is None or len(witness) != 3 or not all(finite(x) for x in witness):
            raise ValueError("碰撞缺少有限三维见证")
        if (
            result.evidence_time_s is None
            or not finite(result.evidence_time_s)
            or not 0 <= result.evidence_time_s <= 0.02
        ):
            raise ValueError("接触见证时刻非法")
    elif query.contact_witness is not None or result.evidence_time_s is not None:
        raise ValueError("非碰撞不能伪造接触见证")
    interval = result.evidence_interval_s
    if query.status == "CLEAR":
        if interval is not None:
            raise ValueError("CLEAR不应带未认证区间")
    elif interval is None or len(interval) != 2 or not all(finite(x) for x in interval):
        raise ValueError("缺少有限证据区间")
    elif not 0 <= interval[0] <= interval[1] <= 0.02:
        raise ValueError("证据区间越界")
    elif (
        result.evidence_time_s is not None
        and not interval[0] <= result.evidence_time_s <= interval[1]
    ):
        raise ValueError("见证时刻不在证据区间")
    return result
