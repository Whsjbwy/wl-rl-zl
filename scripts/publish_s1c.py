"""从本次真实记录生成中文及脱敏摘要；完整原始记录仍保留在run目录。"""

import argparse
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.publish_s0 import redact
from scripts.strict_json import read_json, write_json
from scripts.verify_s1c import validate_report


def publish(root: Path, run: Path, source: Path) -> None:
    """摘要明确省略全轨迹，不能用于替代原始--check-report输入。"""
    report = read_json(run / "gate_report.json")
    validate_report(report, root)
    if report.get("acceptance_status") != "PASS":
        raise ValueError("完整验收仍待完成")
    faults = report["fault_injection"]
    if faults["positive"]["exit_code"] != 0 or any(r["exit_code"] != 1 for r in faults["cases"]):
        raise ValueError("CLI故障未通过")
    public = json.loads(redact(json.dumps(report, ensure_ascii=False), root, source))
    for scene in public["details"]["convergence"]:
        for difference in scene["differences"]:
            difference.pop("common_time_errors")
    public["publication_kind"] = "脱敏摘要；完整原始报告及共同时间误差位于指定run目录和审核包"
    write_json(root / "docs/reports/S1C_gate_report.json", public)
    numeric = report["details"]["numeric"]
    parameters = read_json(run / "numeric/momentum.json")["parameters"]
    config = read_json(root / "configs/s1c.json")
    lines = [
        "# S1-C 开阔水域物理核验收报告",
        "",
        "**本机S1-C物理核PASS；父阶段S1仍PENDING。最终head CI及ZIP以交付回执为准。**",
        "",
        f"运行：`{run.name}`；base：`{report['base_sha']}`。",
        f"内容指纹：`{report['tested_content_sha256']}`；配置SHA：`{report['config_sha256']}`。",
        f"实际被测HEAD：`{report['tested_head']}`；工作区有改动：{report['working_tree_dirty']}。",
        "最终Git SHA另列，避免提交自引用。",
        "",
        "## C01—C20",
        "",
        "|编号|检查|本机结果|",
        "|---|---|---|",
    ]
    for key, value in config["checks"].items():
        status = "PASS；最终CI/审核包另列" if key == "C20" else "PASS"
        lines.append(f"|{key}|{value}|{status}|")
    lines += [
        "",
        "## 数值与原始证据",
        "",
        f"公式复算质量：{parameters['mass_kg']} kg；"
        f"重心惯量Ix/Iy/Iz：{parameters['inertia_cg_kgm2']} kg·m²。",
        f"100组体轴导数最大差：{numeric['derivative_max']} m/s²。",
        f"100组无流RHS六通道最大差：{numeric['rhs_max_by_channel']}（前三m/s²、后三rad/s²）。",
        f"相同六通道RMS差：{numeric['rhs_rms_by_channel']}。",
        f"归一化动量残差：{numeric['momentum_normalized_max']}。",
        f"ZOH误差：{numeric['actuator_max_by_channel']}（rad/rad/rpm）。",
        "三场景三步长均为20秒；共同时间误差逐点保留在原始gate_report.json。",
    ]
    for scene in report["details"]["convergence"]:
        lines.append(
            f"场景{scene['scene']}细/粗RMS比：{scene['ratios']}；"
            f"0.02对0.005最大差：{scene['differences'][2]['max_abs']}。"
        )
    costs = report["details"]["snapshots"]
    lines += [
        f"快照实验实际积分{costs['integration_steps']}步，其中预测分支{costs['prediction_branch_steps']}步；",
        f"{costs['timed_steps']}个对照步计时p50={costs['p50_s']}s、p95={costs['p95_s']}s。",
        "每组cost.json保留热身、克隆、预测探针及四序列逐步原值；不据此承诺训练吞吐。",
    ]
    current_cases = read_json(run / "numeric/l0_l1_current.json")["cases"]
    lines += ["有流四类L1减L0连续右端差（前三m/s²、后三rad/s²）："]
    for index, row in enumerate(current_cases):
        lines.append(f"工况{index}：{row['L1_minus_L0']['nu_dot']}。")
    lines += [
        "L0局部项由只读trace从冻结函数实际返回点采集；L1物理推进不使用该诊断工具。",
        "矩阵、推进/升阻/横流、刚体/附加/阻尼/恢复/流惯性项及逐项差值均保存在原始JSON。",
        "三维烟雾图各坐标轴独立缩放，深度向下；只展示已记录轨迹，不用于几何尺寸判断。",
    ]
    baseline = read_json(run / "parent/s1b/s1a/experiments/metrics.json")
    maximum = max(
        value["max_abs"] for row in baseline["regressions"] for value in row["channels"].values()
    )
    s0 = read_json(run / "parent/s1b/s1a/s0/baseline/results/remus100/summary.json")
    lines += [
        f"本次原五种200秒逐步回归最大差：{maximum}。",
        f"本次S0：{s0['samples']}行/{s0['simulation_duration_s']}s；"
        f"深度{s0['final_depth_m']}m，航向{s0['final_heading_deg']}deg，"
        f"航程{s0['path_length_m']}m。",
    ]
    lines += [
        "100组各100步完整状态内存/JSON/分支一致；3组60秒闭环全部有限且可恢复。",
        "12个真实异常点/异常类型组合保持完整主状态；解除故障重试只推进一次。",
        f"{len(faults['cases'])}类临时坏证据通过真实CLI非零拒绝，未修改历史数据。",
        f"第一方{report['details']['tests']['total']}项通过，逐ID保留原200项；指定上游4项。",
        "一次完整R1父入口包含五种200秒逐步回归、100组原快照、B01—B22及S0默认200秒；",
        "父报告和全部CSV/JSONL位于parent/；未用旧README结果代替本次执行。",
        "",
        "## L0/L1及限制",
        "",
        "L0保持冻结；L1采用完整三维场导数、CRB(nu)、CA_red(nu_relative)及MA nu_current_dot。",
        "执行器按ZOH更新，整体仍一阶；不要求有流L0/L1整段轨迹相同。",
        "忽略压力梯度、波浪和环境旋转流载荷；制造场仅用于导数验证。",
        "尚未集成L1碰撞监控，未实现地形边界流、能耗奖励、规划或训练。",
        "",
        "## 实际命令与日志",
        "",
        "`python scripts/verify_s1c.py --source <固定来源检出>`；"
        f"原始目录`results/s1c/{run.name}/`。",
    ]
    for command in public["commands"]:
        lines.append(
            f"- `{' '.join(command['argv'])}`；退出{command['exit_code']}；"
            f"{command['duration_s']:.3f}s。"
        )
    lines += [
        "",
        "未完成项：报告生成时最终提交CI、审核ZIP及外部复核尚待交付回执确认。",
        "父阶段S1仍PENDING；停止于S1-C，不自动进入后续阶段。",
    ]
    (root / "docs/reports/S1C_验收报告.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8", newline="\n"
    )
    implementation = """# S1-C 实施记录

实际基线main=792cbf746b71792b6ea4e9eba8ba60f525b50be1；PR #3 merged=true，已审head未增加提交。
origin为Whsjbwy/wl-rl-zl，开始时工作区干净，新建codex/s1c-current-l1-core。
实际读取AGENTS.md、README、S0/S1A/S1B/S1BR1任务卡及验收报告、方法映射、来源清单、CI；
完整读取S1-C任务卡与外部复核结论，附件12项SHA校验一致，独立文件保存不覆盖历史。
读取正式DOCX第5章、6.3及13章正文，核对冻结remus100.py、gnc.py、models.py和现有快照/适配器。
任务卡是本轮授权验收依据，外部报告为既有范围审查事实，不把附件脚本当本机实测。

先登记configs/s1c.json门槛和原200项ID；新增独立海流/L1模块与透明快照。
父入口只调用R1一次，保留既有必需项和容差；新模块被递归Ruff/格式/mypy/指纹扫描。
新增本阶段实际导入路径核验、四点三类异常、语义CLI坏证据、S1C ZIP扩展及坏包测试。
冻结36文件、原控制器/依赖锁、历史报告保持原字节。没有安装训练依赖。

结果与运行日志见本次报告；早期开发检查保留results/s1c/initial-validation及development.xml。
首次静态检查的格式/类型问题已修复；不删除测试、不放宽门槛、不改旧参考结果。
审核包按最终Git blob导出，完整本机及真实CI原始证据仅在包中；脱敏只改打包副本。
"""
    (root / "docs/reports/S1C_实施记录.md").write_text(
        implementation, encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    publish(Path(__file__).resolve().parents[1], args.run.resolve(), args.source.resolve())
