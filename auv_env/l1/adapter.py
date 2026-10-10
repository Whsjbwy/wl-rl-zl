"""L1候选控制/物理/执行器/序列化验证后统一提交；不向L0监控器塞新状态。"""

import copy
import logging
import math
from dataclasses import asdict, dataclass

import numpy as np
from python_vehicle_simulator.vehicles import remus100

from auv_env.config import ControlReference, finite_scalar, nonnegative_int
from auv_env.current_kinematics import advance_pose
from auv_env.currents import CurrentField
from auv_env.l1 import actuators, rhs
from auv_env.l1.parameters import MODEL_ID, export_parameters, parameter_hash
from auv_env.l1.state import L1Snapshot
from auv_env.state import CONTROLLER_FIELDS, Vector, vector


@dataclass(slots=True)
class L1StepResult:
    """独立步后快照及当前步项级诊断；矩阵单位与rhs.evaluate一致。"""

    state: L1Snapshot
    diagnostics: dict[str, Vector]


class L1Adapter:
    """独立开阔水域模型。dt秒；field纯场；seed仅供完整RNG恢复验证。

    非法输入抛ValueError；数值与外部查询异常原样传播，候选不提交。
    初态同步深度/航向参考滤波值，仅为合法初始化，不宣称配平。
    """

    def __init__(
        self, field: CurrentField | None = None, dt_s: float = 0.02, seed: int = 20261011
    ) -> None:
        self._dt = finite_scalar(dt_s, "dt_s")
        if self._dt <= 0:
            raise ValueError("dt必须为正")
        self._seed = nonnegative_int(seed, "seed")
        selected_field = CurrentField() if field is None else field
        if not isinstance(selected_field, CurrentField):
            raise ValueError("field必须为CurrentField")
        self._field = CurrentField.from_dict(selected_field.to_dict())
        self._parameters = export_parameters()
        self.reset()

    def reset(
        self,
        *,
        eta: object = (0, 0, 30, 0, 0, 0),
        nu: object = (1, 0, 0, 0, 0, 0),
        actuator: object = (0, 0, 900),
        reference: ControlReference | None = None,
    ) -> L1Snapshot:
        """重建完整状态和RNG；输入(6,)/(6,)/(3,)，非法时旧状态不变。"""
        pose = vector(eta, 6, "eta")
        if reference is not None and not isinstance(reference, ControlReference):
            raise ValueError("参考必须为ControlReference")
        ref = (
            ControlReference(float(pose[2]), float(pose[5]), 900)
            if reference is None
            else reference
        )
        control = {name: 0.0 for name in CONTROLLER_FIELDS}
        control.update(z_d=ref.depth_m, psi_d=ref.heading_rad)
        state = L1Snapshot(
            1,
            MODEL_ID,
            parameter_hash(),
            self._field.to_dict(),
            self._dt,
            0,
            0.0,
            pose,
            vector(nu, 6, "nu"),
            actuators.actual_state(actuator),
            control,
            ref,
            np.zeros(3),
            np.zeros(3),
            dict(np.random.PCG64(self._seed).state),
        )
        self.set_state(state)
        return self.get_state()

    def get_state(self) -> L1Snapshot:
        """无推进、无RNG消费的完整深副本。"""
        return copy.deepcopy(self._state)

    def set_state(self, snapshot: L1Snapshot) -> None:
        """仅接受同模型/场/参数/dt快照；全部验证及序列化成功后原子替换。"""
        if not isinstance(snapshot, L1Snapshot):
            raise ValueError("需要L1Snapshot")
        candidate = snapshot.validated_copy()
        if candidate.dt_s != self._dt or candidate.field != self._field.to_dict():
            raise ValueError("不能跨步长或海流场恢复")
        candidate.to_json()
        self._state = candidate

    def clone(self) -> "L1Adapter":
        """完整独立副本；构造/复制不推进原实例或消费其RNG。"""
        result = L1Adapter(self._field, self._dt, self._seed)
        result.set_state(self.get_state())
        return result

    def sample_random(self, size: int) -> Vector:
        """显式消费独立PCG64；仅此方法消费RNG，不推进物理时间。"""
        nonnegative_int(size, "size")
        generator = np.random.Generator(np.random.PCG64())
        generator.bit_generator.state = copy.deepcopy(self._state.rng_state)
        result = generator.random(size)
        self._state.rng_state = copy.deepcopy(dict(generator.bit_generator.state))
        return result

    def step(
        self, reference: ControlReference | None = None, *, raw_command: object | None = None
    ) -> L1StepResult:
        """闭环参考或开环(3,)rad/rad/rpm命令，二者互斥；推进一个dt。

        闭环复用冻结控制器增益及七项记忆。当前真实执行器计算载荷，
        新速度更新姿态，执行器ZOH更新；完整候选序列化后统一提交。
        异常记录候选及堆栈，原实例不变；不伪造终止/碰撞或吞掉异常。
        """
        if reference is not None and raw_command is not None:
            raise ValueError("参考与开环命令不能同时指定")
        if reference is not None and not isinstance(reference, ControlReference):
            raise ValueError("参考必须为ControlReference")
        candidate = self.get_state()
        try:
            ref = (
                candidate.reference if reference is None else ControlReference(**asdict(reference))
            )
            if raw_command is None:
                controller = remus100(
                    "depthHeadingAutopilot",
                    ref.depth_m,
                    math.degrees(ref.heading_rad),
                    ref.propeller_rpm,
                    0,
                    0,
                )
                for name, value in candidate.controller.items():
                    setattr(controller, name, value)
                raw = controller.depthHeadingAutopilot(candidate.eta, candidate.nu, self._dt)
                candidate.controller = {n: float(getattr(controller, n)) for n in CONTROLLER_FIELDS}
            else:
                raw = vector(raw_command, 3, "raw_command")
            clipped, next_actuator = actuators.update(candidate.actuator, raw, self._dt)
            with np.errstate(over="raise", invalid="raise", divide="raise"):
                sample = self._field.sample(candidate.eta[:3], candidate.time_s)
                if (
                    sample.config_hash != self._field.config_hash
                    or sample.version != 1
                    or type(sample.version) is not int
                ):
                    raise ValueError("场查询标识不匹配")
                diagnostics = rhs.evaluate(
                    candidate.eta, candidate.nu, candidate.actuator, sample, self._parameters
                )
                candidate.nu = candidate.nu + self._dt * diagnostics["nu_dot"]
                candidate.eta = advance_pose(candidate.eta, candidate.nu, self._dt)
                candidate.actuator = next_actuator
                candidate.raw_command, candidate.clipped_command = raw.copy(), clipped
                candidate.reference = ref
                candidate.step_count += 1
                candidate.time_s = candidate.step_count * self._dt
            candidate.to_json()
            result = L1StepResult(copy.deepcopy(candidate), copy.deepcopy(diagnostics))
        except Exception:
            logging.exception("L1候选失败；主状态未提交；候选=%r", candidate)
            raise
        self._state = candidate
        return result
