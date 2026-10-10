"""导数反例及仅从真实记录绘图，不为制图推进环境。"""

from pathlib import Path

import matplotlib
import numpy as np

from auv_env.current_kinematics import current_terms, rotation
from auv_env.currents import CurrentField
from scripts.s1c_numeric import rodrigues
from scripts.s1c_trajectories import read_states
from scripts.strict_json import write_json

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def counterexamples(output: Path) -> None:
    """四个独立制造反例，保留输入、解析、两端原值与差分。"""
    records = []
    for term in ("rotation", "temporal", "spatial", "joint"):
        eta = np.array([1, 2, 30, 0.2, -0.3, 0.4])
        nu = np.array([1.2, 0.1, 0.2, 0.1, -0.2, 0.3])
        if term not in {"joint", "rotation"}:
            nu[3:] = 0
        field = CurrentField(
            "affine",
            background=[0.2, 0.3, 0.1],
            jacobian=np.eye(3) * 0.01 if term in {"spatial", "joint"} else np.zeros((3, 3)),
            partial_t=[0.02, 0.03, 0.01] if term in {"temporal", "joint"} else [0, 0, 0],
        )
        terms = current_terms(eta, nu, field.sample(eta[:3], 2))
        values = []
        for h in (-1e-4, 1e-4):
            matrix = rotation(eta[3:]) @ rodrigues(nu[3:], h)
            position = eta[:3] + h * rotation(eta[3:]) @ nu[:3]
            values.append(matrix.T @ field.sample(position, 2 + h).velocity_ned_mps)
        records.append(
            dict(
                label=term,
                eta=eta.tolist(),
                nu=nu.tolist(),
                time_s=2.0,
                h_s=1e-4,
                field=field.to_dict(),
                terms={k: v.tolist() for k, v in terms.items()},
                body_minus=values[0].tolist(),
                body_plus=values[1].tolist(),
                difference=((values[1] - values[0]) / 2e-4).tolist(),
            )
        )
    write_json(output / "derivative_counterexamples.json", {"cases": records})


def plot(run: Path) -> None:
    """三张闭环NED轨迹图，深度轴向下；全部坐标来自保存状态。"""
    output = run / "figures"
    output.mkdir(exist_ok=False)
    for scene in range(3):
        rows = read_states(run / f"trajectories/smoke-{scene}.jsonl")
        positions = np.array([row["eta"][:3] for row in rows])
        figure = plt.figure(figsize=(7, 5))
        axis = figure.add_subplot(111, projection="3d")
        axis.plot(*positions.T, label=f"L1 smoke {scene}, recorded 60 s")
        axis.set(xlabel="North x (m)", ylabel="East y (m)", zlabel="Down z (m)")
        axis.invert_zaxis()
        axis.legend()
        figure.tight_layout()
        figure.savefig(output / f"smoke-{scene}.png", dpi=140)
        plt.close(figure)
