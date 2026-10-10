"""S1-C独立判别：合法输入、真实执行器、完整状态与异常原子性。"""

import json

import numpy as np
import pytest
from python_vehicle_simulator.vehicles import remus100

from auv_env.current_kinematics import advance_pose, current_terms, rotation
from auv_env.currents import CurrentField
from auv_env.l1 import actuators, rhs
from auv_env.l1.adapter import L1Adapter
from auv_env.l1.parameters import export_parameters
from auv_env.l1.state import L1Snapshot


@pytest.mark.parametrize("kind", ["zero", "uniform", "sinusoidal", "affine"])
def test_fields_roundtrip_alias(kind):
    """查询/恢复/输出修改均不改变场配置。"""
    source = np.array([0.2, 0.3, 0.1])
    field = CurrentField(kind, background=source, amplitude=(0.1, 0.2, 0.3), omega_radps=0.1)
    before = field.to_dict()
    source[:] = 100
    sample = field.sample([1, 2, 3], 0.4)
    expected = sample.velocity_ned_mps.copy()
    sample.velocity_ned_mps[:] = 100
    np.testing.assert_array_equal(
        CurrentField.from_dict(before).sample([1, 2, 3], 0.4).velocity_ned_mps, expected
    )
    assert before == field.to_dict()


@pytest.mark.parametrize("bad", [True, float("nan"), float("inf"), "2", [1]])
def test_bad_time(bad):
    with pytest.raises((ValueError, TypeError)):
        CurrentField().sample([0, 0, 0], bad)


@pytest.mark.parametrize("bad", [[0, 0], [0, 0, np.nan], [True] * 3, ["0"] * 3, [[0] * 3]])
def test_bad_position(bad):
    with pytest.raises(ValueError):
        CurrentField().sample(bad, 0)


def test_mass_and_rotation():
    p = export_parameters()
    assert abs(p["mass_kg"] - 31.02938497577544) < 1e-10
    for key in ("MRB", "MA", "M"):
        m = np.array(p[key])
        np.testing.assert_allclose(m, m.T, atol=1e-12, rtol=0)
        assert np.linalg.eigvalsh(m).min() > 0
    np.testing.assert_allclose(rotation([0, 0, np.pi / 2]) @ [1, 0, 0], [0, 1, 0], atol=1e-12)
    np.testing.assert_allclose(rotation([0, np.pi / 2, 0]) @ [1, 0, 0], [0, 0, -1], atol=1e-12)


@pytest.mark.parametrize("dt", [0.02, 0.01, 0.005])
def test_zoh(dt):
    initial = np.array([-0.2, 0.1, 100.0])
    value = initial.copy()
    raw = np.array([1.0, -1.0, 2000.0])
    command = np.clip(raw, actuators.LOWER, actuators.UPPER)
    for _ in range(200):
        _, value = actuators.update(value, raw, dt)
    expected = command + (initial - command) * np.exp(-200 * dt / actuators.TIME_CONSTANT_S)
    np.testing.assert_allclose(value, expected, atol=1e-10, rtol=0)


@pytest.mark.parametrize("bad", [[0, 0, -1], [0.3, 0, 900], [0, -0.3, 900], [0, 0, 1526]])
def test_bad_actual_atomic_reset(bad):
    env = L1Adapter()
    before = env.get_state().to_json()
    with pytest.raises(ValueError):
        env.reset(actuator=bad)
    assert before == env.get_state().to_json()


@pytest.mark.parametrize("dt", [0, -1, np.inf, True])
def test_bad_actuator_dt(dt):
    with pytest.raises(ValueError):
        actuators.update([0, 0, 0], [0, 0, 900], dt)


def test_current_full_rotation_and_no_double_drift():
    eta, nu = np.array([0, 0, 30, 0.2, 0.3, 0.4]), np.array([1, 0.1, 0.2, 0, 0, 0])
    sample = CurrentField("uniform", background=[0.3, 0.2, 0]).sample(eta[:3], 0)
    terms = current_terms(eta, nu, sample)
    assert abs(terms["c_body"][2]) > 0.01
    np.testing.assert_allclose(
        advance_pose(eta, nu, 0.02)[:3] - eta[:3], 0.02 * rotation(eta[3:]) @ nu[:3], atol=1e-14
    )


def test_no_current_rhs_and_actual_not_command():
    eta = np.array([0, 0, 30, 0.1, -0.1, 0.2])
    nu = np.array([1.2, 0.1, 0.05, 0.02, -0.01, 0.03])
    actual = np.array([0.02, -0.03, 900.0])
    original, _ = remus100().dynamics(eta.copy(), nu.copy(), actual.copy(), actual.copy(), 1.0)
    result = rhs.evaluate(eta, nu, actual, CurrentField().sample(eta[:3], 0))
    np.testing.assert_allclose(result["nu_dot"], original - nu, atol=1e-10, rtol=1e-10)
    env = L1Adapter()
    a, b = env.clone().step(raw_command=[0, 0, 0]), env.clone().step(raw_command=[0, 0, 1525])
    np.testing.assert_array_equal(a.state.nu, b.state.nu)
    assert a.state.actuator[2] != b.state.actuator[2]


