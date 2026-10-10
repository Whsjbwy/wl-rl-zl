# wl-rl-zl

本仓库为 AUV 研究开发仓库。S0 基线来自
`Whsjbwy/AUVWorkPlace@c2dfbd4f4425d29bdcba98294563cda19890cdb1`，
保留六自由度动力学、深度/航向控制器及原入口，不宣称上游为原创。
原仓库 README 保留于 [来源说明](docs/provenance/source_readme.md)，
上游 MIT 许可见 [LICENSE](PythonVehicleSimulator/LICENSE)。
上游提交记录为 `a93d1938ba1f78f2580c24c73f003091b2f03cd0`；本次校验锚定固定来源 Git 树，
没有另行声称已比对整个上游仓库。

## 当前范围

S0 已迁入冻结基线；S1-A 已交付；当前仅实施获授权的 S1-B：静态几何与艇体碰撞监控。
当前真实结果见 [S0 验收报告](docs/reports/S0_验收报告.md) 和 [gate_report.json](gate_report.json)。
`examples/` 的图片和 summary 是来源演示产物，不能当成本次实测。
`docs/governance/` 是任务包的历史制作核对记录，其中的通过和权限状态不是本次运行状态。
未实现强化学习、蒸馏、规划或 C1/C2/C3；S1-B 通过不代表整个 S1 已验收。

## 安装与完整验收

在仓库根目录 PowerShell，用 Python **3.12** 创建独立环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt -r requirements-dev.txt
git clone --no-checkout https://github.com/Whsjbwy/AUVWorkPlace.git ../s0-source
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe scripts/verify_s0.py --source ../s0-source
```

`--source` 是只读取源的临时 clone，不指向或修改用户旧工作区；入口校验固定提交和 Git blob。
每次验收在 `results/s0/<唯一run_id>/baseline/` 复制冻结文件并执行原入口，
实际仿真参数为 200 秒、0.02 秒步长、30 米深度、50 度航向、1525 rpm、
0.5 米/秒海流和 170 度流向。输出 CSV/summary/图及 stdout/stderr 独立保存，失败也保留。
验收失败返回非零，绝不将短仿真替代完整回归。报告记录每个命令、耗时、状态及内容哈希。

生成可提交的脱敏中文报告（将 `<run_id>` 替换为实际目录名）：

```powershell
.\.venv\Scripts\python.exe scripts/publish_s0.py --run results/s0/<run_id> --source ../s0-source
```

原始结果留在被 Git 忽略的 `results/`，只提交小型脱敏证据及哈希清单。
原 `run_remus.py` 会按自身位置覆盖 `results/remus100/`，重复实验应使用上面的隔离入口。
保留 `run_remus.cmd` 但本次未验证双击；未运行动画及 GPU/CUDA 测试。

## 检查工具与 CI

`requirements-lock.txt` 原样保留。第一方开发工具单列于 `requirements-dev.txt`：
Ruff 0.13.3（MIT，规则与格式）、mypy 1.18.2（MIT，接口类型）、
jsonschema 4.25.1（MIT，阶段报告结构）。实际传递依赖版本在报告记录。
规则应用于 `auv_env/`、`scripts/` 和 `tests/`，不格式化冻结上游。

```powershell
.\.venv\Scripts\python.exe -m ruff check auv_env scripts tests
.\.venv\Scripts\python.exe -m ruff format --check auv_env scripts tests
.\.venv\Scripts\python.exe -m mypy auv_env scripts
.\.venv\Scripts\python.exe -m pytest tests -q
```

`.github/workflows/quality.yml` 配置 Windows/Python 3.12 的 S1-B、完整 S1-A 和嵌套 S0 检查，
任务分支推送或 PR 触发，只读权限并归档失败证据。配置存在不等于 CI 已通过，实际状态以 GitHub 运行为准。

## S1-A 单步、快照与完整验收

公共接口为 `auv_env.RemusAdapter`、`RemusConfig`、`ControlReference`；
`step()` 推进一个 0.02 秒物理步，`get_state()` 返回独立快照，
`set_state()` 原子恢复完整状态，`clone()` 建立互不污染的分支。
坐标采用 NED，位置为米、速度为米/秒、角度为弧度、转速为 rpm。
积分和原日志对齐见 [方法与代码映射](docs/method_code_map.md)。
快照包含执行器、七个控制器内部状态、控制参考、步数、配置标识及 PCG64 状态。
读取快照和离线绘图均不推进仿真。

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe scripts/verify_s1a.py --source ../s0-source
```

