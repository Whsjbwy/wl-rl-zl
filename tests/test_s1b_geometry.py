"""B01—B16：独立手算构型、碰撞反例和安全负例；不以被测输出生成期望。"""

import math
from dataclasses import replace

import numpy as np
import pytest
from python_vehicle_simulator.lib.gnc import Rzyx

from auv_env.collision import query_pose
from auv_env.geometry import (
    Box,
    Capsule,
    Pose,
    Sphere,
    interpolate,
    motion_bound,
    rotation,
    segment_box,
)
from auv_env.scene import CollisionPolicy, Scene
from auv_env.swept import query_sweep
from auv_env.terrain import Gaussian, Terrain


def world(**kwargs: object) -> Scene:
    """宽诊断区域，非300m正式开发图；海底足够深便于隔离障碍解析测试。"""
    return Scene(
        "analytic",
        "analytic",
        Terrain(1000.0),
        lower=(-100.0, -100.0, -100.0),
        upper=(100.0, 100.0, 100.0),
        **kwargs,
    )


@pytest.mark.parametrize(
    "angles,axis",
    [((0, 0, 0), (1, 0, 0)), ((0, 0, math.pi / 2), (0, 1, 0)), ((0, math.pi / 2, 0), (0, 0, -1))],
    ids=["zero", "yaw90", "pitch90"],
)
def test_B01_rotation(angles: tuple, axis: tuple) -> None:
    """手算艇首方向与冻结旋转矩阵独立对照；正俯仰艇首深度减小。"""
    pose = Pose.from_eta([3, 7, 11, *angles])
    matrix = rotation(pose.orientation)
    np.testing.assert_allclose(matrix[:, 0], axis, atol=1e-14)
    np.testing.assert_allclose(matrix, Rzyx(*angles), atol=1e-14)
    np.testing.assert_allclose(matrix.T @ matrix, np.eye(3), atol=1e-14)
    assert pose.position == (3.0, 7.0, 11.0)


@pytest.mark.parametrize(
    "length,radius", [(0, 0.095), (-1, 0.095), (1.6, 0), (float("nan"), 0.095)]
)
def test_B02_invalid_capsule(length: float, radius: float) -> None:
    """尺寸非法必须拒绝。"""
    with pytest.raises(ValueError):
        Capsule(length, radius)


def test_B02_total_length_and_sphere() -> None:
    """包含端帽的外端为±0.8；中心线为±0.705；允许球形退化。"""
    body = Capsule()
    a, b = body.endpoints(Pose((0, 0, 0)))
    assert a[0] == pytest.approx(-0.705)
    assert b[0] + body.radius_m == pytest.approx(0.8)
    np.testing.assert_array_equal(*Capsule(0.19, 0.095).endpoints(Pose((0, 0, 0))))


@pytest.mark.parametrize(
    "z,label,gap", [(70.0, "CLEAR", 9.905), (79.905, "COLLISION", 0.0), (81.0, "COLLISION", -1.095)]
)
def test_B03_flat(z: float, label: str, gap: float) -> None:
    """独立平底手算80-z-r，实体内部不能判安全。"""
    scene = replace(world(), terrain=Terrain(80.0))
    result = query_pose(scene, Pose((0, 0, z)))
    assert result.status == label
    assert result.clearance_value_m == pytest.approx(gap, abs=1e-6)


def test_B04_sloped_tilted_capsule() -> None:
    """沿正俯仰30度的胶囊对斜面法向投影独立解析对照。"""
    scene = replace(world(), terrain=Terrain(20.0, 0.3, 0.4))
    pose = Pose.from_eta([2, 3, 19, 0, math.pi / 6, 0])
    axis = np.array([math.sqrt(3) / 2, 0, -0.5])
    normal = np.array([0.3, 0.4, -1.0])
    expected = (20 + 0.3 * 2 + 0.4 * 3 - 19 - 0.705 * abs(normal @ axis)) / math.sqrt(1.25) - 0.095
    assert query_pose(scene, pose).clearance_value_m == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("point", [(0.0, 0.0), (7.0, -3.0), (15.0, 20.0)])