@pytest.mark.parametrize("target", ["field", "rhs", "attitude", "serialize"])
@pytest.mark.parametrize("error", [ValueError, FloatingPointError, RuntimeError])
def test_atomic_retry(monkeypatch, target, error):
    """各失败点保持全部JSON/RNG/控制器；解除故障仅推进一次。"""
    env = L1Adapter(CurrentField("uniform", background=[0, 0.3, 0]))
    control, branch = env.clone(), env.clone()
    before = env.get_state().to_json()

    def fail(*args, **kwargs):
        raise error("故障注入")

    with monkeypatch.context() as patch:
        if target == "field":
            patch.setattr(CurrentField, "sample", fail)
        elif target == "rhs":
            patch.setattr(rhs, "evaluate", fail)
        elif target == "attitude":
            patch.setattr("auv_env.l1.adapter.advance_pose", fail)
        else:
            patch.setattr(L1Snapshot, "to_json", fail)
        with pytest.raises(error):
            env.step()
    assert before == env.get_state().to_json() == branch.get_state().to_json()
    assert env.step().state.to_json() == control.step().state.to_json()
    assert env.get_state().step_count == 1


def test_snapshot_isolation_and_reject():
    env = L1Adapter()
    env.sample_random(7)
    snapshot = env.step().state
    restored = L1Snapshot.from_json(snapshot.to_json())
    branch = env.clone()
    branch.set_state(restored)
    snapshot.eta[:] = 0
    snapshot.field["config"]["background"][0] = 99
    assert env.get_state().to_json() == branch.get_state().to_json()
    for _ in range(20):
        assert env.step().state.to_json() == branch.step().state.to_json()
    np.testing.assert_array_equal(env.sample_random(5), branch.sample_random(5))
    with pytest.raises(ValueError):
        L1Adapter(dt_s=0.01).set_state(restored)
    with pytest.raises(ValueError):
        L1Adapter(CurrentField("uniform", background=[1, 0, 0])).set_state(restored)
    data = json.loads(restored.to_json())
    data["nu"][0] = float("nan")
    with pytest.raises(ValueError):
        L1Snapshot.from_json(json.dumps(data))


def test_ma_zero_limit_and_singular_attitude():
    p = export_parameters()
    force = np.arange(6, dtype=float)
    zeros = np.zeros(6)
    actual = rhs.momentum_solve(
        np.array(p["MRB"]), np.zeros((6, 6)), force, zeros, zeros, zeros, zeros, np.ones(6)
    )
    np.testing.assert_allclose(np.array(p["MRB"]) @ actual, force, atol=1e-12)
    with pytest.raises(ValueError):
        advance_pose([0, 0, 30, 0, np.pi / 2, 0], np.zeros(6), 0.02)


def test_rotation_orthogonality_100():
    """手算滚转方向及100组三维旋转正交/行列式，绝对门槛1e-12。"""
    np.testing.assert_allclose(rotation([np.pi / 2, 0, 0]) @ [0, 1, 0], [0, 0, 1], atol=1e-12)
    for angles in np.random.default_rng(20261011).uniform(-np.pi, np.pi, (100, 3)):
        matrix = rotation(angles)
        np.testing.assert_allclose(matrix.T @ matrix, np.eye(3), atol=1e-12, rtol=0)
        assert abs(np.linalg.det(matrix) - 1) <= 1e-12


@pytest.mark.parametrize("constants", [[0, 0.1, 1], [-0.1, 0.1, 1], [0.1, np.inf, 1]])
def test_bad_time_constants(constants):
    with pytest.raises(ValueError):
        actuators.update([0, 0, 900], [0, 0, 900], 0.02, constants)


@pytest.mark.parametrize(
    "attribute", ["version", "parameter_hash", "model_id", "dt_s", "step_count"]
)
def test_snapshot_identity_rejection(attribute):
    env = L1Adapter()
    before = env.get_state().to_json()
    state = env.get_state()
    setattr(
        state,
        attribute,
        {
            "version": 2,
            "parameter_hash": "wrong",
            "model_id": "L0",
            "dt_s": 0.01,
            "step_count": True,
        }[attribute],
    )
    with pytest.raises(ValueError):
        env.set_state(state)
    assert before == env.get_state().to_json()


@pytest.mark.parametrize("bad", [False, 0, "", {}])
def test_invalid_configuration_not_silent_default(bad):
    """假值配置也必须拒绝，不能被or默认值分支静默接受。"""
    with pytest.raises(ValueError):
        L1Adapter(field=bad)
    env = L1Adapter()
    before = env.get_state().to_json()
    with pytest.raises(ValueError):
        env.reset(reference=bad)
    assert env.get_state().to_json() == before
