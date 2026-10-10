"""S1-C扩展审核协议的真实ZIP/解压/坏包CLI测试，夹具不冒充实验。"""

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from test_review_bundle import valid_bundle

from scripts.bundle_payload import Payload
from scripts.s1c_bundle import build
from scripts.verify_bundle import S1C_REQUIRED, verify


def fixture(tmp_path: Path) -> Path:
    """在已验收R1结构上增加L1源码、报告与数值索引。"""
    legacy = valid_bundle(tmp_path)
    with zipfile.ZipFile(legacy) as archive:
        data = {n: archive.read(n) for n in archive.namelist() if n != "bundle_manifest.json"}
    for name in S1C_REQUIRED:
        data[name] = b"{}" if name.endswith(".json") else b"fixture"
    data["git/revision.json"] = json.dumps({"stage": "S1-C", "code_commit_sha": "b" * 40}).encode()
    index = json.loads(data["review_index.json"])
    for category in ("derivatives", "forces", "actuator", "snapshots", "trajectories"):
        index[category] = ["README_先读.md"]
    data["review_index.json"] = json.dumps(index).encode()
    data["git/tracked_source_manifest.json"] = json.dumps(
        [
            dict(
                path=n,
                git_blob=hashlib.sha1(b"blob " + str(len(v)).encode() + b"\0" + v).hexdigest(),
            )
            for n, v in data.items()
            if n.startswith(("source/", "reports/"))
        ]
    ).encode()
    payload = Payload({})
    for name, value in data.items():
        payload.add(name, value, "test_fixture", "synthetic", "unit-test")
    path = tmp_path / "s1c.zip"
    payload.write(path, True)
    return path


def test_s1c_bundle_roundtrip(tmp_path):
    path = fixture(tmp_path)
    result = verify(path, extract=tmp_path / "extract")
    assert result["bundle_complete"] and result["status"] == "PASS"


@pytest.mark.parametrize("fault", ["missing", "byte", "duplicate", "traversal"])
def test_s1c_bad_zip_cli(tmp_path, fault):
    good, bad = fixture(tmp_path), tmp_path / "bad.zip"
    target = "source/auv_env/l1/adapter.py"
    with zipfile.ZipFile(good) as src, zipfile.ZipFile(bad, "w", zipfile.ZIP_DEFLATED) as dest:
        for info in src.infolist():
            if fault == "missing" and info.filename == target:
                continue
            data = src.read(info)
            if fault == "byte" and info.filename == target:
                data = bytes([data[0] ^ 1]) + data[1:]
            dest.writestr(info, data)
        if fault == "duplicate":
            with pytest.warns(UserWarning):
                dest.writestr(target, b"duplicate")
        elif fault == "traversal":
            dest.writestr("../escape.txt", b"bad")
    result = subprocess.run(
        [sys.executable, "scripts/verify_bundle.py", str(bad)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 1


def test_s1c_packager_dirty_rejected(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    (tmp_path / "uncommitted.txt").write_text("fixture")
    with pytest.raises(ValueError, match="不干净"):
        build(tmp_path, tmp_path, tmp_path, tmp_path / "receipt.json", tmp_path / "out")
