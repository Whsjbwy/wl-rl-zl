"""S1-A 真实逐步对照、100组恢复及分支隔离，原始数据只写唯一运行目录。"""

import argparse
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from auv_env import ControlReference, RemusAdapter, RemusConfig, RemusSnapshot
from auv_env.state import CONTROLLER_FIELDS, Vector
from scripts.s0_checks import CSV_COLUMNS, read_json
from scripts.s1a_reference import scheduled_reference, upstream_trajectory


def adapter_config(config: dict[str, Any], case: dict[str, Any]) -> RemusConfig:
    """由已登记场景创建固定流适配配置，角度只在边界转换。"""
    return RemusConfig(
        dt_s=config["dt_s"],
        current_mps=case["current_mps"],
        current_direction_rad=math.radians(case["current_direction_deg"]),
        seed=config["seed"],
    )


def reference_at(step: int, config: dict[str, Any], varying: bool = True) -> ControlReference:
    """返回当前物理步参考，航向单位由任务配置 deg 转为公共接口 rad。"""
    value = scheduled_reference(step, config["reference_schedule"], varying)
    return ControlReference(
        value["depth_m"], math.radians(value["heading_deg"]), value["propeller_rpm"]
    )


def snapshot_values(state: RemusSnapshot) -> Vector:
    """返回用于逐字段误差的 29 通道向量；单位为 s、m、rad、m/s、rad/s、rpm。"""
    return np.concatenate(
        (
            [state.time_s],
            state.eta,
            state.nu,
            state.actuator,
            [state.controller[name] for name in CONTROLLER_FIELDS],
            state.last_command,
            [state.reference.depth_m, state.reference.heading_rad, state.reference.propeller_rpm],
        )
    )


def compare_snapshots(first: RemusSnapshot, second: RemusSnapshot, atol: float) -> float:
    """完整浮点字段按绝对差，计数/模型/配置/RNG 精确比较；不一致抛异常。"""
    left, right = snapshot_values(first), snapshot_values(second)
    if not np.isfinite(left).all() or not np.isfinite(right).all():
        raise ValueError("快照序列出现非有限值")
    error = float(np.max(np.abs(left - right)))
    if error > atol:
        raise ValueError(f"快照误差超阈值 {error}")
    if (
        first.step_count,
        first.version,
        first.model_id,
        first.config_hash,
        first.config,
        first.rng_state,
    ) != (
        second.step_count,
        second.version,
        second.model_id,
        second.config_hash,
        second.config,
        second.rng_state,
    ):
        raise ValueError("快照离散字段或完整 RNG 不一致")
    return error


def trajectory_errors(expected: Vector, actual: Vector, config: dict[str, Any]) -> dict[str, Any]:
    """比较 (N+1,19) 全轨迹；角度以未包裹弧度原值比较，不掩盖整周错误。"""
    shape = (config["n_steps"] + 1, 19)
    if expected.shape != shape or actual.shape != shape:
        raise ValueError("轨迹形状不符合预登记步数")
    if not np.isfinite(expected).all() or not np.isfinite(actual).all():
        raise ValueError("轨迹包含 NaN/Inf")
    timeline = np.arange(shape[0]) * config["dt_s"]
    if (
        np.max(np.abs(actual[:, 0] - timeline)) > config["time_atol_s"]
        or np.max(np.abs(expected[:, 0] - timeline)) > config["time_atol_s"]
    ):
        raise ValueError("轨迹时间错位")
    difference = np.abs(actual - expected)
    stats = {
        name: {
            "max_abs": float(difference[:, index].max()),
            "rms": float(np.sqrt(np.mean(difference[:, index] ** 2))),
        }
        for index, name in enumerate(CSV_COLUMNS)
    }
    if not np.allclose(actual[:, 1:], expected[:, 1:], atol=config["atol"], rtol=config["rtol"]):
        raise ValueError(f"逐元素轨迹误差超出门槛；最大差={difference.max()}")
    return {
        "channels": stats,
        "max_abs": float(difference.max()),
        "samples": shape[0],
        "duration_s": float(actual[-1, 0]),
    }


