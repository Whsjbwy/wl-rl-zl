"""只读场景/真实JSONL绘图，绝不创建适配器或调用物理step。"""

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

from auv_env.geometry import Pose, rotation
from auv_env.scene import Scene


def draw_body(axis: Any, scene: Scene, eta: list[float]) -> None:
    """绘制真实尺寸胶囊面：中心线1.41m加端帽，不以一个点替代艇体。"""
    pose = Pose.from_eta(eta)
    body = scene.body
    angle = np.linspace(0, 2 * np.pi, 24)
    cap = np.linspace(0, np.pi / 2, 12)
    along = np.concatenate(
        [
            -body.half_segment_m - body.radius_m * np.cos(cap),
            np.linspace(-body.half_segment_m, body.half_segment_m, 4),
            body.half_segment_m + body.radius_m * np.sin(cap),
        ]
    )
    radial = np.concatenate(
        [body.radius_m * np.sin(cap), np.full(4, body.radius_m), body.radius_m * np.cos(cap)]
    )
    points = np.stack(
        [
            np.broadcast_to(along[:, None], (len(along), len(angle))),
            radial[:, None] * np.cos(angle),
            radial[:, None] * np.sin(angle),
        ],
        axis=-1,
    )
    ned = points @ rotation(pose.orientation).T + np.array(pose.position)
    axis.plot_surface(ned[:, :, 0], ned[:, :, 1], ned[:, :, 2], color="orange", alpha=0.95)


def draw_obstacles(axis: Any, scene: Scene) -> None:
    """绘制同一解析对象中的静态实体球和AABB。"""
    azimuth = np.linspace(0, 2 * np.pi, 25)
    elevation = np.linspace(0, np.pi, 16)
    for sphere in scene.spheres:
        x = sphere.center[0] + sphere.radius_m * np.outer(np.cos(azimuth), np.sin(elevation))
        y = sphere.center[1] + sphere.radius_m * np.outer(np.sin(azimuth), np.sin(elevation))
        z = sphere.center[2] + sphere.radius_m * np.outer(np.ones_like(azimuth), np.cos(elevation))
        axis.plot_surface(x, y, z, color="crimson", alpha=0.3)
    for box in scene.boxes:
        corners = np.array(
            [
                [x, y, z]
                for x in (box.lower[0], box.upper[0])
                for y in (box.lower[1], box.upper[1])
                for z in (box.lower[2], box.upper[2])
            ]
        )
        faces = [
            [corners[i] for i in indices]
            for indices in (
                (0, 1, 3, 2),
                (4, 5, 7, 6),
                (0, 1, 5, 4),
                (2, 3, 7, 6),
                (0, 2, 6, 4),
                (1, 3, 7, 5),
            )
        ]
        axis.add_collection3d(Poly3DCollection(faces, alpha=0.25, facecolor="red"))


def plot_all(source: Path, output: Path) -> None:
    """生成三张场景图与一张下界曲线；输入只读，输出目录不得已存在。"""
    output.mkdir(parents=True, exist_ok=False)
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]
    plt.rcParams["axes.unicode_minus"] = False
    curve, curve_axis = plt.subplots(figsize=(9, 5), constrained_layout=True)
    for kind in ("hills", "ridge", "channel"):
        scene = Scene.from_json((source / f"{kind}.scene.json").read_text(encoding="utf-8"))
        records = {
            intent: [
                json.loads(line)
                for line in (source / f"{kind}.{intent}.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            for intent in ("safe", "impact")
        }
        fig = plt.figure(figsize=(12, 5), layout="constrained")
        overview = fig.add_subplot(121, projection="3d")
        detail = fig.add_subplot(122, projection="3d")
        grid = np.linspace(0, 300, 55)
        north, east = np.meshgrid(grid, grid)
        depths = np.array(
            [
                [scene.terrain.depth_at(x, y) for x, y in zip(xs, ys, strict=True)]
                for xs, ys in zip(north, east, strict=True)
            ]
        )
        overview.plot_surface(north, east, depths, cmap="terrain", alpha=0.7)
        for axis in (overview, detail):
            draw_obstacles(axis, scene)
            axis.set(xlabel="北 x（m）", ylabel="东 y（m）", zlabel="向下深度 z（m）")
        for intent, rows in records.items():
            eta = np.array([row["snapshot"]["physical"]["eta"] for row in rows])
            for axis in (overview, detail):
                axis.plot(
                    eta[:, 0],
                    eta[:, 1],
                    eta[:, 2],
                    label="安全诊断" if intent == "safe" else "撞击诊断",
                )
                draw_body(axis, scene, eta[-1].tolist())
            times = [row["snapshot"]["physical"]["step_count"] * 0.02 for row in rows[1:]]
            lower = [row["sweep"]["query"]["clearance_lower_bound_m"] for row in rows[1:]]
            curve_axis.plot(times, lower, label=f"{kind}/{intent} 扫掠下界")
        event = records["impact"][-1]
        position = event["snapshot"]["physical"]["eta"][:3]
        witness = event["sweep"]["query"]["contact_witness"]
        for axis in (overview, detail):
            axis.scatter(*witness, color="black", marker="*", s=60, label="接触见证（非首接触）")
            axis.legend(fontsize=7)
        overview.set(
            xlim=(0, 300), ylim=(0, 300), zlim=(85, 0), title=f"{kind}：解析海底与真实轨迹"
        )
        detail.set(
            xlim=(position[0] - 2, position[0] + 4),
            ylim=(97, 103),
            zlim=(23, 17),
            title="局部：真实尺寸胶囊及事件",
        )
        detail.set_box_aspect((1, 1, 1))
        fig.savefig(output / f"{kind}.png", dpi=150)
        plt.close(fig)
    curve_axis.axhline(0, color="black", linewidth=0.8)
    curve_axis.axhline(1, color="gray", linestyle="--", label="1m软裕度")
    curve_axis.set(
        xlabel="真实步末时间（s）",
        ylabel="该物理步扫掠净空下界（m）",
        title="下界不是一般曲面的精确净空",
    )
    curve_axis.legend(fontsize=8)
    curve.savefig(output / "clearance.png", dpi=150)
    plt.close(curve)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plot_all(args.input, args.output)
