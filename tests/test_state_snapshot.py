"""完整状态恢复、独立分支、JSON往返及无效快照原子拒绝。"""

import json

import numpy as np
import pytest

from auv_env import ControlReference, RemusAdapter, RemusConfig, RemusSnapshot


def test_snapshot_roundtrip_and_rng() -> None:
    """非零历史+已消费 RNG 经 JSON恢复，后续100步及随机序列严格相同。"""
    first = RemusAdapter()
    first.advance(ControlReference(15, -0.6, 1100), 80)
    first.sample_random(13)
    second = RemusAdapter()
    saved = first.get_state()
    second.set_state(RemusSnapshot.from_json(saved.to_json()))
    for _ in range(100):
        np.testing.assert_array_equal(first.sample_random(3), second.sample_random(3))
        a = first.step(ControlReference(35, 1.2, 1400)).state
        b = second.step(ControlReference(35, 1.2, 1400)).state
        assert a.to_json() == b.to_json()
    saved.eta[:] = 999
    assert second.get_state().eta[0] != 999


@pytest.mark.parametrize(
    "fault",
    [
        "version",
        "model",
        "config",
        "hash",
        "shape",
        "nan",
        "controller",
        "controller_nan",
        "step",
        "rng_missing",
        "rng_negative",
        "reference",
    ],
)
def test_invalid_snapshot_is_atomic(fault: str) -> None:
    """完整校验未通过时，不应更新艇体、参考、控制器或 RNG 中的任何一项。"""
    adapter = RemusAdapter()
    adapter.advance(ControlReference(), 40)
    before = adapter.get_state().to_json()
    snapshot = adapter.get_state()
    if fault == "version":
        snapshot.version = 2
    elif fault == "model":
        snapshot.model_id = "other"
    elif fault == "config":
        snapshot.config = RemusConfig(current_mps=0)
    elif fault == "hash":
        snapshot.config_hash = "wrong"
    elif fault == "shape":
        snapshot.nu = np.zeros((6, 1))
    elif fault == "nan":
        snapshot.actuator[0] = np.nan
    elif fault == "controller":
        snapshot.controller.pop("z_d")
    elif fault == "controller_nan":
        snapshot.controller["psi_d"] = np.inf
    elif fault == "step":
        snapshot.step_count = -1
    elif fault == "rng_missing":
        snapshot.rng_state.pop("state")
    elif fault == "rng_negative":
        snapshot.rng_state["state"]["state"] = -1
    else:
        snapshot.reference = {}  # type: ignore[assignment]
    with pytest.raises(ValueError):
        adapter.set_state(snapshot)
    assert adapter.get_state().to_json() == before


@pytest.mark.parametrize("fault", ["missing", "extra", "nonobject", "rng", "config"])
def test_json_invalid(fault: str) -> None:
    """拒绝缺失字段、额外字段、非对象和错误结构的 JSON。"""
    data = json.loads(RemusAdapter().get_state().to_json())
    if fault == "missing":
        data.pop("actuator")
    elif fault == "extra":
        data["unknown"] = 1
    elif fault == "nonobject":
        data = []
    elif fault == "rng":
        data["rng_state"] = {}
    else:
        data["config"] = None
    with pytest.raises(ValueError):
        RemusSnapshot.from_json(json.dumps(data))


def test_branch_and_reset_complete() -> None:
    """A/B/主环境的可变状态不共享，重置恢复初始配置和 RNG。"""
    main = RemusAdapter()
    main.advance(ControlReference(20, -0.8, 1000), 50)
    saved = main.get_state().to_json()
    a, b = main.clone(), main.clone()
    a.sample_random(5)
    a.advance(ControlReference(40, 1.0, 1400), 20)
    assert main.get_state().to_json() == saved == b.get_state().to_json()
    after_a = a.get_state().to_json()
    b.advance(ControlReference(10, -1.0, 1200), 20)
    assert a.get_state().to_json() == after_a
    assert main.get_state().to_json() == saved
    main.sample_random(4)
    main.reset()
    assert main.get_state().to_json() == RemusAdapter().get_state().to_json()


def test_extreme_step_count_fails_atomically() -> None:
    """不能把超出浮点时间范围的整数快照半恢复到环境。"""
    adapter = RemusAdapter()
    before = adapter.get_state().to_json()
    invalid = adapter.get_state()
    invalid.step_count = 10**400
    with pytest.raises(ValueError, match="时间"):
        adapter.set_state(invalid)
    assert adapter.get_state().to_json() == before