def regressions(config: dict[str, Any], output: Path) -> list[dict[str, Any]]:
    """执行五种 200 秒工况；真实主实例只走 N 步，终端指令从克隆预览。"""
    results = []
    for case in config["cases"]:
        logging.info("逐步回归 %s", case["name"])
        folder = output / case["name"]
        folder.mkdir()
        expected = upstream_trajectory(config, case)
        adapter = RemusAdapter(adapter_config(config, case))
        rows = np.empty_like(expected)
        for index in range(config["n_steps"]):
            before = adapter.get_state()
            result = adapter.step(reference_at(index, config, case["vary_reference"]))
            rows[index] = np.concatenate(
                ([before.time_s], before.eta, before.nu, result.command, before.actuator)
            )
        final = adapter.get_state()
        preview = adapter.clone().step(
            reference_at(config["n_steps"], config, case["vary_reference"])
        )
        rows[-1] = np.concatenate(
            ([final.time_s], final.eta, final.nu, preview.command, final.actuator)
        )
        if adapter.get_state().to_json() != final.to_json():
            raise ValueError("末行预览污染主环境")
        if final.step_count != config["n_steps"] or final.time_s != config["duration_s"]:
            raise ValueError("步边界或终点时间不符")
        for name, data in (("upstream", expected), ("adapter", rows)):
            np.savetxt(
                folder / f"{name}.csv",
                data,
                delimiter=",",
                header=",".join(CSV_COLUMNS),
                comments="",
            )
        metrics = trajectory_errors(expected, rows, config)
        results.append({"case": case["name"], "status": "PASS", **metrics})
    return results


def replay_sequence(
    adapter: RemusAdapter, config: dict[str, Any], count: int
) -> list[RemusSnapshot]:
    """每步显式消费 RNG 后推进；随机数仅验证状态恢复，不施加随机动力学。"""
    states = []
    for _ in range(count):
        adapter.sample_random(3)
        states.append(adapter.step(reference_at(adapter.get_state().step_count, config)).state)
    return states


def check_branches(adapter: RemusAdapter, config: dict[str, Any], evidence: Path) -> float:
    """验证 A/B/主实例、外部快照、只读保存相互隔离，再与未分支对照推进。"""
    original = adapter.get_state().to_json()
    control, branch_a, branch_b = adapter.clone(), adapter.clone(), adapter.clone()
    branch_a.sample_random(9)
    branch_a.advance(ControlReference(15, -0.3, 1100), 10)
    records = {
        "main_before": original,
        "main_after_a": adapter.get_state().to_json(),
        "b_after_a": branch_b.get_state().to_json(),
    }
    if adapter.get_state().to_json() != original or branch_b.get_state().to_json() != original:
        raise ValueError("运行 A 污染主环境或 B")
    after_a = branch_a.get_state().to_json()
    branch_b.advance(ControlReference(40, 1.2, 1400), 10)
    records.update(
        a_before_b=after_a,
        a_after_b=branch_a.get_state().to_json(),
        main_after_b=adapter.get_state().to_json(),
    )
    if adapter.get_state().to_json() != original or branch_a.get_state().to_json() != after_a:
        raise ValueError("运行 B 污染主环境或 A")
    external = adapter.get_state()
    external.eta[:] = 123
    external.nu[:] = 123
    external.actuator[:] = 123
    external.controller["z_int"] = 123
    external.last_command[:] = 123
    external.rng_state["state"]["state"] = 123
    for _ in range(3):
        adapter.get_state().to_json()
    if adapter.get_state().to_json() != original:
        raise ValueError("外部快照/读取污染主环境")
    records["main_after_reads"] = adapter.get_state().to_json()
    first, second = replay_sequence(adapter, config, 10), replay_sequence(control, config, 10)
    records["continued_main"] = "\n".join(state.to_json() for state in first)
    records["continued_control"] = "\n".join(state.to_json() for state in second)
    evidence.write_text(json.dumps(records), encoding="utf-8")
    return max(
        compare_snapshots(a, b, config["replay_atol"]) for a, b in zip(first, second, strict=True)
    )


