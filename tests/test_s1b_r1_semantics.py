"""R2单位层复算与严格数值测试；完整21类真实CLI注入另由R1入口逐项执行。"""

import copy

import pytest

from scripts.s1b_analytic import analytical_measurements
from scripts.s1b_semantics import check_analytic, check_statistics
from scripts.strict_json import loads, number


@pytest.mark.parametrize("value", [True, False, "1", float("nan"), float("inf"), -1.0])
def test_r1_nonnegative_number(value: object) -> None:
    """计数/误差不接受bool、字符串、非有限或负值。"""
    with pytest.raises(ValueError):
        number(value, minimum=0)


@pytest.mark.parametrize("text", ['{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', '{"x":1,"x":2}'])
def test_r1_json_rejection(text: str) -> None:
    """非标准JSON和重复键均不容忍。"""
    with pytest.raises(ValueError):
        loads(text)


@pytest.mark.parametrize("fault", ["paired", "gradient", "input", "point", "difference"])
def test_r1_analytic_independent(fault: str) -> None:
    """两列错误同值仍失败，输入、差分记录不能冒充独立真值。"""
    values = analytical_measurements()
    check_analytic(values)
    if fault == "paired":
        values["distances"][0].update(expected=123.0, actual=123.0, abs_error_m=0.0)
    elif fault == "gradient":
        values["gradient_max_abs_error"] = -1.0
    elif fault == "input":
        values["distances"][0]["input"]["depth_m"] = 100
    elif fault == "point":
        values["gradients"][0]["point_m"][0] = 10
    else:
        values["gradients"][0]["finite_difference"][0] = 10
    with pytest.raises(ValueError):
        check_analytic(values)


@pytest.mark.parametrize("field", ["status_counts", "uncertain_ratio", "mean_subdivisions"])
def test_r1_denominator(field: str) -> None:
    """只按实际外部查询条目计分母，内部query_count不应加进来。"""
    rows = [
        {"status": "CLEAR", "subdivisions": 0, "query_count": 900, "clearance_lower_bound_m": 10},
        {
            "status": "UNCERTAIN",
            "subdivisions": 4,
            "query_count": 700,
            "clearance_lower_bound_m": -1,
        },
    ]
    metrics = {
        "status_counts": {"CLEAR": 1, "COLLISION": 0, "UNCERTAIN": 1},
        "uncertain_ratio": 0.5,
        "mean_subdivisions": 2.0,
    }
    check_statistics(metrics, rows)
    wrong = copy.deepcopy(metrics)
    wrong[field] = (
        {"CLEAR": 900, "COLLISION": 0, "UNCERTAIN": 700} if field == "status_counts" else 0
    )
    with pytest.raises(ValueError):
        check_statistics(wrong, rows)
