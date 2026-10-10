"""S0 独立来源、CSV、依赖和证据检查。坐标采用 NED，深度正向下。"""

import csv
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from scripts.import_s0 import SOURCE_SHA, SOURCE_URL, UPSTREAM_SHA, destination, source_objects

Json = dict[str, Any]
CSV_COLUMNS = (
    "time_s,north_m,east_m,depth_m,roll_rad,pitch_rad,yaw_rad,u_mps,v_mps,w_mps,"
    "p_radps,q_radps,r_radps,rudder_command_rad,stern_command_rad,rpm_command,"
    "rudder_actual_rad,stern_actual_rad,rpm_actual"
).split(",")


def read_json(path: Path) -> Json:
    """读取对象型 JSON；文件缺失或结构非法时抛异常，无写副作用。"""
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须为对象: {path.name}")
    return value


def digest(path: Path) -> str:
    """计算原始文件字节的 SHA-256；不进行换行转换。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_source(root: Path, source: Path) -> Json:
    """对照固定提交 Git 树逐项核对清单、blob 和工作副本；漂移抛异常。"""
    manifest = read_json(root / "docs/provenance/source_manifest.json")
    if (manifest["source_commit"], manifest["source_url"], manifest["upstream_commit"]) != (
        SOURCE_SHA,
        SOURCE_URL,
        UPSTREAM_SHA,
    ):
        raise ValueError("来源身份不符")
    objects = source_objects(source)
    expected = {name: destination(name) for name in objects if destination(name) is not None}
    entries = manifest["files"]
    if len(entries) != len(expected) or {e["source_path"] for e in entries} != set(expected):
        raise ValueError("清单遗漏或重复来源文件")
    for entry in entries:
        name = entry["source_path"]
        if entry["target_path"] != expected[name]:
            raise ValueError("任务卡路径映射不符")
        data = (root / entry["target_path"]).read_bytes()
        blob = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
        if (
            blob != objects[name]
            or blob != entry["source_blob_sha"]
            or hashlib.sha256(data).hexdigest() != entry["sha256"]
            or len(data) != entry["size_bytes"]
        ):
            raise ValueError(f"来源内容漂移: {name}")
    # .gitignore 允许逐项合并，不能要求整个目标文件与来源 blob 相等。
    merge = manifest["gitignore_merge"]
    rules = "\n".join(merge["source_rules"]) + "\n"
    blob = hashlib.sha1(f"blob {len(rules.encode())}\0".encode() + rules.encode()).hexdigest()
    if blob != objects[".gitignore"] or merge["source_blob_sha"] != blob:
        raise ValueError("来源忽略规则漂移")
    if not set(merge["source_rules"]).issubset((root / ".gitignore").read_text().splitlines()):
        raise ValueError("遗漏来源忽略规则")
    return {"files_verified": len(entries), "source_commit": SOURCE_SHA}


def validate_config(config: Json) -> None:
    """校验固定 S0 门槛，禁止用配置更改将冒烟或宽松阈值当作完整回归。"""
    expected = {
        "duration_s": 200.0,
        "dt_s": 0.02,
        "samples": 10001,
        "parameters": {
            "duration": 200,
            "depth": 30,
            "heading": 50,
            "rpm": 1525,
            "current": 0.5,
            "current_direction": 170,
            "no_animation": True,
        },
        "reference": {
            "final_depth_m": 29.74975,
            "final_heading_deg": 49.99890,
            "path_length_m": 453.55089,
        },
        "tolerance": {"final_depth_m": 0.001, "final_heading_deg": 0.001, "path_length_m": 0.01},
        "time_tolerance_s": 1e-9,
        "summary_tolerance": 1e-8,
        "expected_upstream_tests": 4,
        "seed": None,
    }
    if any(config.get(key) != value for key, value in expected.items()):
        raise ValueError("配置不符合 S0 固定任务卡")
    if not 0 < config["timeout_s"] <= 1800:
        raise ValueError("超时必须在 (0,1800] 秒内")


def angle_distance_deg(first: float, second: float) -> float:
    """返回角度最小绝对差（度），兼容跨越 ±180 度边界。"""
    return abs((first - second + 180) % 360 - 180)


def check_results(folder: Path, config: Json) -> Json:
    """从 10001×19 CSV 独立复算米/度指标，校验时间、summary 与门槛。"""
    validate_config(config)
    with (folder / "simulation.csv").open(encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        if next(reader) != CSV_COLUMNS:
            raise ValueError("CSV 列名或顺序不符")
        rows = [[float(value) for value in row] for row in reader]
    if len(rows) != config["samples"] or any(len(row) != 19 for row in rows):
        raise ValueError("CSV 记录数或列数不符")
    if not all(math.isfinite(value) for row in rows for value in row):
        raise ValueError("CSV 包含 NaN/Inf")
    if any(
        abs(row[0] - index * config["dt_s"]) > config["time_tolerance_s"]
        for index, row in enumerate(rows)
    ):
        raise ValueError("时间轴错位")
    # 直接对 NED 位置差求欧氏距离，不使用速度积分或 summary 的路径长度。
    actual = {
        "final_depth_m": rows[-1][3],
        "final_heading_deg": math.degrees(rows[-1][6]),
        "path_length_m": math.fsum(
            math.dist(a[1:4], b[1:4]) for a, b in zip(rows, rows[1:], strict=False)
        ),
    }
    summary = read_json(folder / "summary.json")
    if (
        summary["samples"] != len(rows)
        or summary["all_states_finite"] is not True
        or summary["parameters"] != config["parameters"]
    ):
        raise ValueError("summary 结构、参数或记录数不符")
    for key, expected in {
        "simulation_duration_s": rows[-1][0],
        "sample_time_s": config["dt_s"],
    }.items():
        value = float(summary[key])
        if not math.isfinite(value) or abs(value - expected) > config["time_tolerance_s"]:
            raise ValueError(f"summary 时间不符: {key}")
    differences = {}
    for key, value in actual.items():
        summary_value = float(summary[key])
        distance = angle_distance_deg if key == "final_heading_deg" else lambda a, b: abs(a - b)
        if (
            not math.isfinite(summary_value)
            or distance(value, summary_value) > config["summary_tolerance"]
        ):
            raise ValueError(f"CSV 与 summary 不一致: {key}")
        differences[key] = distance(value, config["reference"][key])
        if differences[key] > config["tolerance"][key]:
            raise ValueError(f"回归超出门槛: {key}={value}")
    return {
        "actual": actual,
        "difference": differences,
        "samples": len(rows),
        "duration_s": rows[-1][0],
        "all_states_finite": True,
    }


def check_junit(path: Path, expected_count: int | None = None) -> Json:
    """按 JUnit 实际用例验证数量与失败/跳过，禁止只搜索 passed 文本。"""
    cases = list(ElementTree.parse(path).iter("testcase"))
    if not cases or (expected_count is not None and len(cases) != expected_count):
        raise ValueError(f"测试数量不符: {len(cases)}")
    if any(list(case) for case in cases):
        raise ValueError("JUnit 包含失败、错误、跳过或非预期输出")
    return {"tests": len(cases)}


def check_lock(root: Path) -> Json:
    """核对当前环境已安装的基线及工具版本；不隐式安装或更新。"""
    versions = {}
    for name in ("requirements-lock.txt", "requirements-dev.txt"):
        for line in (root / name).read_text(encoding="utf-8").splitlines():
            if "==" in line and not line.startswith("#"):
                package, expected = line.split("==")
                actual = importlib.metadata.version(package)
                if actual != expected:
                    raise ValueError(f"锁定版本不符: {package}={actual}, 期望 {expected}")
                versions[package] = actual
    return versions


def fingerprint(root: Path) -> Json:
    """记录全部被测第一方代码、冻结源码、测试、配置及 CI 的内容指纹。"""
    paths: set[Path] = set()
    for directory in ("scripts", "tests", "configs", ".github"):
        paths.update(
            p for p in (root / directory).rglob("*") if p.is_file() and "__pycache__" not in p.parts
        )
    for entry in read_json(root / "docs/provenance/source_manifest.json")["files"]:
        paths.add(root / entry["target_path"])
    paths.update(
        root / name
        for name in (
            "pyproject.toml",
            "requirements-dev.txt",
            "docs/provenance/source_manifest.json",
            "docs/schemas/gate_report.schema.json",
        )
    )
    files = {p.relative_to(root).as_posix(): digest(p) for p in sorted(paths)}
    return {
        "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "files": files,
    }