def test_B05_gaussian_gradient(point: tuple) -> None:
    """固定1e-4m差分独立核验解析梯度；峰顶深度用80-10手算。"""
    terrain = Terrain(bumps=(Gaussian(10.0, 0.0, 0.0, 15.0, 20.0),))
    assert terrain.depth_at(0, 0) == 70.0
    x, y = point
    step = 1e-4
    difference = [
        (terrain.depth_at(x + step, y) - terrain.depth_at(x - step, y)) / (2 * step),
        (terrain.depth_at(x, y + step) - terrain.depth_at(x, y - step)) / (2 * step),
    ]
    np.testing.assert_allclose(terrain.gradient_at(x, y), difference, atol=1e-6, rtol=1e-6)


def test_B06_global_bound_and_apex_distance() -> None:
    """顶点水上点到实体精确距离5m；全域界的公式独立手算。"""
    terrain = Terrain(bumps=(Gaussian(5.0, 0.0, 0.0, 15.0, 20.0),))
    assert terrain.gradient_bound() == pytest.approx(5 / (math.sqrt(math.e) * 15))
    lower = (terrain.depth_at(0, 0) - 70) / math.sqrt(1 + terrain.gradient_bound() ** 2)
    assert 0 < lower <= 5.0
    for x in (-30.0, -15.0, 0.0, 15.0, 30.0):
        assert np.linalg.norm(terrain.gradient_at(x, 3)) <= terrain.gradient_bound()


@pytest.mark.parametrize(
    "center,label,gap",
    [
        ((0, 2, 0), "CLEAR", 1.805),
        ((0, 0.195, 0), "COLLISION", 0.0),
        ((0, 0.1, 0), "COLLISION", -0.095),
        ((1.0, 0, 0), "CLEAR", 0.1),
        ((0.8, 0, 0), "COLLISION", -0.1),
    ],
    ids=["side_clear", "tangent", "overlap", "nose_clear", "nose_hit"],
)
def test_B07_sphere(center: tuple, label: str, gap: float) -> None:
    """轴中点/端帽最近点有手算距离。"""
    result = query_pose(world(spheres=(Sphere("s", center, 0.1),)), Pose((0, 0, 0)))
    assert result.status == label
    assert result.clearance_value_m == pytest.approx(gap, abs=1e-6)


@pytest.mark.parametrize(
    "start,end,distance",
    [
        ((-2, 0, 0), (2, 0, 0), 0.0),
        ((-2, 2, 0), (2, 2, 0), 1.0),
        ((-2, 2, 2), (2, 2, 2), math.sqrt(2)),
        ((2, 2, 2), (3, 3, 3), math.sqrt(3)),
        ((0, 0, 0), (0, 0, 0), 0.0),
    ],
    ids=["middle_cross", "face_parallel", "edge", "corner", "inside_degenerate"],
)
def test_B08_segment_solid_box(start: tuple, end: tuple, distance: float) -> None:
    """实体盒面/棱/角距离独立解析，不仅测试线段两个端点。"""
    actual, _, _ = segment_box(
        np.array(start, dtype=float), np.array(end, dtype=float), Box("b", (-1, -1, -1), (1, 1, 1))
    )
    assert actual == pytest.approx(distance, abs=1e-6)


@pytest.mark.parametrize("offset,label", [(0.195, "COLLISION"), (0.3, "CLEAR"), (0.1, "COLLISION")])
def test_B08_capsule_box(offset: float, label: str) -> None:
    """胶囊对盒面相切/分离/重叠。"""
    scene = world(boxes=(Box("b", (-0.2, offset - 0.1, -0.1), (0.2, offset + 0.1, 0.1)),))
    assert query_pose(scene, Pose((0, 0, 0))).status == label


@pytest.mark.parametrize("sign", [-1.0, 1.0], ids=["tail", "nose"])
def test_B09_nose_tail(sign: float) -> None:
    """中心离障碍很远但首尾端帽接触。"""
    scene = world(spheres=(Sphere("s", (0.8 * sign, 0, 0), 0.1),))
    assert 0.8 > 0.095 + 0.1
    assert query_pose(scene, Pose((0, 0, 0))).status == "COLLISION"


def test_B10_body_domain_not_center() -> None:
    """中心在操作域内，端帽已越界；85m操作深度不是海底。"""
    scene = Scene("domain", "domain", Terrain(100.0))
    result = query_pose(scene, Pose((0.7, 20, 20)))
    assert result.status == "COLLISION" and result.reason == "domain_boundary"
    assert query_pose(scene, Pose((20, 20, 85.0))).reason == "domain_boundary"


