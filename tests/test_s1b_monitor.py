"""B17—B21：场景重现、完整监控状态和真实物理步终止语义。"""

import json
from dataclasses import replace

import numpy as np
import pytest

from auv_env import ControlReference, RemusAdapter, RemusConfig
from auv_env.collision import query_pose
from auv_env.geometry import Pose, Sphere
from auv_env.scene import CollisionPolicy, Scene, generate_scene
from auv_env.terrain import Gaussian, Terrain
from auv_env.world_monitor import MonitorSnapshot, WorldMonitor


def deep_scene() -> Scene:
    """无事件中性诊断夹具，不伪装成300m开发场景。"""
    return Scene(
        "neutral",
        "neutral",
        Terrain(1000.0),
        lower=(-1000.0, -1000.0, -1000.0),
        upper=(1000.0, 1000.0, 1000.0),
    )


def ready_adapter() -> RemusAdapter:
    """显式水下初态；不更改原S0初态。"""
    adapter = RemusAdapter(RemusConfig(current_mps=0.0))
    adapter.reset(eta=np.array([50.0, 50.0, 20.0, 0.0, 0.0, 0.0]))
    return adapter


def impact_monitor() -> WorldMonitor:
    """真实一步将艇首推向球；初态已在实体外。"""
    adapter = ready_adapter()
    adapter.reset(
        eta=np.array([99.05, 100.0, 20.0, 0.0, 0.0, 0.0]),
        nu=np.array([10.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
    )
    scene = replace(deep_scene(), spheres=(Sphere("target", (100.1, 100.0, 20.0), 0.2),))
    return WorldMonitor(adapter, scene)


@pytest.mark.parametrize("kind", ["flat", "slope", "hills", "ridge", "channel"])
def test_B17_scene_roundtrip(kind: str) -> None:
    """相同种子重复生成及读回几何一致，不在读取时重抽参数。"""
    scene = generate_scene(kind, 17)
    assert scene.to_json() == generate_scene(kind, 17).to_json()
    assert Scene.from_json(scene.to_json()) == scene
    if kind in {"hills", "ridge", "channel"}:
        assert scene.terrain != generate_scene(kind, 29).terrain
        assert sum(b.amplitude_m for b in scene.terrain.bumps) <= 55.0
        assert all(
            25 <= scene.terrain.depth_at(x, y) <= 80 for x in (0, 150, 300) for y in (0, 150, 300)
        )


def test_B17_hash_and_read_only() -> None:
    """生成与只读查询不消耗适配器RNG；篡改内容必须拒绝。"""
    adapter = ready_adapter()
    before = adapter.get_state().to_json()
    scene = generate_scene("hills", 17)
    for _ in range(3):
        query_pose(scene, Pose((50, 50, 20)))
        Scene.from_json(scene.to_json())
    assert adapter.get_state().to_json() == before
    data = json.loads(scene.to_json())
    data["scene"]["seed"] = 29
    with pytest.raises(ValueError, match="哈希"):
        Scene.from_json(json.dumps(data))


def test_B18_monitor_neutral() -> None:
    """1000个真实物理步完全对照全部状态，不只比较位置；包含参考切换。"""
    bare = ready_adapter()
    monitor = WorldMonitor(bare, deep_scene())
    for index in range(1000):
        reference = ControlReference(
            25.0 if index < 500 else 35.0, 0.3 if index < 500 else -0.4, 1300.0
        )
        expected = bare.step(reference).state
        state, result = monitor.step(reference)
        assert result.query.status == "CLEAR"
        assert state.physical.to_json() == expected.to_json()


def test_B19_branches_snapshots_and_external_mutation() -> None:
    """主实例与两分支相互隔离，外部数组/事件字典不可污染真实状态。"""
    monitor = WorldMonitor(ready_adapter(), deep_scene())
    for _ in range(20):
        monitor.step(ControlReference())
    saved = monitor.get_state().to_json()
    a, b = monitor.clone(), monitor.clone()
    a.step(ControlReference(10, -0.5, 1000))
    after_a = a.get_state().to_json()
    assert monitor.get_state().to_json() == saved == b.get_state().to_json()
    b.step(ControlReference(40, 0.6, 1400))
    assert a.get_state().to_json() == after_a and monitor.get_state().to_json() == saved
    outside = monitor.get_state()
    outside.physical.eta[:] = 999
    outside.policy["max_depth"] = 0
    assert monitor.get_state().to_json() == saved
    b.set_state(MonitorSnapshot.from_json(saved))
    for _ in range(100):
        assert (
            monitor.step(ControlReference())[0].to_json() == b.step(ControlReference())[0].to_json()
        )


def test_B19_terminated_replay() -> None:
    """事件前快照重放得到同一终止状态；终止快照可完整克隆并保留拒绝推进。"""
    monitor = impact_monitor()
    before = monitor.get_state()
    first, _ = monitor.step(ControlReference())
    assert first.terminated
    monitor.set_state(before)
    second, _ = monitor.step(ControlReference())
    assert second.to_json() == first.to_json()
    copied = monitor.clone()
    assert copied.get_state().to_json() == first.to_json()
    with pytest.raises(RuntimeError):
        copied.step(ControlReference())


def test_B20_terminal_state_not_rollback() -> None:
    """保留真实步前/后、上次认证状态与见证时间；拒绝再次推进且无回滚。"""
    monitor = impact_monitor()
    before = monitor.get_state()
    after, result = monitor.step(ControlReference())
    assert after.termination_reason == "collision"
    assert after.physical.step_count == before.physical.step_count + 1
    assert after.physical.eta[0] > before.physical.eta[0]
    assert after.last_certified.to_json() == before.physical.to_json()
    assert after.event["after"] == after.physical.to_json()
    assert 0 <= result.evidence_time_s <= 0.02
    saved = after.to_json()
    with pytest.raises(RuntimeError):
        monitor.step(ControlReference())
    assert monitor.get_state().to_json() == saved


@pytest.mark.parametrize("fault", ["hash", "policy", "nan", "missing", "terminal"])
def test_B21_atomic_restore(fault: str) -> None:
    """非法监控快照必须原子拒绝。"""
    monitor = WorldMonitor(ready_adapter(), deep_scene())
    before = monitor.get_state().to_json()
    snapshot = monitor.get_state()
    with pytest.raises(ValueError):
        if fault == "hash":
            snapshot.scene_hash = "bad"
        elif fault == "policy":
            snapshot.policy["max_depth"] = 0
        elif fault == "nan":
            snapshot.physical.eta[0] = np.nan
        elif fault == "missing":
            snapshot = MonitorSnapshot.from_json("{}")
        else:
            snapshot.terminated = True
        monitor.set_state(snapshot)
    assert monitor.get_state().to_json() == before


def test_B21_invalid_scene_pose_reset() -> None:
    """错误形状/NaN/重复ID/非法初态/reset均拒绝，不修改主状态。"""
    with pytest.raises(ValueError):
        Pose((1, 2))
    with pytest.raises(ValueError):
        Pose((1, 2, np.nan))
    with pytest.raises(ValueError):
        replace(deep_scene(), spheres=(Sphere("s", (0, 0, 0), 1),) * 2)
    with pytest.raises(ValueError):
        WorldMonitor(RemusAdapter(), generate_scene("flat", 17))
    monitor = WorldMonitor(ready_adapter(), deep_scene())
    saved = monitor.get_state().to_json()
    with pytest.raises(ValueError):
        monitor.reset(eta=np.zeros(5))
    assert monitor.get_state().to_json() == saved


def test_B20_boundary_and_uncertain_terminal() -> None:
    """操作越界与无法认证单独终止，不混进已证实实体碰撞。"""
    adapter = ready_adapter()
    adapter.reset(
        eta=np.array([299.0, 20.0, 20.0, 0.0, 0.0, 0.0]),
        nu=np.array([20.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
    )
    boundary = WorldMonitor(adapter, generate_scene("flat", 17))
    assert boundary.step(ControlReference())[0].termination_reason == "domain_boundary"
    adapter.reset(
        eta=np.array([0.0, 0.0, 76.5, 0.0, 0.0, 0.0]), nu=np.array([0.0, 0.0, 50.0, 0.0, 0.0, 0.0])
    )
    scene = replace(
        deep_scene(),
        terrain=Terrain(bumps=(Gaussian(50.0, 50.0, 50.0, 1.0, 1.0),)),
        policy=CollisionPolicy(max_depth=0),
    )
    monitor = WorldMonitor(adapter, scene)
    state, result = monitor.step(ControlReference())
    assert state.termination_reason == "uncertified_clearance"
    assert result.query.status == "UNCERTAIN"
    before = state.to_json()
    with pytest.raises(RuntimeError):
        monitor.step(ControlReference())
    assert monitor.get_state().to_json() == before