该命令在唯一 `results/s1a/<run_id>/` 执行五种 200 秒逐步对照、
100 组各 100 步内存/JSON 恢复、分支隔离、五种重置、失败传播测试及原 S0 完整回归。
阈值先登记在 `configs/s1a.json`；任何必需检查失败即返回非零。
原始 CSV、完整状态 JSONL、日志和哈希留在该目录，S0 历史报告不覆盖。

```powershell
.\.venv\Scripts\python.exe scripts/publish_s1a.py --run results/s1a/<run_id> --source ../s0-source
.\.venv\Scripts\python.exe scripts/verify_s1a.py --check-report results/s1a/<run_id>/gate_report.json
```

本次实测见 [S1-A 中文报告](docs/reports/S1A_验收报告.md) 和
[结构化报告](docs/reports/S1A_gate_report.json)。演示图由本次快照恢复序列生成，
不使用旧 README 图或旧仿真数据。整个 S1 尚未验收，后续工作须单独授权。


## S1-B 三维地形与艇体碰撞监控

`Scene`保存解析海底、静态球体/AABB及独立生成种子；`query_pose`检查完整姿态胶囊，
`query_sweep`检查相邻物理步的线性位置+最短SO(3)插值；`WorldMonitor`组合原适配器，
遇实体接触、操作越界或无法认证即终止，保存真实步前/后，不回滚位置。
CLEAR/COLLISION/UNCERTAIN分开；下界不冒称精确净空，1m软裕度不是碰撞半径。
完整约定见方法映射与S1-B任务卡。

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe scripts/verify_s1b.py --source ../s0-source
```

一键入口在唯一 `results/s1b/<run_id>/` 执行B01—B22、原阶段完整回归、
开发诊断与只读绘图；任何必需检查失败返回非零。使用三类固定开发图，未生成封存测试集。
再次只读演示（输出目录须不存在）：

```powershell
.\.venv\Scripts\python.exe -m scripts.demo_s1b_scene --input results/s1b/<run_id>/experiments --output results/s1b/<run_id>/replay_figures
.\.venv\Scripts\python.exe scripts/publish_s1b.py --run results/s1b/<run_id> --source ../s0-source
```

实际结果见[中文S1-B报告](docs/reports/S1B_验收报告.md)及[结构化报告](docs/reports/S1B_gate_report.json)。
胶囊是协议几何近似，未验证海底绕流或完整海洋物理；未实现自主避障，不进入后续训练。

## S1-B-R1 定点修订与审核包

本轮只修订监控异常事务性、验收器独立复算和证据交付；父阶段S1及外部复审仍PENDING。
异常候选不提交；真实碰撞、越界与UNCERTAIN照常提交步末并终止。历史报告不覆盖。

```powershell
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe scripts/verify_s1b_r1.py --source ../s0-source
.\.venv\Scripts\python.exe scripts/publish_s1b_r1.py --run results/s1b_r1/<run_id> --source ../s0-source
.\.venv\Scripts\python.exe scripts/build_review_bundle.py --run results/s1b_r1/<run_id> --before <修复前失败目录> --ci <最终CI下载目录> --receipt <最终远端回执.json>
.\.venv\Scripts\python.exe scripts/verify_bundle.py review_packages/<实际包名>.zip --extract review_packages/<新的核验目录>
```

打包前要求干净最终提交并核对远端SHA；ZIP及旁哈希不提交Git、不发布Release。
完整100组恢复、原阶段CSV、JSONL、JUnit、故障日志、CI、来源与许可证均进入审核包。
命令中的尖括号需替换为真实路径；本机实测命令/结果见S1B_R1报告和最终交付回执。
