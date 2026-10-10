# wl-rl-zl

本仓库为 AUV 研究开发仓库。S0 基线来自
`Whsjbwy/AUVWorkPlace@c2dfbd4f4425d29bdcba98294563cda19890cdb1`，
保留六自由度动力学、深度/航向控制器及原入口，不宣称上游为原创。
原仓库 README 保留于 [来源说明](docs/provenance/source_readme.md)，
上游 MIT 许可见 [LICENSE](PythonVehicleSimulator/LICENSE)。
上游提交记录为 `a93d1938ba1f78f2580c24c73f003091b2f03cd0`；本次校验锚定固定来源 Git 树，
没有另行声称已比对整个上游仓库。

## 当前范围

只实施 S0：冻结迁入、来源校验、完整默认回归、验收脚本自检与 CI 配置。
当前真实结果见 [S0 验收报告](docs/reports/S0_验收报告.md) 和 [gate_report.json](gate_report.json)。
`examples/` 的图片和 summary 是来源演示产物，不能当成本次实测。
`docs/governance/` 是任务包的历史制作核对记录，其中的通过和权限状态不是本次运行状态。
未实现强化学习、蒸馏、地形、碰撞、C1/C2/C3；不会自动进入 S1。

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
规则仅应用于 `scripts/` 和 `tests/`，不格式化冻结上游。

```powershell
.\.venv\Scripts\python.exe -m ruff check scripts tests
.\.venv\Scripts\python.exe -m ruff format --check scripts tests
.\.venv\Scripts\python.exe -m mypy scripts
.\.venv\Scripts\python.exe -m pytest tests -q
```

`.github/workflows/quality.yml` 配置 Windows/Python 3.12 的完整 S0 检查，
任务分支推送或 PR 触发，只读权限并归档失败证据。配置存在不等于 CI 已通过，实际状态以 GitHub 运行为准。
