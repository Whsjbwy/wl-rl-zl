"""保持冻结模型控制、速度、姿态更新顺序的事务式单步接口。"""

import copy
import logging
import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
from python_vehicle_simulator.lib.gnc import attitudeEuler
from python_vehicle_simulator.vehicles import remus100

from auv_env.config import ControlReference, RemusConfig, nonnegative_int
from auv_env.state import CONTROLLER_FIELDS, MODEL_ID, RemusSnapshot, Vector, vector


@dataclass(slots=True)
class StepResult:
    """state 是更新后独立快照；command 是本步实际指令 (3,) rad/rad/rpm。"""

    state: RemusSnapshot
    command: Vector
    start_time_s: float


class RemusAdapter:
    """确定性 REMUS 物理适配；模型不消费 RNG，独立 RNG 仍完整进入快照。"""

    def __init__(self, config: RemusConfig | None = None) -> None:
        self._config = RemusConfig() if config is None else config
        if not isinstance(self._config, RemusConfig):
            raise ValueError("config 类型错误")
        self.reset()

    @property
    def config(self) -> RemusConfig:
        """取得不可变配置，无副作用。"""
        return self._config

    def _new_vehicle(self, reference: ControlReference) -> Any:
        return remus100(
            "depthHeadingAutopilot",
            reference.depth_m,
            math.degrees(reference.heading_rad),
            reference.propeller_rpm,
            self.config.current_mps,
            math.degrees(self.config.current_direction_rad),
        )

    def reset(
        self,
        *,
        eta: Vector | None = None,
        nu: Vector | None = None,
        actuator: Vector | None = None,
    ) -> RemusSnapshot:
        """重置到指定 (6,)/(6,)/(3,) 状态，缺省全零；单位见快照。

        同时重建控制器、初始参考和种子。非法形状/NaN/Inf 抛 ValueError，原状态不变。
        reset 有重置副作用，返回与内部不共享数组的完整快照。
        """
        initial_eta = vector(np.zeros(6) if eta is None else eta, 6, "eta")
        initial_nu = vector(np.zeros(6) if nu is None else nu, 6, "nu")
        initial_actuator = vector(np.zeros(3) if actuator is None else actuator, 3, "actuator")
        vehicle = self._new_vehicle(self.config.initial_reference)
        rng = np.random.Generator(np.random.PCG64(self.config.seed))
        self._vehicle, self._rng = vehicle, rng
        self._eta, self._nu, self._actuator = initial_eta, initial_nu, initial_actuator
        self._reference = self.config.initial_reference
        self._last_command = np.zeros(3, dtype=np.float64)
        self._step_count = 0
        return self.get_state()

    def get_state(self) -> RemusSnapshot:
        """返回完整深副本，无控制器调用、时间推进或随机数消费。"""
        return RemusSnapshot(
            1,
            MODEL_ID,
            self.config,
            self.config.config_hash,
            self._step_count,
            self._eta.copy(),
            self._nu.copy(),
            self._actuator.copy(),
            {name: float(getattr(self._vehicle, name)) for name in CONTROLLER_FIELDS},
            self._reference,
            self._last_command.copy(),
            copy.deepcopy(dict(self._rng.bit_generator.state)),
        )

    def set_state(self, snapshot: RemusSnapshot) -> None:
        """原子恢复完整快照；先验证全部字段并构造候选对象，失败不留下半更新。"""
        if not isinstance(snapshot, RemusSnapshot):
            raise ValueError("snapshot 必须为 RemusSnapshot")
        candidate = snapshot.validated_copy(self.config)
        vehicle = self._new_vehicle(candidate.reference)
        for name, value in candidate.controller.items():
            setattr(vehicle, name, value)
        rng = np.random.Generator(np.random.PCG64())
        rng.bit_generator.state = candidate.rng_state
        self._vehicle, self._rng = vehicle, rng
        self._eta, self._nu, self._actuator = candidate.eta, candidate.nu, candidate.actuator
        self._reference, self._last_command = candidate.reference, candidate.last_command
        self._step_count = candidate.step_count

    def clone(self) -> "RemusAdapter":
        """复制完整执行状态；新实例的模型、数组、控制器、RNG 全部独立。"""
        result = RemusAdapter(self.config)
        result.set_state(self.get_state())
        return result

    def step(self, reference: ControlReference) -> StepResult:
        """推进一个 dt_s；输入深度 m/航向 rad/转速 rpm，返回步后快照及本步指令。

        控制器先更新记忆，再更新速度/实际执行器，最后用新速度更新姿态。
        冻结 dynamics 会原地修改 nu，因此仅传入候选副本，不额外限幅或叠加海流。
        非法输入抛 ValueError；数值异常记录诊断、抛 FloatingPointError，整步不提交。
        """
        if not isinstance(reference, ControlReference):
            raise ValueError("reference 必须为 ControlReference")
        reference = ControlReference(**asdict(reference))
        vehicle = copy.deepcopy(self._vehicle)
        vehicle.ref_z = reference.depth_m
        vehicle.ref_psi = math.degrees(reference.heading_rad)
        vehicle.ref_n = reference.propeller_rpm
        eta, nu, actuator = self._eta.copy(), self._nu.copy(), self._actuator.copy()
        try:
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                command = vehicle.depthHeadingAutopilot(eta, nu, self.config.dt_s)
                nu, actuator = vehicle.dynamics(eta, nu, actuator, command, self.config.dt_s)
                eta = attitudeEuler(eta, nu, self.config.dt_s)
            values = np.concatenate(
                [eta, nu, actuator, command, [getattr(vehicle, name) for name in CONTROLLER_FIELDS]]
            )
            if not np.isfinite(values).all():
                raise FloatingPointError("冻结模型返回非有限状态")
        except (ArithmeticError, ValueError) as error:
            logging.exception(
                "动力学失败 step=%d time_s=%g reference=%s state=%s",
                self._step_count,
                self._step_count * self.config.dt_s,
                reference,
                self.get_state().to_json(),
            )
            raise FloatingPointError("动力学数值异常；本步未提交") from error
        start_time_s = self._step_count * self.config.dt_s
        self._vehicle = vehicle
        self._eta, self._nu, self._actuator = eta, nu, actuator
        self._reference, self._last_command = reference, command.copy()
        self._step_count += 1
        return StepResult(self.get_state(), command.copy(), start_time_s)

    def advance(self, reference: ControlReference, n_substeps: int) -> RemusSnapshot:
        """显式重复 n 次 step；n 为非负整数，0 不推进。每一步独立提交。"""
        nonnegative_int(n_substeps, "n_substeps")
        if not isinstance(reference, ControlReference):
            raise ValueError("reference 类型错误")
        for _ in range(n_substeps):
            self.step(reference)
        return self.get_state()

    def sample_random(self, size: int) -> Vector:
        """显式消费本实例 RNG，返回 shape=(size,) 的无量纲 U[0,1)；不推进物理。"""
        nonnegative_int(size, "size")
        return self._rng.random(size)
