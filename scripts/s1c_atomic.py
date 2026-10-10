"""真实L1故障注入原始证据，完整前后状态及重试对照。"""

import json
from pathlib import Path
from unittest.mock import patch

from auv_env.currents import CurrentField
from auv_env.l1.adapter import L1Adapter
from scripts.strict_json import write_json


def execute(output: Path) -> None:
    """四个失败点×三类异常，候选日志由实际适配器写堆栈。"""
    output.mkdir(parents=True, exist_ok=False)
    records = []
    for target in (
        "auv_env.currents.CurrentField.sample",
        "auv_env.l1.rhs.evaluate",
        "auv_env.l1.adapter.advance_pose",
        "auv_env.l1.state.L1Snapshot.to_json",
    ):
        for error in (ValueError, FloatingPointError, RuntimeError):
            env = L1Adapter(CurrentField("uniform", background=[0, 0.3, 0]))
            env.sample_random(9)
            control, branch = env.clone(), env.clone()
            before = env.get_state().to_json()
            caught = False
            with patch(target, side_effect=error("S1C故障注入")):
                try:
                    env.step()
                except error:
                    caught = True
            after = env.get_state().to_json()
            branch_state = branch.get_state().to_json()
            retry = env.step().state.to_json()
            expected = control.step().state.to_json()
            if not caught or before != after or before != branch_state or retry != expected:
                raise ValueError("异常原子性/重试失败")
            records.append(
                dict(
                    target=target,
                    error=error.__name__,
                    before=json.loads(before),
                    after_exception=json.loads(after),
                    branch=json.loads(branch_state),
                    retry=json.loads(retry),
                    control=json.loads(expected),
                )
            )
    write_json(output / "results.json", {"cases": records})
