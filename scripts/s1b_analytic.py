"""预登记解析构型：期望值使用手算公式，实际值独立调用几何函数；验收时重新计算。"""

import json
import math
from dataclasses import asdict, replace
from typing import Any

import numpy as np

from auv_env.collision import query_pose
from auv_env.geometry import Box, Pose, Sphere, segment_box
from auv_env.scene import Scene
from auv_env.terrain import Gaussian, Terrain


def analytical_measurements() -> dict[str, Any]:
    """独立手算平面/球/盒距离及固定差分梯度，保存每个实际误差。"""
    scene = Scene(
        "analytic",
        "analytic",
        Terrain(80.0),
        lower=(-100.0, -100.0, -100.0),
        upper=(100.0, 100.0, 100.0),
    )
    values: list[dict[str, Any]] = []
    for z in (70.0, 79.905, 81.0):
        actual = query_pose(scene, Pose((0, 0, z))).clearance_value_m
        values.append({"case": f"flat-{z}", "expected": 80 - z - 0.095, "actual": actual})
    slope = replace(scene, terrain=Terrain(20.0, 0.3, 0.4))
    expected = (
        20 + 0.3 * 2 + 0.4 * 3 - 19 - 0.705 * abs(0.3 * math.sqrt(3) / 2 + 0.5)
    ) / math.sqrt(1.25) - 0.095
    values.append(
        {
            "case": "slope-tilted",
            "expected": expected,
            "actual": query_pose(
                slope, Pose.from_eta([2, 3, 19, 0, math.pi / 6, 0])
            ).clearance_value_m,
        }
    )
    sphere = replace(scene, terrain=Terrain(1000.0), spheres=(Sphere("s", (0, 2, 0), 0.1),))
    values.append(
        {
            "case": "sphere-side",
            "expected": 1.805,
            "actual": query_pose(sphere, Pose((0, 0, 0))).clearance_value_m,
        }
    )
    for point, expected_distance in (
        ((2, 2, 2), math.sqrt(3)),
        ((0, 0, 0), 0.0),
        ((0, 2, 2), math.sqrt(2)),
    ):
        p = np.array(point, dtype=float)
        actual = segment_box(p, p, Box("b", (-1, -1, -1), (1, 1, 1)))[0]
        values.append({"case": f"box-{point}", "expected": expected_distance, "actual": actual})
    for item in values:
        item["abs_error_m"] = abs(item["actual"] - item["expected"])
        if item["abs_error_m"] > 1e-6:
            raise ValueError("解析误差超过预登记阈值")
    terrain = Terrain(bumps=(Gaussian(10.0, 0.0, 0.0, 15.0, 20.0),))
    gradient_errors = []
    gradients = []
    for x, y in ((0.0, 0.0), (7.0, -3.0), (15.0, 20.0)):
        h = 1e-4
        difference = np.array(
            [
                (terrain.depth_at(x + h, y) - terrain.depth_at(x - h, y)) / (2 * h),
                (terrain.depth_at(x, y + h) - terrain.depth_at(x, y - h)) / (2 * h),
            ]
        )
        gradient_actual = np.array(terrain.gradient_at(x, y))
        if not np.allclose(gradient_actual, difference, atol=1e-6, rtol=1e-6):
            raise ValueError("梯度差分核对失败")
        error = float(np.max(np.abs(gradient_actual - difference)))
        gradient_errors.append(error)
        gradients.append(
            {
                "point_m": [x, y],
                "step_m": h,
                "analytic": gradient_actual.tolist(),
                "finite_difference": difference.tolist(),
                "max_abs_error": error,
                "terrain": asdict(terrain),
            }
        )
    # 这些固定输入来自原B03/B04/B07/B08预登记，复核时重新调用被测函数而非相信自报两列。
    inputs = (
        [
            {"kind": "flat", "depth_m": 80.0, "pose_m": [0, 0, z], "radius_m": 0.095}
            for z in (70.0, 79.905, 81.0)
        ]
        + [
            {
                "kind": "slope",
                "plane": [20, 0.3, 0.4],
                "eta": [2, 3, 19, 0, math.pi / 6, 0],
                "half_segment_m": 0.705,
                "radius_m": 0.095,
            },
            {"kind": "sphere", "center_m": [0, 2, 0], "radius_m": 0.1, "body_radius_m": 0.095},
        ]
        + [
            {
                "kind": "segment_box",
                "point_m": list(point),
                "lower_m": [-1, -1, -1],
                "upper_m": [1, 1, 1],
            }
            for point in ((2, 2, 2), (0, 0, 0), (0, 2, 2))
        ]
    )
    for row, inputs_row in zip(values, inputs, strict=True):
        row["input"] = inputs_row
        row["case_id"] = row["case"]
    return json.loads(
        json.dumps(
            {
                "gradients": gradients,
                "distances": values,
                "max_abs_error_m": max(v["abs_error_m"] for v in values),
                "gradient_max_abs_error": max(gradient_errors),
            },
            allow_nan=False,
        )
    )
