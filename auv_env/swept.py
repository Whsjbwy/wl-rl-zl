"""每个物理步的几何扫掠；线性中心+最短SO(3)，不是连续动力学真解。"""

from dataclasses import dataclass, replace

from auv_env.collision import QueryResult, query_pose
from auv_env.config import finite_scalar
from auv_env.geometry import Pose, interpolate, motion_bound
from auv_env.scene import Scene

INTERPOLATION_VERSION = "linear-position-shortest-so3-v1"


@dataclass(frozen=True, slots=True)
class SweepResult:
    """步内时间单位s；只报发现见证时刻，不冒称精确首次接触时间。"""

    query: QueryResult
    step_interval_s: tuple[float, float]
    evidence_time_s: float | None
    evidence_interval_s: tuple[float, float] | None
    first_contact_time_interval_s: None = None
    interpolation_version: str = INTERPOLATION_VERSION


def query_sweep(scene: Scene, start: Pose, end: Pose, duration_s: float = 0.02) -> SweepResult:
    """区间中点下界减刚体移动界认证；预算耗尽不判安全，无随机/物理副作用。"""
    duration_s = finite_scalar(duration_s, "duration_s")
    if duration_s <= 0:
        raise ValueError("扫掠时长必须正")
    cache: dict[float, tuple[Pose, QueryResult]] = {}
    subdivisions = 0

    def sample(fraction: float) -> tuple[Pose, QueryResult]:
        if fraction not in cache:
            pose = interpolate(start, end, fraction)
            cache[fraction] = pose, query_pose(scene, pose)
        return cache[fraction]

    def walk(
        left: float, right: float, depth: int
    ) -> tuple[QueryResult, float | None, tuple[float, float] | None]:
        nonlocal subdivisions
        first_pose, first = sample(left)
        last_pose, last = sample(right)
        middle = (left + right) / 2
        _, anchor = sample(middle)
        lower = anchor.clearance_lower_bound_m - motion_bound(first_pose, last_pose, scene.body)
        # 这些是见证时刻，不是未经定位的首接触；更早未认证区域始终包含在区间里。
        for fraction, result in ((left, first), (middle, anchor), (right, last)):
            if result.status == "COLLISION":
                return (
                    replace(
                        result,
                        clearance_lower_bound_m=lower,
                        clearance_value_m=None,
                        clearance_kind="lower_bound",
                    ),
                    fraction,
                    (left, right),
                )
        if lower > scene.policy.contact_tolerance_m:
            return (
                replace(
                    anchor,
                    status="CLEAR",
                    certified=True,
                    clearance_lower_bound_m=lower,
                    clearance_value_m=None,
                    clearance_kind="lower_bound",
                    near_obstacle_status="SAFE_MARGIN"
                    if lower >= scene.policy.soft_margin_m
                    else "UNKNOWN",
                ),
                None,
                None,
            )
        if (
            motion_bound(first_pose, last_pose, scene.body) == 0
            or depth >= scene.policy.max_depth
            or (right - left) * duration_s <= scene.policy.min_interval_s
        ):
            return (
                replace(
                    anchor,
                    status="UNCERTAIN",
                    reason="budget_exhausted",
                    certified=False,
                    clearance_lower_bound_m=lower,
                    clearance_value_m=None,
                    clearance_kind="lower_bound",
                    near_obstacle_status="UNKNOWN",
                ),
                None,
                (left, right),
            )
        subdivisions += 1
        # 左区间先搜索；即使它无法认证，也不把右区间见证误称首次接触。
        a, time_a, interval_a = walk(left, middle, depth + 1)
        if a.status == "COLLISION":
            return a, time_a, interval_a
        b, time_b, interval_b = walk(middle, right, depth + 1)
        if b.status == "COLLISION":
            return b, time_b, (left, right) if a.status == "UNCERTAIN" else interval_b
        selected = a if a.status == "UNCERTAIN" else b
        combined = min(a.clearance_lower_bound_m, b.clearance_lower_bound_m)
        return (
            replace(
                selected,
                clearance_lower_bound_m=combined,
                near_obstacle_status="SAFE_MARGIN"
                if combined >= scene.policy.soft_margin_m
                else "UNKNOWN",
            ),
            None,
            interval_a or interval_b,
        )

    result, evidence, interval = walk(0.0, 1.0, 0)
    # 返回整个步的有效下界，不把局部接触子区间下界冒充整段最小间距。
    root_anchor = sample(0.5)[1].clearance_lower_bound_m - motion_bound(start, end, scene.body)
    if result.status != "CLEAR":
        result = replace(result, clearance_lower_bound_m=root_anchor)
    result = replace(
        result,
        query_count=sum(r.query_count for _, r in cache.values()),
        subdivisions=subdivisions + sum(r.subdivisions for _, r in cache.values()),
    )
    return SweepResult(
        result,
        (0.0, duration_s),
        None if evidence is None else evidence * duration_s,
        None if interval is None else (interval[0] * duration_s, interval[1] * duration_s),
    )
