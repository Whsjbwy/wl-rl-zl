"""验收用严格JSON与数值检查；NaN、Infinity、重复键和bool计数不能进入PASS。"""

import json
import math
from pathlib import Path
from typing import Any


def number(
    value: Any, *, minimum: float | None = None, maximum: float | None = None, integer: bool = False
) -> float:
    """有限JSON数值，范围闭区间；类型和范围不合法抛ValueError。"""
    if type(value) not in (int, float) or (integer and type(value) is not int):
        raise ValueError("必需数值类型非法，不能使用bool/字符串")
    if (
        not math.isfinite(value)
        or (minimum is not None and value < minimum)
        or (maximum is not None and value > maximum)
    ):
        raise ValueError("必需数值非有限或范围非法")
    return float(value)


def finite_tree(value: Any) -> None:
    """递归拒绝非有限数；bool字段保留，由对应字段检查区分计数。"""
    if isinstance(value, float):
        number(value)
    elif isinstance(value, dict):
        for item in value.values():
            finite_tree(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            finite_tree(item)


def loads(text: str) -> Any:
    """拒绝非标准常量、溢出指数及重复键，兼容透明嵌套快照。"""

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON重复键")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError(f"JSON非有限常量:{value}")

    result = json.loads(text, parse_constant=constant, object_pairs_hook=pairs)
    finite_tree(result)
    return result


def read_json(path: Path) -> dict[str, Any]:
    """只读对象型JSON；不容忍空文件或非标准数值。"""
    value = loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON顶层必须是对象")
    return value


def write_json(path: Path, value: Any) -> None:
    """只允许标准有限JSON输出；写之前检查，非法值不生成伪有效记录。"""
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
