"""静态艇体查询；解析间隙与一般地形保守下界严格区分。"""

import math
from dataclasses import dataclass

import numpy as np

from auv_env.geometry import Pose, segment_box, segment_point
from auv_env.scene import Scene
from auv_env.state import Vector


@dataclass(frozen=True, slots=True)
class QueryResult:
    """距离单位m；witness是艇体中的见证点；负下界不等于碰撞。"""

    status: str
    reason: str
    object_id: str
    clearance_lower_bound_m: float
    clearance_value_m: float | None
    clearance_kind: str
    contact_witness: tuple[float, ...] | None
    near_obstacle_status: str
    certified: bool
    query_count: int = 1
    subdivisions: int = 0


def exact_result(
    scene: Scene, gap: float, reason: str, object_id: str, witness: Vector
) -> QueryResult:
    """解析 gap 的接触带为<=tol；相交负值不宣称一般精确穿透深度。"""
    collision = gap <= scene.policy.contact_tolerance_m
    return QueryResult(
        "COLLISION" if collision else "CLEAR",
        reason,
        object_id,
        gap,
        gap,
        "exact_gap",
        tuple(float(x) for x in witness) if collision else None,
        "SAFE_MARGIN" if gap >= scene.policy.soft_margin_m else "VIOLATION",
        True,
    )


def terrain_query(scene: Scene, start: Vector, end: Vector) -> QueryResult:
    """解析平面精确判定；曲面以全域G、中心线间距修正和实际表面见证判定。"""
    terrain, radius, policy = scene.terrain, scene.body.radius_m, scene.policy
    if not terrain.bumps:
        normal = np.array([-terrain.slope_n, -terrain.slope_e, 1.0])
        normal /= np.linalg.norm(normal)
        gaps = [
            (terrain.depth_at(p[0], p[1]) - p[2]) / math.sqrt(1 + terrain.gradient_bound() ** 2)
            for p in (start, end)
        ]
        index = int(np.argmin(gaps))
        return exact_result(
            scene, gaps[index] - radius, "terrain", "terrain", (start, end)[index] + radius * normal
        )
    length = float(np.linalg.norm(end - start))
    spacing = policy.line_spacing_m
    divisions, queries = 0, 0
    while True:
        count = max(1, math.ceil(length / spacing))
        points = start + np.linspace(0.0, 1.0, count + 1)[:, None] * (end - start)
        heights = np.array([terrain.depth_at(p[0], p[1]) - p[2] for p in points])
        queries += len(points)
        for point, height in zip(points, heights, strict=True):
            if height <= 0:
                return QueryResult(
                    "COLLISION",
                    "terrain",
                    "terrain",
                    -radius,
                    None,
                    "lower_bound",
                    tuple(point),
                    "VIOLATION",
                    True,
                    queries,
                    divisions,
                )
            gradient = terrain.gradient_at(point[0], point[1])
            inward = np.array([-gradient[0], -gradient[1], 1.0])
            witness = point + radius * inward / np.linalg.norm(inward)
            if witness[2] >= terrain.depth_at(witness[0], witness[1]):
                return QueryResult(
                    "COLLISION",
                    "terrain",
                    "terrain",
                    -radius,
                    None,
                    "lower_bound",
                    tuple(witness),
                    "VIOLATION",
                    True,
                    queries,
                    divisions,
                )
        lower = float(
            heights.min() / math.sqrt(1 + terrain.gradient_bound() ** 2)
            - length / count / 2
            - radius
        )
        if lower > policy.contact_tolerance_m:
            return QueryResult(
                "CLEAR",
                "terrain",
                "terrain",
                lower,
                None,
                "lower_bound",
                None,
                "SAFE_MARGIN" if lower >= policy.soft_margin_m else "UNKNOWN",
                True,
                queries,
                divisions,
            )
        if spacing <= policy.min_line_spacing_m:
            return QueryResult(
                "UNCERTAIN",
                "budget_exhausted",
                "terrain",
                lower,
                None,
                "lower_bound",
                None,
                "UNKNOWN",
                False,
                queries,
                divisions,
            )
        spacing = max(policy.min_line_spacing_m, spacing / 2)
        divisions += 1


def query_pose(scene: Scene, pose: Pose) -> QueryResult:
    """完整胶囊与所有实体/操作边界查询，无仿真副作用；返回联合有效下界。"""
    if not isinstance(scene, Scene) or not isinstance(pose, Pose):
        raise ValueError("查询需要 Scene 和 Pose")
    start, end = scene.body.endpoints(pose)
    radius = scene.body.radius_m
    results = [terrain_query(scene, start, end)]
    for obj in scene.spheres:
        center = np.array(obj.center)
        closest = segment_point(start, end, center)
        distance = float(np.linalg.norm(center - closest))
        witness = (
            closest
            if distance == 0
            else closest + min(radius, distance) * (center - closest) / distance
        )
        results.append(
            exact_result(scene, distance - radius - obj.radius_m, "sphere", obj.object_id, witness)
        )
    for box in scene.boxes:
        distance, point, target = segment_box(start, end, box)
        witness = (
            point if distance == 0 else point + min(radius, distance) * (target - point) / distance
        )
        results.append(exact_result(scene, distance - radius, "box", box.object_id, witness))
    for axis in range(3):
        for sign, bound in ((1.0, scene.lower[axis]), (-1.0, scene.upper[axis])):
            point = min((start, end), key=lambda p: sign * (p[axis] - bound)).copy()
            gap = sign * (point[axis] - bound) - radius
            point[axis] -= sign * radius
            results.append(
                exact_result(scene, float(gap), "domain_boundary", "domain_boundary", point)
            )
    selected = next((r for r in results if r.status == "COLLISION"), None)
    if selected is None:
        selected = next(
            (r for r in results if r.status == "UNCERTAIN"),
            min(results, key=lambda r: r.clearance_lower_bound_m),
        )
    lower = min(r.clearance_lower_bound_m for r in results)
    exact = (
        min(r.clearance_value_m for r in results if r.clearance_value_m is not None)
        if all(r.clearance_value_m is not None for r in results)
        else None
    )
    risk = (
        "SAFE_MARGIN"
        if lower >= scene.policy.soft_margin_m
        else (
            "VIOLATION"
            if any(r.near_obstacle_status == "VIOLATION" for r in results)
            else "UNKNOWN"
        )
    )
    return QueryResult(
        selected.status,
        selected.reason,
        selected.object_id,
        lower,
        exact,
        "exact_gap" if exact is not None else "lower_bound",
        selected.contact_witness,
        risk,
        selected.status != "UNCERTAIN",
        sum(r.query_count for r in results),
        sum(r.subdivisions for r in results),
    )
