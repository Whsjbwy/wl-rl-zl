"""标准库只读审核ZIP核验；不导入或执行包内代码，先安全检查再可选解压。"""

import argparse
import csv
import hashlib
import io
import json
import math
import re
import stat
import struct
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any
from xml.etree import ElementTree

REQUIRED = {
    "README_先读.md",
    "review_index.json",
    "missing_files.json",
    "git/revision.json",
    "git/tracked_source_manifest.json",
    "git/remote_receipt.json",
    "git/diff_from_reviewed_head.patch",
    "git/changed_files.txt",
    "git/diff_stat.txt",
    "tools/verify_bundle.py",
    "packager/privacy_audit.json",
    "source/AGENTS.md",
    "source/auv_env/world_monitor.py",
    "source/scripts/build_review_bundle.py",
    "source/scripts/verify_s1b_r1.py",
    "reports/S1B_R1_gate_report.json",
    "reports/S1B_R1_验收报告.md",
    "reports/S1B_R1_实施记录.md",
    "reports/S1B_R1_问题闭环表.md",
    "reports/fingerprint_reconciliation.json",
}


def safe_path(name: str) -> str:
    """拒绝绝对/盘符/反斜线/点段/空段/ADS；按Windows大小写不敏感核对重复。"""
    if not name or "\\" in name or ":" in name or name.startswith("/"):
        raise ValueError("ZIP路径非法")
    parts = name.split("/")
    if any(p in {"", ".", ".."} or p.endswith((".", " ")) for p in parts):
        raise ValueError("ZIP路径越界或不规范")
    if any(p.lower() in {".git", ".venv", "__pycache__", ".env"} for p in parts):
        raise ValueError("禁止打包的私有/缓存路径")
    return PurePosixPath(name).as_posix()


def json_bytes(data: bytes) -> Any:
    """严格常量及有限JSON；不执行对象钩子。"""

    def bad(value: str) -> None:
        raise ValueError(f"JSON非标准常量:{value}")

    result = json.loads(data, parse_constant=bad)

    def walk(value: Any) -> None:
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("JSON非有限值")
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        if isinstance(value, list):
            for item in value:
                walk(item)

    walk(result)
    return result


def readable(name: str, data: bytes) -> None:
    """读取JSON/JSONL/CSV/JUnit/PNG；故障输入以.txt封装，不冒充正常结果文件。"""
    suffix = Path(name).suffix.lower()
    if suffix == ".json":
        json_bytes(data)
    elif suffix == ".jsonl":
        for line in data.splitlines():
            json_bytes(line)
    elif suffix == ".xml":
        ElementTree.fromstring(data)
    elif suffix == ".csv":
        rows = csv.reader(io.StringIO(data.decode("utf-8-sig")))
        header = next(rows)
        for row in rows:
            if len(row) != len(header) or not all(math.isfinite(float(v)) for v in row):
                raise ValueError("CSV形状/有限值非法")
    elif suffix == ".png":
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            raise ValueError("PNG头非法")
        offset, payload, ended = 8, bytearray(), False
        while offset < len(data):
            length = struct.unpack(">I", data[offset : offset + 4])[0]
            kind = data[offset + 4 : offset + 8]
            chunk = data[offset + 8 : offset + 8 + length]
            crc = struct.unpack(">I", data[offset + 8 + length : offset + 12 + length])[0]
            if zlib.crc32(kind + chunk) & 0xFFFFFFFF != crc:
                raise ValueError("PNG块CRC失败")
            if kind == b"IDAT":
                payload.extend(chunk)
            offset += 12 + length
            if kind == b"IEND":
                ended = True
                break
        if not ended or offset != len(data) or not zlib.decompress(payload):
            raise ValueError("PNG数据不可读取")


def verify(path: Path, *, extract: Path | None = None) -> dict[str, Any]:
    """只读CRC/成员/哈希/来源；可选安全解压到必须不存在的新目录，不运行源文件。"""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        canonical = [safe_path(n).casefold() for n in names]
        if len(set(canonical)) != len(canonical):
            raise ValueError("ZIP重复路径")
        if any(stat.S_ISLNK(i.external_attr >> 16) or i.is_dir() for i in archive.infolist()):
            raise ValueError("ZIP不允许符号链接/目录成员")
        if archive.testzip() is not None:
            raise ValueError("ZIP CRC失败")
        if "bundle_manifest.json" not in names or not REQUIRED.issubset(names):
            raise ValueError("缺少必需成员")
        manifest = json_bytes(archive.read("bundle_manifest.json"))
        entries = manifest["files"]
        paths = [item["path"] for item in entries]
        if len(set(paths)) != len(paths) or set(paths) != set(names) - {"bundle_manifest.json"}:
            raise ValueError("清单载荷集合不一致或自引用")
        for item in entries:
            data = archive.read(item["path"])
            if len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError("成员SHA/字节数不符")
            readable(item["path"], data)
        missing = json_bytes(archive.read("missing_files.json"))
        if manifest["bundle_complete"] != (missing == []):
            raise ValueError("完整性声明与缺失清单不符")
        index = json_bytes(archive.read("review_index.json"))
        for category in ("baseline", "atomic", "semantic", "ci", "fingerprint"):
            if (manifest["bundle_complete"] and not index.get(category)) or any(
                n not in names for n in index.get(category, [])
            ):
                raise ValueError("审核索引无法定位必需证据")
        source = json_bytes(archive.read("git/tracked_source_manifest.json"))
        if {r["path"] for r in source} != {
            n for n in names if n.startswith(("source/", "reports/"))
        }:
            raise ValueError("源码清单遗漏或混入未提交文件")
        for item in source:
            data = archive.read(item["path"])
            sha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            if sha != item["git_blob"]:
                raise ValueError("源码与Git blob不符")
        revision = json_bytes(archive.read("git/revision.json"))
        if not re.fullmatch(r"[a-f0-9]{40}", revision["code_commit_sha"]):
            raise ValueError("提交SHA非法")
        if extract is not None:
            extract.mkdir(parents=True, exist_ok=False)
            base = extract.resolve()
            for name in names:
                target = base / name
                if not target.resolve().is_relative_to(base):
                    raise ValueError("解压路径越界")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
            for item in entries:
                data = (base / item["path"]).read_bytes()
                if hashlib.sha256(data).hexdigest() != item["sha256"]:
                    raise ValueError("解压后SHA不符")
                readable(item["path"], data)
        return {
            "status": "PASS",
            "bundle_complete": manifest["bundle_complete"],
            "files": len(names),
            "code_commit_sha": revision["code_commit_sha"],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "bytes": path.stat().st_size,
        }


def main() -> int:
    """CLI任一结构/哈希/读取错误返回非零，禁止用包内代码替代校验。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zip", type=Path)
    parser.add_argument("--extract", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.zip, extract=args.extract), ensure_ascii=False))
        return 0
    except Exception as error:
        print(f"审核ZIP核验失败: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
