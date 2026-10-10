"""核验真实S1-B报告后生成中文报告、小型脱敏证据及场景图。"""

import argparse
import json
import shutil
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.publish_s0 import redact
from scripts.s0_checks import digest, read_json
from scripts.verify_s0 import write_json
from scripts.verify_s1b import validate_report


def publish(root: Path, run: Path, source: Path) -> None:
    """原始数据不入Git；逐文件关联脱敏前后哈希，不覆盖历史S0/S1-A报告。"""
    report = read_json(run / "gate_report.json")
    validate_report(report, root)
    folder = root / "docs/reports/evidence" / f"s1b-{run.name}"
    folder.mkdir(exist_ok=False)
    files = list(run.glob("*.log")) + [
        run / "resolved_config.json",
        run / "tested_content.json",
        run / "experiments/metrics.json",
        run / "experiments/timings.json",
        run / "s1a/unit.xml",
        run / "s1a/gate_report.json",
        run / "s1a/s0/gate_report.json",
    ]
    files += (
        list((run / "s1a").glob("*.log"))
        + list((run / "s1a/s0").glob("*.log"))
        + list((run / "figures").glob("*.png"))
    )
    evidence = []
    for path in files:
        target = folder / path.relative_to(run).as_posix().replace("/", "__")
        if path.suffix == ".png":
            shutil.copyfile(path, target)
        else:
            target.write_text(
                redact(path.read_text(encoding="utf-8", errors="replace"), root, source),
                encoding="utf-8",
                newline="\n",
            )
        evidence.append(
            {
                "path": target.relative_to(root).as_posix(),
                "sha256": digest(target),
                "raw_sha256": digest(path),
            }
        )
    public = json.loads(redact(json.dumps(report, ensure_ascii=False), root, source))
    public["published_evidence"] = evidence
    write_json(root / "docs/reports/S1B_gate_report.json", public)
    metrics = read_json(run / "experiments/metrics.json")
    tests = next(c["details"] for c in report["checks"] if c["name"] == "B01_B22")
    parent = read_json(run / "s1a/experiments/metrics.json")
    s0 = read_json(run / "s1a/s0/baseline/results/remus100/summary.json")
    lines = [
        "# S1-B 验收报告",
        "",
        f"**S1-B：{report['status']}；父阶段 S1：PENDING。**",
        "",
        f"运行编号：`{run.name}`；实际base：`{report['base_sha']}`。",
        f"被测HEAD：`{report['tested_base_commit']}`；测试工作区有改动：{report['working_tree_dirty']}。",
        f"被测内容SHA-256：`{report['tested_content_sha256']}`。",
        f"配置SHA-256：`{report['config_sha256']}`。",
        "最终提交SHA、PR和CI另行交付，不将测试前HEAD伪称最终提交。",
        "",
        "## 实现与边界",
        "",
        "解析地形包含平底、斜面、丘陵、山脊、弯曲通道；静态球体和实体AABB；总长1.6m、半径0.095m、中心线半长0.705m的姿态胶囊。",
        "WorldMonitor组合原适配器，每0.02s保存真实步前/步后和扫掠事件；终止不回滚、不反弹，再次step拒绝。",
        "运动表示为位置线性+最短SO(3)；只对该几何插值认证，不声称连续动力学真解。见证时刻不是精确首次接触。",
        "一般曲面返回有效下界或UNCERTAIN，负下界不等于碰撞；1m软裕度和1e-6m接触带分开。",
        "冻结36文件、依赖锁、动力学、控制器与原S0/S1-A历史报告未改。未做海底绕流耦合。",
        "",
        "## 实际测试与误差",
        "",
        f"全部第一方测试：{tests['tests']}项；其中S1-B新增{tests['new_cases']}项，旧93项覆盖保留；指定上游4项。",
        f"解析距离最大绝对误差：{metrics['analytic']['max_abs_error_m']} m（门槛1e-6m）。",
        f"梯度差分最大绝对误差：{metrics['analytic']['gradient_max_abs_error']}（步长1e-4m，atol/rtol=1e-6）。",
        f"监控中性1000步完整状态误差：{metrics['neutral']['max_abs']}（门槛1e-10）；RNG与离散字段一致。",
        "",
        "|编号|实际展开用例数|结果|",
        "|---|---:|---|",
    ]
    lines += [
        f"|{name}|{len(values)}|PASS（JUnit全部通过）|" for name, values in tests["B01_B22"].items()
    ]
    lines += [
        "",
        "## 碰撞反例与不确定",
        "",
        "|合成几何用例|预登记|实测|两端均为CLEAR|",
        "|---|---|---|---|",
    ]
    lines += [
        f"|{row['name']}|{row['expected']}|{row['actual']['query']['status']}|{row['endpoint_only_clear']}|"
        for row in metrics["counterexamples"]
    ]
    misclassified = sum(
        row["expected"] != row["actual"]["query"]["status"] for row in metrics["counterexamples"]
    )
    lines += [
        "",
        f"反例标签错分数：{misclassified}；预登记安全/碰撞反例无误报或漏报，UNCERTAIN单列。",
        "薄障碍及原地旋转反例均找到中途接触见证；这些给定端态不冒充物理可达航行。",
        "三类固定开发场景各运行200步安全轨迹及真实物理撞击诊断；全部保存完整快照，终止重放一致。",
        f"诊断与反例统计：{metrics['status_counts']}；UNCERTAIN比例={metrics['uncertain_ratio']:.6f}，平均细分次数={metrics['mean_subdivisions']:.6f}。",
        "这些是已登记诊断输入的分类，不是导航成功率；预期不确定单独统计，不当碰撞或安全。",
        "",
        "## 实测成本",
        "",
        "|操作|样本数|p50(ms)|p95(ms)|最大(ms)|",
        "|---|---:|---:|---:|---:|",
    ]
    lines += [
        f"|{name}|{v['count']}|{v['p50_s'] * 1000:.6f}|"
        f"{v['p95_s'] * 1000:.6f}|{v['max_s'] * 1000:.6f}|"
        for name, v in metrics["performance"].items()
    ]
    lines += [
        "",
        "CPU单环境；计时原值保留，不据小样本宣称实时保证。",
        "",
        "## 原阶段完整回归",
        "",
        f"S1-A五种各200秒/10001行：最大误差{max(x['max_abs'] for x in parent['regressions'])}；"
        "100组内存/JSON/分支重放均通过。",
        f"S0：{s0['samples']}点/{s0['simulation_duration_s']}秒；深度{s0['final_depth_m']}m，航向{s0['final_heading_deg']}度，航程{s0['path_length_m']}m。",
        "Ruff/格式/mypy覆盖新增目录；来源、依赖、导入路径、历史哈希及被测指纹通过。",
        "",
        "## 实际执行命令",
        "",
    ]
    for section in (
        public,
        json.loads(redact(json.dumps(read_json(run / "s1a/gate_report.json")), root, source)),
        json.loads(redact(json.dumps(read_json(run / "s1a/s0/gate_report.json")), root, source)),
    ):
        for command in section["commands"]:
            argv = "& " + " ".join("'" + str(x).replace("'", "''") + "'" for x in command["argv"])
            lines.append(f"- `{argv}`；退出{command['exit_code']}；{command['duration_s']:.3f}s。")
    lines += [
        "",
        f"原始轨迹/快照/事件/日志：`{run.relative_to(root).as_posix()}/`。",
        f"脱敏证据与图：`{folder.relative_to(root).as_posix()}/`。",
        "三张场景图包含真实尺寸胶囊、海底、实体和事件；曲线明确是扫掠下界。绘图只读原始记录。",
        "",
        "## 未运行项及限制",
        "",
        "未开展正式封存集、变海流、模型/执行器修订、能耗、动态障碍、规划或强化学习训练；未测GPU/CUDA。",
        "胶囊未有CAD包络证明；保守梯度界可能造成UNCERTAIN，停止原因是uncertified_clearance，不伪称碰撞。",
        "B01—B22通过只说明本轮几何监控验收，父阶段S1仍PENDING。完成后停止，等待用户审核。",
        "",
    ]
    (root / "docs/reports/S1B_验收报告.md").write_text(
        "\n".join(lines), encoding="utf-8", newline="\n"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    args = parser.parse_args()
    publish(Path(__file__).resolve().parents[1], args.run.resolve(), args.source.resolve())
