"""只读已生成的原始轨迹与恢复轨迹绘图，不创建或推进仿真实例。"""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def plot_replay(folder: Path, output: Path) -> None:
    """从本地第000组完整状态 JSONL 绘制深度与恢复误差，输出 PNG；不覆盖已有图。"""
    if output.exists():
        raise FileExistsError(output)
    traces = [
        [
            json.loads(line)
            for line in (folder / f"000.{name}.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        for name in ("original", "restored")
    ]
    times = [row["step_count"] * row["config"]["dt_s"] for row in traces[0]]
    depths = [[row["eta"][2] for row in trace] for trace in traces]
    # Windows 字体优先；缺少该字体的平台可回退到已安装的中文字体。
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, axes = plt.subplots(2, 1, figsize=(8, 6), constrained_layout=True)
    axes[0].plot(times, depths[0], label="原始轨迹")
    axes[0].plot(times, depths[1], "--", label="快照恢复")
    axes[0].set(ylabel="深度（m）", title="S1-A 快照重放：本次实测轨迹")
    axes[0].legend()
    axes[1].plot(times, [a - b for a, b in zip(*depths, strict=True)])
    axes[1].set(xlabel="时间（s）", ylabel="深度误差（m）")
    fig.savefig(output, dpi=130)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    plot_replay(args.input, args.output)
