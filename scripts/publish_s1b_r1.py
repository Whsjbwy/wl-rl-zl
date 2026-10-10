"""生成R1中文/结构化报告，保留历史S0/S1-A/S1-B报告和完整原始记录。"""

import argparse
import json
import sys
from pathlib import Path
from xml.etree import ElementTree

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.publish_s0 import redact
from scripts.s0_checks import digest
from scripts.strict_json import read_json, write_json
from scripts.verify_s1b_r1 import validate_report


def publish(root: Path, run: Path, source: Path) -> None:
    """从真实报告复算后发布小型证据；最终SHA/CI/ZIP后生成外部回执避免自引用。"""
    report = read_json(run / "gate_report.json")
    validate_report(report, root)
    if report["status"] != "PASS":
        raise ValueError("未通过不能发布完成报告")
    folder = root / "docs/reports/evidence" / ("s1b-r1-" + run.name)
    folder.mkdir(exist_ok=False)
    paths = [
        run / "tested_content.json",
        run / "resolved_config.json",
        run / "s1b/s1a/unit.xml",
        run / "atomic/results.json",
        run / "fault_injection/results.json",
        run / "s1b/experiments/metrics.json",
    ]
    paths += list(run.glob("*.log"))
    evidence = []
    for path in paths:
        target = folder / path.relative_to(run).as_posix().replace("/", "__")
        text = redact(path.read_text(encoding="utf-8"), root, source)
        if path.suffix == ".xml":
            text = text.replace('hostname="<host>"', 'hostname="&lt;host&gt;"')
            ElementTree.fromstring(text)
        target.write_text(text, encoding="utf-8", newline="\n")
        evidence.append(
            {
                "path": target.relative_to(root).as_posix(),
                "sha256": digest(target),
                "raw_sha256": digest(path),
            }
        )
    public = json.loads(
        redact(json.dumps(report, ensure_ascii=False, allow_nan=False), root, source)
    )
    public["published_evidence"] = evidence
    write_json(root / "docs/reports/S1B_R1_gate_report.json", public)
    checks = {r["name"]: r["details"] for r in report["checks"]}
    metrics = read_json(run / "s1b/experiments/metrics.json")
    parent = read_json(run / "s1b/s1a/experiments/metrics.json")
    s0 = read_json(run / "s1b/s1a/s0/baseline/results/remus100/summary.json")
    lines = [
        "# S1-B-R1 验收报告",
        "",
        "**本机S1-B-R1：PASS；父阶段S1：PENDING；外部复审：PENDING。**",
        "",
        f"本次运行：`{run.name}`。审查锚点：`{report['reviewed_head']}`。",
        f"被测HEAD：`{report['tested_base_commit']}`，测试时工作区有改动：{report['working_tree_dirty']}。",
        f"内容指纹：`{report['tested_content_sha256']}`；配置原始SHA：`{report['config_sha256']}`。",
        "最终提交/远程/CI及ZIP SHA在交付回执中分开记录，测试前HEAD不冒称最终提交。",
        "",
        "## 问题闭环",
        "",
        "修复前真实Windows探针12项失败：三类几何异常半更新，九类坏摘要未被拒绝。",
        "证据run=20261010T095423Z-0a5a96b0，完整前后快照、重试状态、堆栈及JUnit单独保留。",
        "R1候选物理步/几何结构验证/统一提交；异常保留全快照，真实COLLISION/UNCERTAIN/越界仍提交步末并终止。",
        "R2固定输入重新计算手算expected和实际actual；保存梯度点位/步长/解析/差分；严格有限值和类型。",
        "从六个反例及六条诊断的每个真实步复算计数/比例/细分，初态、中性对照和内部query_count不入分母。",
        "对未改原始证据及其临时副本先执行正向CLI，再22类篡改并同步哈希；全部由真实--check-report退出1拒绝。",
        "R3五文件均可由Git blob转CRLF精确重建CI记录SHA；规范化内容一致，未重写旧报告。",
        "",
        "## 实际回归",
        "",
        f"第一方{checks['tests']['total']}项通过，逐ID保留原159项，新增{checks['tests']['added']}项；指定上游4项通过。",
        f"B01—B22保留；R1完整异常对照3类；语义CLI拒绝{checks['raw_evidence']['semantic_rejections']}类。",
        f"解析距离最大误差{metrics['analytic']['max_abs_error_m']} m；"
        f"梯度差分误差{metrics['analytic']['gradient_max_abs_error']}。",
        f"监控中性完整状态误差{metrics['neutral']['max_abs']}；"
        f"五种200秒逐步回归最大误差{max(r['max_abs'] for r in parent['regressions'])}。",
        "100组各100步内存/JSON恢复及分支全部通过；每组完整五份证据保留。",
        f"S0：{s0['samples']}点/{s0['simulation_duration_s']}s；深度{s0['final_depth_m']}m，航向{s0['final_heading_deg']}度，航程{s0['path_length_m']}m。",
        f"真实统计：{metrics['status_counts']}；不确定比例{metrics['uncertain_ratio']}；平均细分{metrics['mean_subdivisions']}。",
        "Ruff/格式/mypy、导入、来源36文件、依赖锁、pip check、历史哈希和指纹均通过。",
        "",
        "## 实际命令",
        "",
    ]
    for relative in (
        "gate_report.json",
        "s1b/gate_report.json",
        "s1b/s1a/gate_report.json",
        "s1b/s1a/s0/gate_report.json",
    ):
        data = read_json(run / relative)
        for command in data["commands"]:
            text = " ".join(command["argv"])
            lines.append(
                f"- `{redact(text, root, source)}`；退出{command['exit_code']}；"
                f"{command['duration_s']:.3f}s。"
            )
    lines += [
        "",
        "## 证据与交付边界",
        "",
        f"原始数据：`{run.relative_to(root).as_posix()}`。",
        f"小型脱敏证据：`{folder.relative_to(root).as_posix()}`。",
        "审核ZIP从最终提交导出源码，装入本机完整原始证据及真实CI下载，随后CRC/哈希/解压反向验收。",
        "验收与打包器缺文件/篡改/重复/越界/软链接失败传播均有测试；最终ZIP大小/SHA/校验结果另见交付回执。",
        "原159项覆盖及历史结果保留；未放宽阈值，未改变冻结动力学、控制器或依赖锁。",
        "未实施S1-C、变海流、能耗、规划、训练、封存集、GPU/CUDA；未进行CAD包络证明。",
        "既有Actions Node弃用警告不因此升级；后续合并和外部复审由用户决定。",
        "",
    ]
    (root / "docs/reports/S1B_R1_验收报告.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )
    table = [
        "# S1-B-R1 问题闭环表",
        "",
        "|问题|修复前证据|修订|本次复核|",
        "|---|---|---|---|",
        "|R1异常半更新|真实三类异常，12项附件探针中的3项失败|候选推进、严格几何结果、统一提交|完整快照不变，重试只一步，正常终止保留步末|",
        "|R2摘要复算缺口|9类坏摘要未被拒绝|独立输入/逐步原始数据复算，严格JSON|22类真实CLI坏证据均退出1；正向副本退出0|",
        "|R3指纹差异|本机与CI五个文本SHA不同|Git blob/EOL逐项对账，"
        "五文件新LF规则|归一化内容一致，旧SHA保留|",
        "|审核包|原交付没有完整ZIP|可复用生成器及只读核验|坏包测试通过；最终ZIP回执另列|",
        "",
        "外部复审仍PENDING。本表只描述本轮实际修订和机器验证，不代替用户审核。",
    ]
    (root / "docs/reports/S1B_R1_问题闭环表.md").write_text(
        "\n".join(table), encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    publish(Path(__file__).resolve().parents[1], args.run.resolve(), args.source.resolve())
