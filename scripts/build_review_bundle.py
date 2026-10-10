"""从最终Git提交与指定完整运行构造审核ZIP；不是公开Release，不自动上传。"""

import argparse
import getpass
import json
import sys
import tempfile
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.bundle_payload import Payload, export_commit, git
from scripts.s1b_checks import fingerprint
from scripts.strict_json import read_json, write_json
from scripts.verify_bundle import REQUIRED, verify
from scripts.verify_s1b_r1 import validate_report

REVIEWED_HEAD = "2bac576fbca5bd55b55c68da5755fbb17b02737e"


def build(root: Path, run: Path, before: Path, ci: Path, receipt: Path, output: Path) -> Path:
    """只接受干净最终HEAD及其远端回执；缺CI证据保留INCOMPLETE标识，不伪造完整。"""
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    if git(root, "status", "--porcelain"):
        raise ValueError("工作区不干净，禁止混入未提交源代码")
    report = read_json(run / "gate_report.json")
    remote = read_json(receipt)
    if remote["local_sha"] != commit or remote["remote_sha"] != commit:
        raise ValueError("远端与最终提交未对账")
    validate_report(report, root)
    if fingerprint(root)["sha256"] != report["tested_content_sha256"]:
        raise ValueError("最终源码/配置与被测内容指纹不同")
    replacements = {
        str(root): "<repo>",
        str(Path.home()): "<user>",
        "C:\\Users\\runneradmin": "<ci_user>",
        "D:\\a\\_temp": "<ci_temp>",
        tempfile.gettempdir(): "<temp>",
        getpass.getuser(): "<user_name>",
    }
    parent = read_json(run / "s1b/gate_report.json")
    source_command = parent["commands"][0]["argv"]
    replacements[source_command[source_command.index("--source") + 1]] = "<source_checkout>"
    for path in ci.rglob("gate_report.json"):
        record = read_json(path)
        executable = record.get("environment", {}).get("executable")
        if executable:
            replacements[executable] = "<ci_python>"
        for check in record.get("checks", []):
            if check["name"] == "imports":
                terrain = check["details"].get("terrain", "")
                if "\\auv_env\\" in terrain:
                    replacements[terrain.split("\\auv_env\\")[0]] = "<ci_repo>"
    payload = Payload(replacements)
    tracked = export_commit(payload, root, commit)
    payload.directory(run, "evidence/local/" + run.name, "local_evidence", run.name)
    payload.directory(before, "evidence/before_fix/" + before.name, "before_fix", before.name)
    payload.directory(ci, "ci", "ci_evidence", remote.get("ci_run_id", "unknown"))
    payload.json("git/tracked_source_manifest.json", tracked)
    payload.json("git/remote_receipt.json", remote)
    payload.json(
        "git/revision.json",
        {
            "reviewed_head": REVIEWED_HEAD,
            "base_sha": remote["base_sha"],
            "code_commit_sha": commit,
            "git_tree_sha": git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
            "tested_content_sha256": report["tested_content_sha256"],
            "config_sha256": report["config_sha256"],
        },
    )
    for name, arguments in (
        ("diff_from_reviewed_head.patch", ("diff", "--binary", REVIEWED_HEAD, commit)),
        ("changed_files.txt", ("diff", "--name-status", REVIEWED_HEAD, commit)),
        ("diff_stat.txt", ("diff", "--stat", REVIEWED_HEAD, commit)),
    ):
        payload.add("git/" + name, git(root, *arguments), "git_diff", name, commit)
    tool = git(root, "show", commit + ":scripts/verify_bundle.py")
    payload.add(
        "tools/verify_bundle.py", tool, "verification_tool", "scripts/verify_bundle.py", commit
    )
    prefix = "evidence/local/" + run.name
    index = {
        "baseline": [prefix + "/s1b/gate_report.json", prefix + "/s1b/s1a/unit.xml"],
        "atomic": [prefix + "/atomic/results.json"],
        "semantic": [prefix + "/fault_injection/results.json"],
        "ci": [n for n in payload.data if n.startswith("ci/") and n.endswith("gate_report.json")],
        "fingerprint": ["reports/fingerprint_reconciliation.json", prefix + "/tested_content.json"],
    }
    # 每条原始快照、分支、CSV按完整目录保留；索引只是入口，不替代原始数据。
    index["snapshots"] = [n for n in payload.data if n.startswith(prefix) and "/snapshots/" in n]
    index["regressions"] = [n for n in payload.data if n.startswith(prefix) and n.endswith(".csv")]
    payload.json("review_index.json", index)
    missing = []
    if remote.get("ci") != "SUCCESS" or not index["ci"]:
        missing.append("最终head CI未成功或原始CI报告不可用")
    if report["status"] != "PASS":
        missing.append("本次验收未通过")
    if len(index["snapshots"]) < 500 or len(index["regressions"]) < 11:
        missing.append("父阶段原始快照/轨迹证据不完整")
    text = (
        "# S1-B-R1审核材料\n\n仅修订监控异常事务和验收器复算；父阶段S1及外部复审仍PENDING。\n"
        "source是最终提交源码；reports保留原报告与R1报告；evidence为本机完整原始记录及修复前失败；ci为真实CI下载。\n"
        "readme/index定位完整100组快照和五工况CSV，manifest记录原/打包SHA及副本脱敏。\n"
        "旧JUnit中非法hostname占位符原字节保留为.original.txt；可读副本及对应关系见packager/historical_xml.json。\n"
        "只读核验：python tools/verify_bundle.py <本ZIP> --extract <新的空目标路径>。\n"
        "历史报告集中在reports；复现前先复制reports目录到source/docs/reports，保留其相对目录。\n"
        "将其中.xml.original.txt恢复为.xml原名；这是历史字节还原，不用可读副本替代来源。\n"
        "复现：在source安装requirements-lock.txt及requirements-dev.txt，"
        "执行python scripts/verify_s1b_r1.py --source <固定来源检出>。\n"
        "异常候选不提交；有效碰撞/越界/UNCERTAIN仍提交真实步末并终止。\n"
        "固定海流、胶囊几何近似；未进行CAD包络证明、变海流、规划或训练。\n"
    )
    payload.add("README_先读.md", text.encode(), "readme", "generated", run.name)
    payload.add("tools/README_复现命令.md", text.encode(), "readme", "generated", run.name)
    payload.json(
        "packager/privacy_audit.json",
        {
            "policy": "仅打包副本替换已知绝对路径，不改变数值/序列/历史源字节",
            "source_policy": "所有已提交文件按Git blob逐字节导出",
            "redacted_files": [r["path"] for r in payload.records if r["redacted"]],
            "excluded": [".git", ".venv", ".env", "缓存", "无关个人文件"],
        },
    )
    missing.extend(sorted(REQUIRED - (set(payload.data) | {"missing_files.json"})))
    payload.json("missing_files.json", missing)
    name = f"S1B_R1_review_{commit[:12]}_{run.name}{'_INCOMPLETE' if missing else ''}.zip"
    path = output / name
    payload.write(path, not missing)
    if path.stat().st_size >= 150 * 1024 * 1024:
        raise ValueError("审核包超过150MB，需显式去重或分包，不删除必需证据")
    result = verify(path, extract=output / (path.stem + "_extracted"))
    write_json(output / (path.stem + "_verification.json"), result)
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=["S1B_R1", "S1C"], default="S1B_R1")
    parser.add_argument("--before", type=Path)
    for name in ("run", "ci", "receipt"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("review_packages"))
    args = parser.parse_args()
    if args.stage == "S1C":
        from scripts.s1c_bundle import build as build_s1c

        result = build_s1c(
            Path(__file__).resolve().parents[1],
            args.run.resolve(),
            args.ci.resolve(),
            args.receipt.resolve(),
            args.output.resolve(),
        )
        print(json.dumps({"zip": str(result)}, ensure_ascii=False))
        raise SystemExit(0)
    if args.before is None:
        parser.error("S1B_R1需要--before")
    result = build(
        Path(__file__).resolve().parents[1],
        args.run.resolve(),
        args.before.resolve(),
        args.ci.resolve(),
        args.receipt.resolve(),
        args.output.resolve(),
    )
    print(json.dumps({"zip": str(result)}, ensure_ascii=False))
