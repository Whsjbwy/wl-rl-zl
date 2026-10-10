"""从固定 Git 对象迁入基线；不检出或修改来源仓库。"""

import argparse
import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

SOURCE_SHA = "c2dfbd4f4425d29bdcba98294563cda19890cdb1"
UPSTREAM_SHA = "a93d1938ba1f78f2580c24c73f003091b2f03cd0"
SOURCE_URL = "https://github.com/Whsjbwy/AUVWorkPlace.git"


def source_objects(source: Path) -> dict[str, str]:
    """读取固定提交的路径到 blob SHA 映射；Git 失败时抛异常。"""
    actual = (
        subprocess.check_output(["git", "-C", str(source), "rev-parse", f"{SOURCE_SHA}^{{commit}}"])
        .decode()
        .strip()
    )
    if actual != SOURCE_SHA:
        raise ValueError("源提交不符")
    tree = subprocess.check_output(["git", "-C", str(source), "ls-tree", "-rz", SOURCE_SHA])
    objects = {}
    for row in tree.split(b"\0"):
        if row:
            metadata, path = row.split(b"\t", 1)
            mode, kind, blob = metadata.decode().split()
            if kind != "blob" or mode != "100644":
                raise ValueError("来源包含非普通文件，需人工审查")
            objects[path.decode()] = blob
    return objects


def destination(source_path: str) -> str | None:
    """返回任务卡允许的目标相对路径；未授权路径返回 None。"""
    if source_path == "README.md":
        return "docs/provenance/source_readme.md"
    if source_path.startswith(("PythonVehicleSimulator/", "examples/")):
        return source_path
    if source_path in {"run_remus.py", "run_remus.cmd", "requirements-lock.txt"}:
        return source_path
    return None


def migrate(source: Path, root: Path) -> None:
    """逐字节迁入允许文件并登记来源；拒绝覆盖已有文件。"""
    objects = source_objects(source)
    files = []
    for name, blob in objects.items():
        target = destination(name)
        if target is None:
            continue
        data = subprocess.check_output(["git", "-C", str(source), "cat-file", "blob", blob])
        path = root / target
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(data)
        files.append(
            {
                "source_path": name,
                "target_path": target,
                "source_blob_sha": blob,
                "sha256": hashlib.sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
        )
    ignore = subprocess.check_output(
        ["git", "-C", str(source), "cat-file", "blob", objects[".gitignore"]]
    ).decode()
    path = root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    rules = list(
        dict.fromkeys((existing + "\n" + ignore + "\n.ruff_cache/\n.mypy_cache/\n").splitlines())
    )
    path.write_text("\n".join(rules).strip() + "\n", encoding="utf-8")
    manifest = {
        "source_url": SOURCE_URL,
        "source_commit": SOURCE_SHA,
        "upstream_commit": UPSTREAM_SHA,
        "imported_at": datetime.now(UTC).isoformat(),
        "files": files,
        "gitignore_merge": {
            "source_blob_sha": objects[".gitignore"],
            "source_rules": ignore.splitlines(),
        },
    }
    (root / "docs/provenance/source_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    migrate(args.source, Path(__file__).resolve().parents[1])
