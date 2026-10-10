"""从锁定来源构造只读参数快照；不调用旧动力学推进L1。"""

import hashlib
import json
from functools import lru_cache
from typing import Any

import numpy as np
from python_vehicle_simulator.vehicles import remus100

MODEL_ID = "remus100-l1-openwater-v1"
PARAMETER_NAMES = (
    "rho",
    "L",
    "diam",
    "S",
    "CD_0",
    "r_bg",
    "r_bb",
    "MRB",
    "MA",
    "M",
    "W",
    "B",
    "w_roll",
    "w_pitch",
    "CL_delta_r",
    "A_r",
    "x_r",
    "CL_delta_s",
    "A_s",
    "x_s",
    "T_surge",
    "T_sway",
    "T_heave",
    "zeta_roll",
    "zeta_pitch",
    "T_yaw",
    "deltaMax_r",
    "deltaMax_s",
    "nMax",
    "T_delta",
    "T_n",
)


def export_parameters() -> dict[str, Any]:
    """返回SI参数/惯性矩阵独立副本；质量按椭球公式复算，非注释31.9kg。"""
    vehicle = remus100()
    data = {
        name: (
            getattr(vehicle, name).tolist()
            if isinstance(getattr(vehicle, name), np.ndarray)
            else float(getattr(vehicle, name))
        )
        for name in PARAMETER_NAMES
    }
    data["mass_kg"] = 4 / 3 * np.pi * vehicle.rho * (vehicle.L / 2) * (vehicle.diam / 2) ** 2
    mass = data["mass_kg"]
    data["inertia_cg_kgm2"] = [
        2 / 5 * mass * (vehicle.diam / 2) ** 2,
        1 / 5 * mass * ((vehicle.L / 2) ** 2 + (vehicle.diam / 2) ** 2),
        1 / 5 * mass * ((vehicle.L / 2) ** 2 + (vehicle.diam / 2) ** 2),
    ]
    data["source_commit"] = "c2dfbd4f4425d29bdcba98294563cda19890cdb1"
    data["CA_zero_indices"] = [[4, 0], [0, 4], [4, 2], [2, 4], [5, 0], [0, 5], [5, 1], [1, 5]]
    data["propeller"] = dict(
        diameter_m=0.14,
        thrust_deduction=0.1,
        advance_factor=0.944,
        Ja_max=0.6632,
        KT_0=0.4566,
        KT_max=0.1798,
        KQ_0=0.0700,
        KQ_max=0.0312,
        roll_scale=0.1,
    )
    return data


@lru_cache(maxsize=1)
def parameter_hash() -> str:
    """参数及来源标识的稳定SHA256。"""
    return hashlib.sha256(json.dumps(export_parameters(), sort_keys=True).encode()).hexdigest()