def snapshots(config: dict[str, Any], output: Path) -> list[dict[str, Any]]:
    """不同流、时间、参考下执行100组，每组100步内存及 JSON 重放与双分支隔离。"""
    results = []
    folder = output / "snapshots"
    folder.mkdir()
    for index in range(config["snapshot_groups"]):
        case = config["cases"][index % len(config["cases"])]
        adapter = RemusAdapter(adapter_config(config, case))
        # 分散到三个参考区间，避免100组都只覆盖零初态或同一控制参考。
        warmup_steps = 30 + index * 7
        initial_ref = reference_at((index % 3) * 3000, config)
        adapter.advance(initial_ref, warmup_steps)
        # 参考变化紧随快照发生，重放必须保留原控制器积累。
        adapter.sample_random(index + 1)
        saved = adapter.get_state()
        if not any(saved.controller.values()) or saved.step_count == 0:
            raise ValueError("恢复实验未覆盖非零控制器历史")
        expected = replay_sequence(adapter, config, config["replay_steps"])
        adapter.set_state(saved)
        restored = replay_sequence(adapter, config, config["replay_steps"])
        serialized = RemusAdapter(adapter.config)
        snapshot_path = folder / f"{index:03d}.snapshot.json"
        snapshot_path.write_text(saved.to_json(), encoding="utf-8")
        serialized.set_state(RemusSnapshot.from_json(snapshot_path.read_text(encoding="utf-8")))
        roundtrip = replay_sequence(serialized, config, config["replay_steps"])
        replay_error = max(
            compare_snapshots(a, b, config["replay_atol"])
            for a, b in zip(expected, restored, strict=True)
        )
        json_error = max(
            compare_snapshots(a, b, config["replay_atol"])
            for a, b in zip(expected, roundtrip, strict=True)
        )
        # 原始完整状态序列与 RNG 字典留在本地，不把轨迹压成仅末值。
        for name, states in (("original", expected), ("restored", restored), ("json", roundtrip)):
            (folder / f"{index:03d}.{name}.jsonl").write_text(
                "\n".join(state.to_json() for state in states) + "\n", encoding="utf-8"
            )
        branch_error = check_branches(adapter, config, folder / f"{index:03d}.branches.json")
        results.append(
            {
                "group": index,
                "case": case["name"],
                "snapshot_step": saved.step_count,
                "steps": config["replay_steps"],
                "replay_max_abs": replay_error,
                "json_max_abs": json_error,
                "branch_max_abs": branch_error,
                "rng_and_identifiers_equal": True,
                "status": "PASS",
            }
        )
        if index % 20 == 0:
            logging.info("快照/分支已完成 %d/100", index + 1)
    return results


def resets(config: dict[str, Any], output: Path) -> list[dict[str, Any]]:
    """变化参考运行并消费 RNG 后 reset，与新实例比较完整100步。"""
    results = []
    for case in config["cases"]:
        adapter = RemusAdapter(adapter_config(config, case))
        adapter.advance(ControlReference(10, -0.6, 1000), 60)
        adapter.advance(ControlReference(40, 1.3, 1400), 60)
        adapter.sample_random(21)
        adapter.reset()
        fresh = RemusAdapter(adapter.config)
        compare_snapshots(adapter.get_state(), fresh.get_state(), 0)
        reset_states = replay_sequence(adapter, config, 100)
        fresh_states = replay_sequence(fresh, config, 100)
        for name, states in (("reset", reset_states), ("fresh", fresh_states)):
            (output / f"{case['name']}.{name}.jsonl").write_text(
                "\n".join(state.to_json() for state in states) + "\n", encoding="utf-8"
            )
        error = max(
            compare_snapshots(a, b, config["replay_atol"])
            for a, b in zip(reset_states, fresh_states, strict=True)
        )
        results.append({"case": case["name"], "steps": 100, "max_abs": error, "status": "PASS"})
    return results


def main() -> int:
    """生成真实实验指标和完整原始数据；异常保留堆栈并返回非零。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args.output.mkdir(exist_ok=False)
    config = read_json(Path(__file__).resolve().parents[1] / "configs/s1a.json")
    result = {
        "regressions": regressions(config, args.output),
        "snapshots": snapshots(config, args.output),
        "resets": resets(config, args.output),
    }
    (args.output / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    logging.info("原始实验完成，验收器将独立复核文件与指标")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
