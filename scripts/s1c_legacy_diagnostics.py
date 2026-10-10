"""只读采集冻结L0一次连续右端的实际局部力项，不改上游源码或执行方程。"""

import sys
from types import FrameType
from typing import Any

import numpy as np
from python_vehicle_simulator.vehicles import remus100


def capture(
    eta: np.ndarray, nu: np.ndarray, actual: np.ndarray, current_ned: np.ndarray
) -> dict[str, list[Any]]:
    """复制SI状态调用冻结dynamics，返回矩阵与六维N/Nm项。

    只在该函数的return事件读取局部变量，finally恢复原trace钩子。
    这是离线诊断工具，不被L1物理核调用、不修改主实例或RNG。
    h=1仅提取连续右端，更新后的状态不进入任何正式轨迹。
    """
    vehicle = remus100(
        V_current=float(np.linalg.norm(current_ned[:2])),
        beta_current=float(np.degrees(np.arctan2(current_ned[1], current_ned[0]))),
    )
    saved: dict[str, np.ndarray] = {}
    names = (
        "CRB",
        "CA",
        "D",
        "g",
        "tau",
        "tau_liftdrag",
        "tau_crossflow",
        "nu_r",
        "nu_c",
        "Dnu_c",
        "nu_dot",
    )

    def trace(frame: FrameType, event: str, argument: Any) -> Any:
        if frame.f_code is remus100.dynamics.__code__ and event == "return":
            for name in names:
                saved[name] = np.array(frame.f_locals[name], dtype=float, copy=True)
        return trace

    previous = sys.gettrace()
    try:
        sys.settrace(trace)
        after, _ = vehicle.dynamics(eta.copy(), nu.copy(), actual.copy(), actual.copy(), 1.0)
    finally:
        sys.settrace(previous)
    if set(saved) != set(names):
        raise ValueError("冻结L0实际力项采集不完整")
    np.testing.assert_allclose(after - nu, saved["nu_dot"], atol=1e-10, rtol=1e-10)
    relative = saved["nu_r"]
    values = dict(
        CRB=saved["CRB"],
        CA_red=saved["CA"],
        D=saved["D"],
        g=saved["g"],
        prop_fin=saved["tau"],
        lift_drag=saved["tau_liftdrag"],
        crossflow=saved["tau_crossflow"],
        nu_relative=relative,
        nu_current=saved["nu_c"],
        nu_current_dot=saved["Dnu_c"],
        rigid=saved["CRB"] @ relative,
        added=saved["CA"] @ relative,
        damping=saved["D"] @ relative,
        current_inertia=vehicle.M @ saved["Dnu_c"],
        nu_dot=saved["nu_dot"],
    )
    values["F_applied"] = values["prop_fin"] + values["lift_drag"] + values["crossflow"]
    return {name: value.tolist() for name, value in values.items()}
