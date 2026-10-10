"""R1真实监控故障注入；检查完整事务、重试、恢复、分支和非法查询结果。"""

import json
from dataclasses import replace

import pytest

import auv_env.world_monitor as module
from auv_env import ControlReference, RemusAdapter, RemusConfig
from auv_env.scene import Scene
from auv_env.terrain import Terrain
from auv_env.world_monitor import MonitorSnapshot, WorldMonitor


def monitor() -> WorldMonitor:
    """宽域诊断不修改冻结初态；远离实体以隔离异常事务语义。"""
    return WorldMonitor(
        RemusAdapter(RemusConfig(current_mps=0)),
        Scene("r1", "r1", Terrain(1000), lower=(-1000, -1000, -1000), upper=(1000, 1000, 1000)),
    )


@pytest.mark.parametrize("error", [ValueError, FloatingPointError, RuntimeError])
def test_r1_exception_retry(
    error: type[Exception], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """异常后包括RNG/执行器/控制器的完整JSON不变；对照、恢复和双分支一致。"""
    main = monitor()
    main.step(ControlReference(20, 0.1, 1000))
    before = main.get_state().to_json()
    control, untouched = main.clone(), main.clone()
    with monkeypatch.context() as patch:

        def fail(*args: object, **kwargs: object) -> None:
            raise error("r1_injected")

        patch.setattr(module, "query_sweep", fail)
        with pytest.raises(error, match="r1_injected"):
            main.step(ControlReference())
    assert main.get_state().to_json() == before == untouched.get_state().to_json()
    assert "candidate=" in caplog.text and "Traceback" in caplog.text
    recovered = main.clone()
    recovered.set_state(MonitorSnapshot.from_json(before))
    actual = main.step(ControlReference())[0]
    assert actual.to_json() == control.step(ControlReference())[0].to_json()
    assert actual.to_json() == recovered.step(ControlReference())[0].to_json()
    assert actual.physical.step_count == json.loads(before)["physical"]["step_count"] + 1
    assert untouched.get_state().to_json() == before


@pytest.mark.parametrize(
    "fault",
    [
        "type",
        "status",
        "nan",
        "count_bool",
        "negative_count",
        "certified",
        "time",
        "witness",
        "interval",
        "pose",
    ],
)
def test_r1_invalid_result_atomic(fault: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """非法结构、NaN、布尔计数、时域、伪见证和位姿转换失败均不提交。"""
    main = monitor()
    before = main.get_state().to_json()
    real = module.query_sweep

    def invalid(*args: object, **kwargs: object) -> object:
        result = real(*args, **kwargs)
        if fault == "type":
            return None
        if fault == "time":
            return replace(result, step_interval_s=(0.0, 1.0))
        if fault == "interval":
            return replace(result, evidence_interval_s=(0, 0.01))
        changes = {
            "status": {"status": "INVALID"},
            "nan": {"clearance_lower_bound_m": float("nan")},
            "count_bool": {"query_count": True},
            "negative_count": {"subdivisions": -1},
            "certified": {"certified": False},
            "witness": {"contact_witness": (0, 0, 0)},
        }
        return replace(result, query=replace(result.query, **changes[fault]))

    with monkeypatch.context() as patch:
        if fault == "pose":

            def bad_pose(*args: object) -> None:
                raise ValueError("pose失败")

            patch.setattr(module.Pose, "from_eta", bad_pose)
        else:
            patch.setattr(module, "query_sweep", invalid)
        with pytest.raises(ValueError):
            main.step(ControlReference())
    assert main.get_state().to_json() == before
    assert main.step(ControlReference())[0].physical.step_count == 1


def test_r1_invalid_reference() -> None:
    """物理候选输入失败也不影响主实例。"""
    main = monitor()
    before = main.get_state().to_json()
    with pytest.raises((ValueError, TypeError)):
        main.step(None)
    assert main.get_state().to_json() == before
