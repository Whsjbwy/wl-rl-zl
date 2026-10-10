"""沿用R1载荷/只读核验协议，扩展S1-C完整本机与CI证据。"""

import getpass
import tempfile
from pathlib import Path

from scripts.bundle_payload import Payload, export_commit, git
from scripts.s1b_checks import fingerprint
from scripts.strict_json import read_json, write_json
from scripts.verify_bundle import REQUIRED, S1C_REQUIRED, verify
from scripts.verify_s1c import validate_report


def build(root: Path, run: Path, ci: Path, receipt: Path, output: Path) -> Path:
    """最终干净提交导出；缺CI不能声称完整，不上传ZIP或公开Release。"""
    if git(root, "status", "--porcelain"):
        raise ValueError("工作区不干净")
    commit = git(root, "rev-parse", "HEAD").decode().strip()
    remote, report = read_json(receipt), read_json(run / "gate_report.json")
    if remote["local_sha"] != commit or remote["remote_sha"] != commit:
        raise ValueError("本地/远端SHA不符")
    validate_report(report, root)
    if report.get("acceptance_status") != "PASS":
        raise ValueError("完整故障传播验收尚未通过")
    if report["tested_content_sha256"] != fingerprint(root)["sha256"]:
        raise ValueError("最终源码与被测指纹不符")
    replacements = {
        str(root): "<repo>",
        str(Path.home()): "<user>",
        getpass.getuser(): "<user_name>",
        tempfile.gettempdir(): "<temp>",
        "C:\\Users\\runneradmin": "<ci_user>",
        "D:\\a\\_temp": "<ci_temp>",
    }
    for command in report["commands"]:
        if "--source" in command["argv"]:
            replacements[command["argv"][command["argv"].index("--source") + 1]] = (
                "<source_checkout>"
            )
    ci_reports = []
    for path in ci.rglob("gate_report.json"):
        data = read_json(path)
        executable = data.get("environment", {}).get("executable")
        if executable:
            replacements[executable] = "<ci_python>"
        if data.get("stage") == "S1-C":
            ci_reports.append(data)
            for command in data["commands"]:
                replacements[command["cwd"]] = "<ci_repo>"
    payload = Payload(replacements)
    tracked = export_commit(payload, root, commit)
    prefix = "evidence/local/" + run.name
    payload.directory(run, prefix, "local_evidence", run.name)
    payload.directory(ci, "ci", "actual_ci_evidence", str(remote.get("ci_run_id", "unknown")))
    payload.json("git/tracked_source_manifest.json", tracked)
    payload.json("git/remote_receipt.json", remote)
    payload.json(
        "git/revision.json",
        dict(
            stage="S1-C",
            code_commit_sha=commit,
            git_tree_sha=git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
            base_sha=remote["base_sha"],
            reviewed_head="c7b1af21abdd316f13f1cf4dfb5b3823c51f0cc4",
            tested_content_sha256=report["tested_content_sha256"],
            config_sha256=report["config_sha256"],
        ),
    )
    for name, arguments in (
        ("diff_from_reviewed_head.patch", ("diff", "--binary", remote["base_sha"], commit)),
        ("changed_files.txt", ("diff", "--name-status", remote["base_sha"], commit)),
        ("diff_stat.txt", ("diff", "--stat", remote["base_sha"], commit)),
    ):
        payload.add("git/" + name, git(root, *arguments), "git_diff", name, commit)
    payload.add(
        "tools/verify_bundle.py",
        git(root, "show", commit + ":scripts/verify_bundle.py"),
        "verification_tool",
        "scripts/verify_bundle.py",
        commit,
    )
    index = dict(
        baseline=[prefix + "/parent/gate_report.json"],
        atomic=[prefix + "/atomic/results.json"],
        semantic=[prefix + "/fault_injection/results.json"],
        ci=[n for n in payload.data if n.startswith("ci/") and n.endswith("gate_report.json")],
        fingerprint=[prefix + "/tested_content.json"],
        derivatives=[prefix + "/numeric/derivatives.json"],
        forces=[prefix + "/numeric/momentum.json", prefix + "/numeric/l0_l1_current.json"],
        actuator=[prefix + "/numeric/actuators.json"],
        snapshots=[n for n in payload.data if n.startswith(prefix) and "/snapshots/" in n],
        trajectories=[
            n for n in payload.data if n.startswith(prefix) and n.endswith((".jsonl", ".csv"))
        ],
    )
    payload.json("review_index.json", index)
    text = (
        "# S1-C审核包\n\n独立开阔水域L1工程物理核，父阶段S1仍PENDING。\n"
        "source来自最终提交，reports为历史与新报告，evidence/local保存完整原始数值，ci仅为真实下载。\n"
        "制造场只验证导数；未建地形边界海流、压力梯度、波浪或环境涡量载荷；未进行训练。\n"
        "每项证据入口见review_index.json，逐文件原/包内SHA与脱敏映射见bundle_manifest.json。\n"
        "只读反向核验：python tools/verify_bundle.py <zip> --extract <新目录>。\n"
        "离线源码复现前，将reports复制到source/docs/reports；历史.xml.original.txt恢复为.xml原名。\n"
        "在source安装锁定依赖；python scripts/verify_s1c.py --source <固定来源对象检出>。\n"
        "完整R1父验收只调用一次，包含S1-B/S1-A/S0；L0保持冻结。\n"
    )
    payload.add("README_先读.md", text.encode(), "readme", "generated", run.name)
    payload.add("tools/README_复现命令.md", text.encode(), "readme", "generated", run.name)
    payload.json(
        "packager/privacy_audit.json",
        {
            "policy": "仅副本脱敏，不改数值及原始记录",
            "redacted_files": [r["path"] for r in payload.records if r["redacted"]],
            "excluded": [".git", ".venv", ".env", "缓存", "密钥", "无关个人文件"],
        },
    )
    missing = []
    if remote.get("ci") != "SUCCESS" or not ci_reports:
        missing.append("最终head真实CI成功及完整原始证据缺失")
    for data in ci_reports:
        if data.get("tested_head") != commit or data.get("acceptance_status") != "PASS":
            missing.append("CI非最终head或完整验收未通过")
        if (
            data["status"] != "PASS"
            or data["tested_content_sha256"] != report["tested_content_sha256"]
        ):
            missing.append("CI未通过或内容指纹不同")
    missing.extend(sorted((REQUIRED | S1C_REQUIRED) - (set(payload.data) | {"missing_files.json"})))
    payload.json("missing_files.json", missing)
    path = output / f"S1C_review_{commit[:12]}_{run.name}{'_INCOMPLETE' if missing else ''}.zip"
    payload.write(path, not missing)
    if path.stat().st_size >= 150 * 1024 * 1024:
        raise ValueError("超150MB，需要去重或独立标准分包，不能删必需证据")
    result = verify(path, extract=output / (path.stem + "_extracted"))
    write_json(output / (path.stem + "_verification.json"), result)
    return path
