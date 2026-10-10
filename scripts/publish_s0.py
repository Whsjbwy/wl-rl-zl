"""将真实验收报告与小型日志脱敏归档；原始 CSV 和日志仍保留在 results。"""

import argparse
import json
import platform
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.s0_checks import Json, digest, read_json
from scripts.verify_s0 import validate_report, write_json


def redact(text: str, root: Path, source: Path) -> str:
    """替换本机路径与设备主机名，不输出环境变量或身份凭据。"""
    for path, replacement in [
        (root, "."),
        (source, "<source_checkout>"),
        (Path(sys.base_prefix), "<python312>"),
        (Path.home(), "<user>"),
        (Path(sys.executable), "<python>"),
    ]:
        for spelling in (str(path), path.as_posix(), str(path).replace("\\", "\\\\")):
            text = text.replace(spelling, replacement)
    return text.replace(platform.node(), "<host>") if platform.node() else text


def publish(root: Path, run: Path, source: Path) -> Json:
    """先验证真实报告及产物，再生成可提交的中文摘要和脱敏证据。"""
    report = read_json(run / "gate_report.json")
    validate_report(report, root)
    output = root / "docs/reports/evidence" / run.name
    output.mkdir(parents=True, exist_ok=False)
    selected = list(run.glob("*.log")) + list(run.glob("*.xml"))
    selected += [run / "tested_content.json", run / "resolved_config.json"]
    summary = run / "baseline/results/remus100/summary.json"
    if summary.exists():
        selected.append(summary)
    evidence = []
    for path in selected:
        target = output / path.name
        target.write_text(
            redact(path.read_text(encoding="utf-8", errors="replace"), root, source),
            encoding="utf-8",
        )
        evidence.append(
            {
                "path": target.relative_to(root).as_posix(),
                "sha256": digest(target),
                "raw_sha256": digest(path),
            }
        )
    sanitized = json.loads(redact(json.dumps(report, ensure_ascii=False), root, source))
    sanitized["published_evidence"] = evidence
    write_json(root / "gate_report.json", sanitized)
    numerical: Json = next((c["details"] for c in report["checks"] if c["name"] == "numerical"), {})
    lines = [
        "# S0 验收报告",
        "",
        f"阶段结论：**{report['status']}**。运行编号：`{run.name}`。",
        "",
        "本报告由真实运行记录生成；examples 是来源演示，不作为本次证据。",
        f"基线 SHA：`{report['source_commit']}`。",
        f"被测基准 HEAD：`{report['tested_base_commit']}`；测试时工作区有改动。",
        f"被测内容 SHA-256：`{report['tested_content_sha256']}`。",
        "最终提交 SHA 另见交付回复，避免报告自引用。",
        "",
        "## 实测与阈值",
        "",
        "| 指标 | CSV 实测 | 参考 | 绝对差 | 容差 |",
        "|---|---:|---:|---:|---:|",
    ]
    for key, reference in report["thresholds"]["reference"].items():
        lines.append(
            f"| {key} | {numerical.get('actual', {}).get(key, 'NOT_RUN')} | {reference} | "
            f"{numerical.get('difference', {}).get(key, 'NOT_RUN')} | "
            f"{report['thresholds']['tolerance'][key]} |"
        )
    lines += [
        "",
        f"记录数：{numerical.get('samples', 'NOT_RUN')}；"
        f"时长：{numerical.get('duration_s', 'NOT_RUN')} 秒。",
        "",
        "## 实际检查",
        "",
        "| 检查 | 状态 |",
        "|---|---|",
    ]
    lines += [f"| {c['name']} | {c['status']} |" for c in report["checks"]]
    lines += [
        "",
        "## 命令与证据",
        "",
        "完整命令、退出码、耗时、环境、配置及原始产物哈希见根目录 gate_report.json。",
        f"脱敏小型证据：`{output.relative_to(root).as_posix()}/`。",
        f"本地完整原始记录：`{run.relative_to(root).as_posix()}/`。",
        "原始记录不入 Git，含 CSV/summary/图及失败日志。",
        "",
        "```powershell",
        *[" ".join(c["argv"]) + f" # exit={c['exit_code']}" for c in sanitized["commands"]],
        "```",
        "",
        "## 范围与限制",
        "",
        "未执行上游全套测试、Windows 双击 .cmd、动画、GPU/CUDA 或强化学习训练。",
        "本次只验证 S0 冻结入口与指定测试，不宣称物理模型已全面验证。",
        "远程同步及 CI 在本报告生成时尚未执行；最终状态见交付回复。",
        f"未关闭检查：{', '.join(report['unresolved_issues']) or '无'}。",
        "S0 结束后停止；S1 需要用户另行授权。",
        "",
    ]
    (root / "docs/reports/S0_验收报告.md").write_text("\n".join(lines), encoding="utf-8")
    return sanitized


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    publish(Path(__file__).resolve().parents[1], args.run.resolve(), args.source.resolve())
