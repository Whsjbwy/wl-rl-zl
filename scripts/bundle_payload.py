"""审核包载荷：明确输入目录、提交源码导出及仅副本脱敏，不遍历用户磁盘。"""

import hashlib
import html
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from scripts.verify_bundle import safe_path


def sha(data: bytes) -> str:
    """原始字节SHA-256，不混同Git blob/提交SHA。"""
    return hashlib.sha256(data).hexdigest()


class Payload:
    """唯一包内路径与元数据；日志脱敏只作用于内存中的打包副本。"""

    def __init__(self, replacements: dict[str, str]) -> None:
        self.data: dict[str, bytes] = {}
        self.records: list[dict[str, Any]] = []
        self.replacements = replacements

    def add(
        self, name: str, data: bytes, role: str, source: str, run_id: str, *, redact: bool = False
    ) -> None:
        """禁止重复/危险路径；记录源SHA、打包SHA、字节数及替换规则名。"""
        safe_path(name)
        if name.casefold() in {n.casefold() for n in self.data}:
            raise ValueError("载荷重复路径")
        original = data
        rules = []
        if redact and Path(name).suffix.lower() in {
            ".json",
            ".jsonl",
            ".log",
            ".txt",
            ".xml",
            ".md",
        }:
            text = data.decode("utf-8-sig")
            for prefix, label in sorted(self.replacements.items(), key=lambda p: -len(p[0])):
                for variant in (prefix.replace("\\", "\\\\"), prefix, prefix.replace("\\", "/")):
                    if variant and variant in text:
                        # JUnit文本/属性不能注入裸尖括号；只转义替换标签，保留原XML。
                        replacement = html.escape(label) if name.endswith(".xml") else label
                        text = text.replace(variant, replacement)
                        rules.append(label)
            data = text.encode("utf-8")
        self.data[name] = data
        self.records.append(
            {
                "path": name,
                "source_path": source,
                "source_run_id": run_id,
                "role": role,
                "bytes": len(data),
                "sha256": sha(data),
                "original_sha256": sha(original),
                "redacted": data != original,
                "redaction_rules": sorted(set(rules)),
            }
        )

    def json(self, name: str, value: Any, role: str = "metadata") -> None:
        """生成元数据同样进入清单；不在清单内递归记录清单自身。"""
        self.add(
            name,
            json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode(),
            role,
            "generated",
            "delivery",
        )

    def directory(self, directory: Path, prefix: str, role: str, run_id: str) -> None:
        """只读给定目录；符号链接拒绝，已知缓存排除，所有其他证据完整保留。"""
        excluded = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
        for path in sorted(directory.rglob("*")):
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError("证据目录不允许符号链接")
            relative = path.relative_to(directory)
            if path.is_file() and not excluded.intersection(relative.parts):
                self.add(
                    prefix + "/" + relative.as_posix(),
                    path.read_bytes(),
                    role,
                    relative.as_posix(),
                    run_id,
                    redact=True,
                )

    def write(self, path: Path, complete: bool) -> None:
        """标准DEFLATE；拒绝覆盖，ZIP外写SHA旁文件。"""
        manifest = {
            "schema_version": 1,
            "bundle_complete": complete,
            "self_excluded": "bundle_manifest.json",
            "files": self.records,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(
            path, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as archive:
            for name, data in self.data.items():
                archive.writestr(name, data)
            archive.writestr(
                "bundle_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
        path.with_suffix(".sha256").write_text(
            sha(path.read_bytes()) + "  " + path.name + "\n", encoding="ascii"
        )


def git(root: Path, *arguments: str) -> bytes:
    """只读Git命令，失败不伪造提交信息。"""
    return subprocess.check_output(["git", *arguments], cwd=root)


def export_commit(payload: Payload, root: Path, commit: str) -> list[dict[str, Any]]:
    """从Git对象读取精确源码，不使用工作区文件，不复制.git或未提交代码。"""
    records = []
    historical_xml = []
    for entry in git(root, "ls-tree", "-r", "-z", commit).split(b"\0"):
        if not entry:
            continue
        header, path_bytes = entry.split(b"\t", 1)
        mode, kind, blob = header.decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValueError("Git源码包含子模块/软链接")
        name = path_bytes.decode("utf-8")
        target = (
            "reports/" + name.removeprefix("docs/reports/")
            if name.startswith("docs/reports/")
            else "source/" + name
        )
        data = git(root, "cat-file", "blob", blob)
        if name.startswith("docs/reports/") and name.endswith(".xml"):
            try:
                ElementTree.fromstring(data)
            except ElementTree.ParseError:
                # 只兼容已确认的旧hostname占位符；原Git字节另存文本，绝不覆盖历史证据。
                repaired = data.replace(b'hostname="<host>"', b'hostname="&lt;host&gt;"')
                ElementTree.fromstring(repaired)
                target += ".original.txt"
                view = "evidence/historical_xml/" + name.removeprefix("docs/reports/")
                payload.add(view, repaired, "historical_xml_view", name, commit)
                payload.records[-1].update(
                    original_sha256=sha(data),
                    redacted=True,
                    redaction_rules=["仅将旧hostname属性内<host>转义为XML实体"],
                )
                historical_xml.append(
                    {"repository_path": name, "original": target, "readable_copy": view}
                )
        payload.add(target, data, "committed_source", name, commit)
        records.append(
            {
                "path": target,
                "repository_path": name,
                "git_blob": blob,
                "sha256": sha(data),
                "bytes": len(data),
            }
        )
    payload.json("packager/historical_xml.json", historical_xml)
    return records