def test_B11_soft_margin() -> None:
    """0.5m净空违反1m软裕度，但不属于实体碰撞。"""
    scene = replace(world(), terrain=Terrain(80.0))
    result = query_pose(scene, Pose((0, 0, 79.405)))
    assert result.status == "CLEAR" and result.near_obstacle_status == "VIOLATION"


def test_B12_thin_obstacle_crossing() -> None:
    """合成几何反例：端点法故意给安全，扫掠找到中途实体接触。"""
    scene = world(boxes=(Box("thin", (-0.01, -2, -2), (0.01, 2, 2)),))
    a, b = Pose((-2, 0, 0)), Pose((2, 0, 0))
    assert query_pose(scene, a).status == query_pose(scene, b).status == "CLEAR"
    result = query_sweep(scene, a, b)
    assert result.query.status == "COLLISION" and result.evidence_time_s == 0.01
    assert result.first_contact_time_interval_s is None


def test_B13_rotation_collision() -> None:
    """中心不动，±90度端态安全，中间水平艇首扫到球。"""
    scene = world(spheres=(Sphere("s", (0.75, 0, 0), 0.06),))
    a = Pose.from_eta([0, 0, 0, 0, 0, -math.pi / 2])
    b = Pose.from_eta([0, 0, 0, 0, 0, math.pi / 2])
    assert query_pose(scene, a).status == query_pose(scene, b).status == "CLEAR"
    assert query_sweep(scene, a, b).query.status == "COLLISION"


def test_B14_wrap_and_equivalent_quaternions() -> None:
    """179/-179度中点朝西而不是朝东；q/-q插值相同。"""
    a = Pose.from_eta([0, 0, 0, 0, 0, math.radians(179)])
    b = Pose.from_eta([0, 0, 0, 0, 0, math.radians(-179)])
    assert rotation(interpolate(a, b, 0.5).orientation)[0, 0] == pytest.approx(-1.0)
    same = Pose(b.position, tuple(-x for x in b.orientation))
    assert interpolate(a, b, 0.4) == interpolate(a, same, 0.4)
    assert motion_bound(a, b, Capsule()) < 0.03


def test_B15_safe_certificate_and_budget() -> None:
    """宽净空运动认证；真实安全但过大运动界在零递归预算下应无法认证。"""
    a, b = Pose((-2, 0, 0)), Pose((2, 0, 0))
    assert query_sweep(world(), a, b).query.status == "CLEAR"
    scene = world(spheres=(Sphere("s", (0, 1, 0), 0.1),), policy=CollisionPolicy(max_depth=0))
    result = query_sweep(scene, a, b)
    assert result.query.status == "UNCERTAIN" and not result.query.certified
    assert query_sweep(replace(scene, policy=CollisionPolicy()), a, b).query.status == "CLEAR"


def test_B14_tiny_rotation_bound_is_nonzero() -> None:
    """1e-8rad不能被acos点积舍入成零；移动界仍须覆盖实际端点位移。"""
    first = Pose.from_eta([0, 0, 0, 0, 0, 0])
    last = Pose.from_eta([0, 0, 0, 0, 0, 1e-8])
    midpoint = interpolate(first, last, 0.5)
    actual = np.linalg.norm(
        (rotation(last.orientation) - rotation(midpoint.orientation)) @ np.array([0.8, 0, 0])
    )
    assert 0 < actual <= motion_bound(first, last, Capsule())


@pytest.mark.parametrize("z,label", [(79.8, "UNCERTAIN"), (79.95, "COLLISION"), (20.0, "CLEAR")])
def test_B16_general_terrain(z: float, label: str) -> None:
    """远处陡峰造成松全域界：负下界不证明接触；真实见证与安全负例分开。"""
    terrain = Terrain(bumps=(Gaussian(50.0, 50.0, 50.0, 1.0, 1.0),))
    scene = replace(world(), terrain=terrain)
    result = query_pose(scene, Pose((0, 0, z)))
    assert result.status == label and result.clearance_value_m is None
    if label == "UNCERTAIN":
        assert result.clearance_lower_bound_m < 0 and result.contact_witness is None
        assert result.near_obstacle_status == "UNKNOWN"
