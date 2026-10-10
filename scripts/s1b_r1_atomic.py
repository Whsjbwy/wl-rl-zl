"""真实REMUS异常事务复现实验，保存前/候选异常日志/恢复后完整快照。"""

import argparse
import logging
from pathlib import Path
from unittest.mock import patch

from auv_env import ControlReference, RemusAdapter, RemusConfig
from auv_env.scene import Scene
from auv_env.terrain import Terrain
from auv_env.world_monitor import MonitorSnapshot, WorldMonitor
from scripts.strict_json import loads, write_json


def execute(output: Path) -> None:
    """三类真实故障解除后只推进一次；完整字段、分支与JSON恢复均精确比较。"""
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for error in (ValueError, FloatingPointError, RuntimeError):
        monitor = WorldMonitor(
            RemusAdapter(RemusConfig(current_mps=0)),
            Scene(
                "r1-atomic",
                "r1-atomic",
                Terrain(1000),
                lower=(-1000, -1000, -1000),
                upper=(1000, 1000, 1000),
            ),
        )
        monitor.step(ControlReference(20, 0.1, 1000))
        control, other = monitor.clone(), monitor.clone()
        before = monitor.get_state().to_json()
        with patch("auv_env.world_monitor.query_sweep", side_effect=error("r1_actual_fault")):
            try:
                monitor.step(ControlReference())
            except error:
                pass
            else:
                raise ValueError("异常被吞掉")
        after = monitor.get_state().to_json()
        recovered = monitor.clone()
        recovered.set_state(MonitorSnapshot.from_json(before))
        retry = monitor.step(ControlReference())[0].to_json()
        expected = control.step(ControlReference())[0].to_json()
        restored = recovered.step(ControlReference())[0].to_json()
        row = {
            "error": error.__name__,
            "before": loads(before),
            "after_exception": loads(after),
            "after_retry": loads(retry),
            "control": loads(expected),
            "restored": loads(restored),
            "other_branch": loads(other.get_state().to_json()),
            "unchanged": before == after,
            "retry_equal": retry == expected == restored,
            "branch_unchanged": other.get_state().to_json() == before,
        }
        write_json(output / (error.__name__ + ".json"), row)
        if not all(row[k] for k in ("unchanged", "retry_equal", "branch_unchanged")):
            raise ValueError("监控原子性不成立")
        rows.append({k: row[k] for k in ("error", "unchanged", "retry_equal", "branch_unchanged")})
    write_json(output / "results.json", {"cases": rows, "status": "PASS"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    execute(args.output)
