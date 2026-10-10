"""R1新增语义复核：固定独立解析输入、有限值类型与逐步统计分母。"""

import json
from typing import Any

from scripts.s1b_analytic import analytical_measurements
from scripts.strict_json import finite_tree, number


def check_analytic(record: dict[str, Any]) -> None:
    """重新计算固定构型手算expected与被测actual；兼容旧记录但不信任其自报值。"""
    expected = analytical_measurements()
    finite_tree(record)
    if len(record["distances"]) != len(expected["distances"]):
        raise ValueError("解析输入数量不符")
    for saved, fresh in zip(record["distances"], expected["distances"], strict=True):
        for key in ("actual", "expected", "abs_error_m"):
            number(saved[key], minimum=0 if key == "abs_error_m" else None)
        # 旧历史运行没有input/case_id，不回写旧文件；新运行在R1入口另行要求完整字段。
        if any(saved.get(k) != v for k, v in fresh.items() if k not in {"input", "case_id"}):
            raise ValueError("解析值不符合独立预登记输入复算")
        for key in ("input", "case_id"):
            if key in saved and json.dumps(saved[key], sort_keys=True) != json.dumps(
                fresh[key], sort_keys=True
            ):
                raise ValueError("解析输入/标识不符")
    for key in ("max_abs_error_m", "gradient_max_abs_error"):
        number(record[key], minimum=0, maximum=1e-6)
        if record[key] != expected[key]:
            raise ValueError("解析/梯度摘要与重新计算不符")
    if "gradients" in record and json.dumps(record["gradients"], sort_keys=True) != json.dumps(
        expected["gradients"], sort_keys=True
    ):
        raise ValueError("梯度点位/步长/解析/差分记录不符")


def check_numbers(metrics: dict[str, Any]) -> None:
    """检查所有汇总数量、误差、耗时与比率；bool不允许充当数值。"""
    finite_tree(metrics)
    for value in metrics["status_counts"].values():
        number(value, minimum=0, integer=True)
    number(metrics["uncertain_ratio"], minimum=0, maximum=1)
    number(metrics["mean_subdivisions"], minimum=0)
    number(metrics["neutral"]["steps"], minimum=1, integer=True)
    number(metrics["neutral"]["max_abs"], minimum=0)
    if type(metrics["neutral"]["rng_and_discrete_equal"]) is not bool:
        raise ValueError("中性RNG字段不是布尔值")
    for row in metrics["diagnostics"]:
        number(row["steps"], minimum=1, integer=True)
        for count in row["subdivisions"]:
            number(count, minimum=0, integer=True)
        if row["terminal_replay_equal"] is not True:
            raise ValueError("终止重放标志非法")
    for value in metrics["performance"].values():
        number(value["count"], minimum=1, integer=True)
        for key in ("p50_s", "p95_s", "max_s"):
            number(value[key], minimum=0)


def check_statistics(metrics: dict[str, Any], queries: list[dict[str, Any]]) -> None:
    """分母=六个反例+六条诊断的实际步；排除初态、中性对照与内部查询次数。"""
    counts = dict.fromkeys(("CLEAR", "COLLISION", "UNCERTAIN"), 0)
    subdivisions = 0
    for query in queries:
        if query["status"] not in counts:
            raise ValueError("查询状态非法")
        number(query["subdivisions"], minimum=0, integer=True)
        number(query["query_count"], minimum=1, integer=True)
        number(query["clearance_lower_bound_m"])
        counts[query["status"]] += 1
        subdivisions += query["subdivisions"]
    if not queries or (
        metrics["status_counts"] != counts
        or metrics["uncertain_ratio"] != counts["UNCERTAIN"] / len(queries)
        or metrics["mean_subdivisions"] != subdivisions / len(queries)
    ):
        raise ValueError("计数/比例/平均细分与原始步分母不符")
