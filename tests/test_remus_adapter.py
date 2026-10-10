"""适配接口、原始时序、合法边界、失败原子性和读取无副作用。"""

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import numpy as np
import pytest
from python_vehicle_simulator.lib.gnc import attitudeEuler
from python_vehicle_simulator.vehicles import remus100

from auv_env import ControlReference, RemusAdapter, RemusConfig
from auv_env.state import CONTROLLER_FIELDS


def test_one_step_order_and_actuator() -> None:
    """独立直接上游调用，核对控制器、速度、姿态及实际执行器采样时刻。"""
    adapter = RemusAdapter()
    upstream = remus100("depthHeadingAutopilot", 30, 50, 1525, 0.5, 170)
    eta, nu, actuator = np.zeros(6), np.zeros(6), np.zeros(3)
    for index in range(40):
        command = upstream.depthHeadingAutopilot(eta, nu, 0.02)
        nu, actuator = upstream.dynamics(eta, nu, actuator, command, 0.02)
        eta = attitudeEuler(eta, nu, 0.02)
        result = adapter.step(ControlReference())
        np.testing.assert_allclose(result.state.eta, eta, atol=1e-10, rtol=1e-10)
        np.testing.assert_array_equal(result.state.nu, nu)
        np.testing.assert_array_equal(result.state.actuator, actuator)
        np.testing.assert_array_equal(result.command, command)
        assert result.state.step_count == index + 1
        assert result.state.controller == {
            name: getattr(upstream, name) for name in CONTROLLER_FIELDS
        }


def test_mutable_controller_field_audit() -> None:
    """审计冻结类的非构造方法：任何新增 self 写入均必须进入快照清单。"""
    path = (
        Path(__file__).resolve().parents[1]
        / "PythonVehicleSimulator/src/python_vehicle_simulator/vehicles/remus100.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fields = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name != "__init__":
            for child in ast.walk(node):
                if (
                    isinstance(child, ast.Attribute)
                    and isinstance(child.ctx, ast.Store)
                    and isinstance(child.value, ast.Name)
                    and child.value.id == "self"
                ):
                    fields.add(child.attr)
    assert fields == set(CONTROLLER_FIELDS)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"depth_m": -1},
        {"depth_m": 101},
        {"depth_m": np.nan},
        {"heading_rad": np.inf},
        {"propeller_rpm": -1},
        {"propeller_rpm": 1526},
        {"propeller_rpm": True},
        {"heading_rad": [1]},
        {"depth_m": "30"},
    ],
)
def test_invalid_reference(kwargs: dict) -> None:
    """所有非法参考构造失败，不接触环境。"""
    with pytest.raises(ValueError):
        ControlReference(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"dt_s": 0},
        {"dt_s": np.nan},
        {"current_mps": -1},
        {"seed": True},
        {"seed": -1},
        {"initial_reference": {}},
        {"current_direction_rad": np.inf},
    ],
)
def test_invalid_config(kwargs: dict) -> None:
    """配置有限性、范围、类型校验。"""
    with pytest.raises(ValueError):
        RemusConfig(**kwargs)


def test_reference_boundaries_and_frozen_config() -> None:
    """原模型参考区间端点合法，配置不可通过外部赋值修改。"""
    for depth, rpm in ((0, 0), (100, 1525)):
        RemusAdapter().step(ControlReference(depth, 0, rpm))
    config = RemusConfig()
    with pytest.raises(FrozenInstanceError):
        config.dt_s = 1  # type: ignore[misc]
    assert config.config_hash != RemusConfig(current_mps=0).config_hash


@pytest.mark.parametrize(
    "field,value",
    [
        ("eta", np.zeros((6, 1))),
        ("nu", np.zeros(5)),
        ("actuator", [0, 0, np.nan]),
        ("eta", [0, 0, 0, 0, 0, np.inf]),
        ("nu", ["0"] * 6),
    ],
)
def test_reset_atomic(field: str, value: object) -> None:
    """非法 reset 不得部分清空控制器或原状态。"""
    adapter = RemusAdapter()
    adapter.advance(ControlReference(15, 1.3, 1300), 30)
    before = adapter.get_state().to_json()
    with pytest.raises(ValueError):
        adapter.reset(**{field: value})
    assert adapter.get_state().to_json() == before


@pytest.mark.parametrize("count", [-1, 1.5, True, "2"])
def test_invalid_advance(count: object) -> None:
    """非法步数在任何物理/控制更新之前失败。"""
    adapter = RemusAdapter()
    before = adapter.get_state().to_json()
    with pytest.raises(ValueError):
        adapter.advance(ControlReference(), count)
    assert adapter.get_state().to_json() == before


def test_read_and_advance_equivalence() -> None:
    """重复读取、序列化、零步及克隆预览均不改变下一次控制结果。"""
    adapter, control = RemusAdapter(), RemusAdapter()
    before = adapter.get_state().to_json()
    for _ in range(5):
        adapter.get_state().to_json()
        adapter.clone().step(ControlReference())
    adapter.advance(ControlReference(), 0)
    assert adapter.get_state().to_json() == before
    adapter.advance(ControlReference(), 30)
    for _ in range(30):
        control.step(ControlReference())
    assert adapter.get_state().to_json() == control.get_state().to_json()


def test_dynamic_failure_atomic(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入返回 NaN 的动力学，要求真实失败、诊断以及原状态完整不变。"""
    adapter = RemusAdapter()
    before = adapter.get_state().to_json()

    def broken(
        self: object,
        eta: np.ndarray,
        nu: np.ndarray,
        actual: np.ndarray,
        command: np.ndarray,
        dt: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        return np.full(6, np.nan), actual

    monkeypatch.setattr(remus100, "dynamics", broken)
    with pytest.raises(FloatingPointError):
        adapter.step(ControlReference())
    assert adapter.get_state().to_json() == before


@pytest.mark.parametrize("invalid", [False, 0, "", {}])
def test_falsy_invalid_config_is_rejected(invalid: object) -> None:
    """非法假值不能被默认配置掩盖。"""
    with pytest.raises(ValueError, match="config"):
        RemusAdapter(invalid)  # type: ignore[arg-type]
