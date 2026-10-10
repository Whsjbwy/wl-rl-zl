"""审核包生成/只读核验的真ZIP测试；坏包CLI必须非零且不执行载荷。"""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import pytest

from scripts.build_review_bundle import build
from scripts.bundle_payload import Payload, export_commit
from scripts.verify_bundle import REQUIRED, verify

ROOT = Path(__file__).resolve().parents[1]


def valid_bundle(tmp_path: Path) -> Path:
    """可读的最小结构夹具，明确不作为实验审核结果。"""
    payload = Payload({"C:/Users/private": "<user>"})
    values = {name: b"{}" if name.endswith(".json") else b"fixture" for name in REQUIRED}
    values["missing_files.json"] = b"[]"
    values["review_index.json"] = json.dumps(
        {
            name: ["README_先读.md"]
            for name in ("baseline", "atomic", "semantic", "ci", "fingerprint")
        }
    ).encode()
    values["git/revision.json"] = json.dumps({"code_commit_sha": "a" * 40}).encode()
    tracked = []
    for name, data in values.items():
        if name.startswith(("source/", "reports/")):
            tracked.append(
                {
                    "path": name,
                    "git_blob": hashlib.sha1(
                        b"blob " + str(len(data)).encode() + b"\0" + data
                    ).hexdigest(),
                }
            )
    values["git/tracked_source_manifest.json"] = json.dumps(tracked).encode()
    for name, data in values.items():
        payload.add(name, data, "test_fixture", "synthetic", "unit-test")
    path = tmp_path / "valid.zip"
    payload.write(path, True)
    return path


def test_bundle_roundtrip(tmp_path: Path) -> None:
    """生成器DEFLATE/旁哈希/清单/新目录解压真实往返。"""
    path = valid_bundle(tmp_path)
    result = verify(path, extract=tmp_path / "extracted")
    assert result["status"] == "PASS"
    assert path.with_suffix(".sha256").read_text().startswith(result["sha256"])
    with pytest.raises(FileExistsError):
        verify(path, extract=tmp_path / "extracted")


@pytest.mark.parametrize(
    "fault", ["missing", "byte", "duplicate", "traversal", "absolute", "symlink"]
)
def test_bundle_bad_cli(tmp_path: Path, fault: str) -> None:
    """缺必需文件、篡改、重复、越界和软链接必须由真正CLI拒绝。"""
    good = valid_bundle(tmp_path)
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as dest:
        for info in src.infolist():
            if fault == "missing" and info.filename == "source/AGENTS.md":
                continue
            data = src.read(info)
            if fault == "byte" and info.filename == "source/AGENTS.md":
                data += b"x"
            dest.writestr(info, data)
        if fault == "duplicate":
            with pytest.warns(UserWarning):
                dest.writestr("source/AGENTS.md", b"duplicate")
        elif fault in {"traversal", "absolute"}:
            dest.writestr("../escape.txt" if fault == "traversal" else "C:/escape.txt", b"bad")
        elif fault == "symlink":
            info = zipfile.ZipInfo("source/link")
            info.create_system = 3
            info.external_attr = 0o120777 << 16
            dest.writestr(info, b"../escape")
    result = subprocess.run(
        [sys.executable, "scripts/verify_bundle.py", str(bad)],
        cwd=ROOT,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1
    assert not (tmp_path / "escape.txt").exists()


def test_bundle_redaction_copy_only() -> None:
    """只改打包副本，保留原SHA、替换标签；数值与错误信息不变。"""
    payload = Payload({"C:/Users/private": "<user>"})
    original = b'{"path":"C:/Users/private/run","value":1.25}'
    payload.add("evidence/sample.json", original, "log", "run/sample.json", "run", redact=True)
    assert payload.data["evidence/sample.json"] == b'{"path":"<user>/run","value":1.25}'
    assert payload.records[0]["original_sha256"] == hashlib.sha256(original).hexdigest()
    with pytest.raises(ValueError):
        payload.add("evidence/sample.json", b"duplicate", "log", "x", "x")
    xml = (
        b'<testsuite><testcase file="C:/Users/private/test.py">'
        b"<failure>C:/Users/private/run</failure></testcase></testsuite>"
    )
    payload.add("evidence/unit.xml", xml, "junit", "unit.xml", "run", redact=True)
    tree = ElementTree.fromstring(payload.data["evidence/unit.xml"])
    assert tree[0].attrib["file"] == "<user>/test.py"
    assert tree[0][0].text == "<user>/run"


def test_bundle_source_from_commit(tmp_path: Path) -> None:
    """导出Git blob而非工作区未提交内容；不复制.git。"""

    def git(*args: str) -> bytes:
        return subprocess.check_output(["git", *args], cwd=tmp_path, stderr=subprocess.STDOUT)

    git("init")
    (tmp_path / "AGENTS.md").write_bytes(b"committed\n")
    git("add", "AGENTS.md")
    git(
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "commit",
        "-m",
        "fixture",
    )
    (tmp_path / "AGENTS.md").write_bytes(b"uncommitted\n")
    payload = Payload({})
    rows = export_commit(payload, tmp_path, git("rev-parse", "HEAD").decode().strip())
    assert payload.data["source/AGENTS.md"] == b"committed\n" and len(rows) == 1
    assert not any(".git/" in p for p in payload.data)
    # 顶层打包入口必须拒绝脏工作区，不以导出能力掩盖未测内容。
    with pytest.raises(ValueError, match="不干净"):
        build(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path / "receipt.json", tmp_path / "out")
