"""从核验后的真实S1-A报告生成中文小型证据，不覆盖任何S0历史报告。"""

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.publish_s0 import redact
from scripts.s0_checks import digest, read_json
from scripts.verify_s0 import write_json
from scripts.verify_s1a import validate_report


def publish(root: Path, run: Path, source: Path) -> None:
    """核验原始哈希和轨迹后脱敏；原始数值仍在run目录，只发布日志/指标/清单。"""
    report = read_json(run / "gate_report.json")
    validate_report(report, root)
    target = root / "docs/reports/evidence" / f"s1a-{run.name}"
    target.mkdir(exist_ok=False)
    selected = list(run.glob("*.log")) + list(run.glob("*.xml"))
    selected += [
        run / "resolved_config.json",
        run / "tested_content.json",
        run / "experiments/metrics.json",
    ]
    selected += list((run / "s0").glob("*.log"))
    selected += [run / "s0/baseline/results/remus100/summary.json", run / "s0/gate_report.json"]
    evidence = []
    for path in selected:
        name = path.relative_to(run).as_posix().replace("/", "__")
        output = target / name
        output.write_text(
            redact(path.read_text(encoding="utf-8", errors="replace"), root, source),
            encoding="utf-8",
        )
        evidence.append(
            {
                "path": output.relative_to(root).as_posix(),
                "sha256": digest(output),
                "raw_sha256": digest(path),
            }
        )
    sanitized = json.loads(redact(json.dumps(report, ensure_ascii=False), root, source))
    sanitized["published_evidence"] = evidence
    write_json(root / "docs/reports/S1A_gate_report.json", sanitized)
    metrics = read_json(run / "experiments/metrics.json")
    s0 = read_json(run / "s0/baseline/results/remus100/summary.json")
    unit = next(c["details"] for c in report["checks"] if c["name"] == "unit_collection")
    lines = [
        "# S1-A 验收报告",
        "",
        f"**S1-A：{report['status']}；父阶段 S1：PENDING，尚未整体验收。**",
        "",
        f"运行编号：`{run.name}`；任务卡V1.0。",
        f"实际开发base：`{report['base_sha']}`。",
        f"被测基准HEAD：`{report['tested_base_commit']}`；工作区有未提交改动。",
        f"被测内容SHA-256：`{report['tested_content_sha256']}`。",
        f"解析配置SHA-256：`{report['config_sha256']}`。",
        "最终Git SHA和CI结果另见交付回复，避免报告自引用。",
        "",
        "## 实现范围",
        "",
        "实现不可变配置、reset/step/get_state/set_state/clone、完整控制器与PCG64快照、JSON恢复和独立分支。",
        "冻结36文件原样保留；未改动力学、控制参数、积分、固定海流或限幅。",
        "原S0根报告与文字报告哈希保持不变，新增S0交付回执连接真实PR/合并/CI。",
        "",
        "## 五种完整逐步对照",
        "",
        "| 工况 | 记录数 | 物理时长(s) | 全通道最大绝对差 |",
        "|---|---:|---:|---:|",
    ]
    lines += [
        f"| {row['case']} | {row['samples']} | {row['duration_s']} | {row['max_abs']} |"
        for row in metrics["regressions"]
    ]
    lines += [
        "",
        "每通道max_abs/RMS及单位见metrics.json与method_code_map.md。",
        "状态/执行器/指令使用atol=rtol=1e-10；时间≤1e-9秒；角度比较原始弧度。",
        "真实实例10000步停在200秒；末行指令只在克隆预览，不多推进主实例。",
        "",
        "## 快照、分支与重置",
        "",
        f"完成{len(metrics['snapshots'])}组，每组100步内存恢复与磁盘JSON恢复。",
        f"内存恢复最大绝对差：{max(x['replay_max_abs'] for x in metrics['snapshots'])}。",
        f"JSON恢复最大绝对差：{max(x['json_max_abs'] for x in metrics['snapshots'])}。",
        f"分支后继续运行最大绝对差：{max(x['branch_max_abs'] for x in metrics['snapshots'])}。",
        "每组验证主环境/A/B隔离、外部快照修改、读取/保存无副作用，计数、标识、RNG状态精确一致。",
        f"五种工况重置各100步，最大绝对差：{max(x['max_abs'] for x in metrics['resets'])}。",
        "完整状态序列和分支前后快照保存在原始目录，不仅保存末值。",
        "",
        "## 原S0与检查器",
        "",
        f"本次原S0：{s0['samples']}点/{s0['simulation_duration_s']}秒；"
        f"深度{s0['final_depth_m']}m，航向{s0['final_heading_deg']}度，航程{s0['path_length_m']}m。",
        f"全部第一方测试实际收集{unit['tests']}项；指定上游4项；Ruff/格式/mypy/pip check通过。",
        "故障注入覆盖真实子进程非零/超时、必需测试缺失、证据哈希不符、轨迹扰动、JSON不完整。",
        "",
        "## 实际命令",
        "",
    ]
    for command in sanitized["commands"]:
        argv = "& " + " ".join("'" + str(arg).replace("'", "''") + "'" for arg in command["argv"])
        lines += [f"- `{argv}`；退出{command['exit_code']}，耗时{command['duration_s']:.3f}秒。"]
    lines += [
        "",
        f"原始记录：`{run.relative_to(root).as_posix()}/`。",
        f"脱敏证据：`{target.relative_to(root).as_posix()}/`。",
        "实际导入路径、硬件/依赖、命令及全部原始产物哈希见S1A_gate_report.json。",
        "",
        "## 未运行项与边界",
        "",
        "未实施/验收地形、碰撞、变海流、能耗模型、Gymnasium导航任务、教师或强化学习训练。",
        "未运行GPU/CUDA、动画和.cmd双击；S1-A不把这些未授权内容写成通过。",
        "当前动力学确定性，RNG实验只验证状态独立与恢复。原上游限幅后更新的潜在越界行为未改。",
        "按任务卡完成后停止，后续子任务须另行授权。",
        "",
    ]
    (root / "docs/reports/S1A_验收报告.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    args = parser.parse_args()
    publish(Path(__file__).resolve().parents[1], args.run.resolve(), args.source.resolve())
