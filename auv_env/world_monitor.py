"""组合式每0.02秒监控；保留真实步末状态，终止后拒绝继续，不做碰撞响应。"""

import copy
import json
from dataclasses import asdict, dataclass
from typing import Any

from auv_env import ControlReference, RemusAdapter, RemusSnapshot
from auv_env.collision import query_pose
from auv_env.geometry import Pose
from auv_env.scene import Scene
from auv_env.swept import INTERPOLATION_VERSION, SweepResult, query_sweep


@dataclass(slots=True)
class MonitorSnapshot:
    """组合原快照，不改变RemusSnapshot格式；event包含真实步前/步后JSON及几何证据。"""

    physical: RemusSnapshot
    last_certified: RemusSnapshot
    scene_hash: str
    policy: dict[str, Any]
    terminated: bool
    termination_reason: str | None
    event: dict[str, Any] | None
    version: int = 1
    interpolation_version: str = INTERPOLATION_VERSION

    def to_json(self) -> str:
        """透明序列化完整组合状态，无物理更新；数组由原快照严格验证。"""
        return json.dumps(
            {
                **{
                    name: copy.deepcopy(getattr(self, name))
                    for name in (
                        "scene_hash",
                        "policy",
                        "terminated",
                        "termination_reason",
                        "event",
                        "version",
                        "interpolation_version",
                    )
                },
                "physical": json.loads(self.physical.to_json()),
                "last_certified": json.loads(self.last_certified.to_json()),
            },
            sort_keys=True,
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, text: str) -> "MonitorSnapshot":
        """解析完整字段；环境一致性和事件验证由set_state原子执行。"""
        try:
            data = json.loads(text)
            if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
                raise ValueError("监控快照字段不完整")
            for name in ("physical", "last_certified"):
                data[name] = RemusSnapshot.from_json(json.dumps(data[name]))
            return cls(**data)
        except (KeyError, TypeError) as error:
            raise ValueError("监控快照 JSON 非法") from error


class WorldMonitor:
    """持有独立适配器副本和不可变场景；构造要求完整初态已可认证安全。"""

    def __init__(self, adapter: RemusAdapter, scene: Scene) -> None:
        if not isinstance(adapter, RemusAdapter) or not isinstance(scene, Scene):
            raise ValueError("监控需要适配器和场景")
        if adapter.config.dt_s != 0.02:
            raise ValueError("S1-B 监控固定每0.02秒检查")
        self._scene = scene
        self._adapter = adapter.clone()
        state = self._adapter.get_state()
        if query_pose(scene, Pose.from_eta(state.eta)).status != "CLEAR":
            raise ValueError("初态碰撞/越界/无法认证，禁止自动挪动")
        self._last_certified = state
        self._terminated = False
        self._reason: str | None = None
        self._event: dict[str, Any] | None = None

    @property
    def scene(self) -> Scene:
        """返回真正不可变场景；无副作用。"""
        return self._scene

    def get_state(self) -> MonitorSnapshot:
        """返回完整深副本；不查询推进、不消费RNG。"""
        return MonitorSnapshot(
            self._adapter.get_state(),
            copy.deepcopy(self._last_certified),
            self.scene.scene_hash,
            asdict(self.scene.policy),
            self._terminated,
            self._reason,
            copy.deepcopy(self._event),
        )

    def set_state(self, snapshot: MonitorSnapshot) -> None:
        """验证场景/规则/完整状态/终止事件后原子恢复；错误抛ValueError，原状态不变。"""
        if not isinstance(snapshot, MonitorSnapshot):
            raise ValueError("监控快照类型错误")
        candidate = MonitorSnapshot.from_json(snapshot.to_json())
        if (
            type(candidate.version) is not int
            or candidate.version != 1
            or candidate.interpolation_version != INTERPOLATION_VERSION
            or candidate.scene_hash != self.scene.scene_hash
            or candidate.policy != asdict(self.scene.policy)
            or type(candidate.terminated) is not bool
        ):
            raise ValueError("场景/监控规则/版本不一致")
        physical = self._adapter.clone()
        physical.set_state(candidate.physical)
        candidate.last_certified.validated_copy(physical.config)
        if query_pose(self.scene, Pose.from_eta(candidate.last_certified.eta)).status != "CLEAR":
            raise ValueError("上次认证状态不安全")
        if candidate.terminated:
            self._validate_event(candidate)
        elif (
            candidate.event is not None
            or candidate.termination_reason is not None
            or candidate.last_certified.to_json() != candidate.physical.to_json()
        ):
            raise ValueError("未终止快照状态不一致")
        self._adapter = physical
        self._last_certified = candidate.last_certified
        self._terminated, self._reason, self._event = (
            candidate.terminated,
            candidate.termination_reason,
            candidate.event,
        )

    def _validate_event(self, state: MonitorSnapshot) -> None:
        """重算已终止事件的几何证据，不信任外部可编辑字典中的安全标签。"""
        if not isinstance(state.event, dict) or set(state.event) != {"before", "after", "sweep"}:
            raise ValueError("终止事件不完整")
        if (
            state.event["before"] != state.last_certified.to_json()
            or state.event["after"] != state.physical.to_json()
            or state.physical.step_count != state.last_certified.step_count + 1
        ):
            raise ValueError("终止步前步后不一致")
        actual = query_sweep(
            self.scene, Pose.from_eta(state.last_certified.eta), Pose.from_eta(state.physical.eta)
        )
        if actual.query.status == "CLEAR" or state.termination_reason != self._termination(actual):
            raise ValueError("终止原因与几何事实不一致")
        if json.dumps(asdict(actual), sort_keys=True) != json.dumps(
            state.event["sweep"], sort_keys=True
        ):
            raise ValueError("终止证据被篡改")

    @staticmethod
    def _termination(result: SweepResult) -> str:
        """碰撞、操作越界与未认证分开计数。"""
        if result.query.status == "UNCERTAIN":
            return "uncertified_clearance"
        return "domain_boundary" if result.query.reason == "domain_boundary" else "collision"

    def step(self, reference: ControlReference) -> tuple[MonitorSnapshot, SweepResult]:
        """真实推进一物理步再监控，返回完整状态和扫掠；终止后抛RuntimeError且不推进。"""
        if self._terminated:
            raise RuntimeError("监控已终止，须reset或恢复后才能推进")
        before = self._adapter.get_state()
        after = self._adapter.step(reference).state
        result = query_sweep(self.scene, Pose.from_eta(before.eta), Pose.from_eta(after.eta), 0.02)
        if result.query.status != "CLEAR":
            self._terminated, self._reason = True, self._termination(result)
            self._event = {
                "before": before.to_json(),
                "after": after.to_json(),
                "sweep": asdict(result),
            }
        else:
            self._last_certified = after
        return self.get_state(), result

    def reset(self, **initial: Any) -> MonitorSnapshot:
        """以原适配器reset参数重置；先验证完整候选，非法初态不修改现有监控。"""
        candidate = self._adapter.clone()
        candidate.reset(**initial)
        validated = WorldMonitor(candidate, self.scene)
        self.set_state(validated.get_state())
        return self.get_state()

    def clone(self) -> "WorldMonitor":
        """从已认证状态构造后恢复完整快照，支持已终止分支，所有可变对象独立。"""
        candidate = self._adapter.clone()
        candidate.set_state(self._last_certified)
        result = WorldMonitor(candidate, self.scene)
        result.set_state(self.get_state())
        return result
