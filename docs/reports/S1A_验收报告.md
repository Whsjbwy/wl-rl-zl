# S1-A 验收报告

**S1-A：PASS；父阶段 S1：PENDING，尚未整体验收。**

运行编号：`20261010T073420Z-dbe842ec`；任务卡V1.0。
实际开发base：`fff2f2f7aba3c4251b5684090417f1d680b23201`。
被测基准HEAD：`fff2f2f7aba3c4251b5684090417f1d680b23201`；工作区有未提交改动。
被测内容SHA-256：`8881971d6a69aa2769fbb360f11b854969a43ec19ec46531a19714518a797e84`。
解析配置SHA-256：`83889619a301c68abff5165daae41ce0ecc2fbf43b19de42382a88d77af75e37`。
最终Git SHA和CI结果另见交付回复，避免报告自引用。

## 实现范围

实现不可变配置、reset/step/get_state/set_state/clone、完整控制器与PCG64快照、JSON恢复和独立分支。
冻结36文件原样保留；未改动力学、控制参数、积分、固定海流或限幅。
原S0根报告与文字报告哈希保持不变，新增S0交付回执连接真实PR/合并/CI。

## 五种完整逐步对照

| 工况 | 记录数 | 物理时长(s) | 全通道最大绝对差 |
|---|---:|---:|---:|
| default | 10001 | 200.0 | 0.0 |
| no_current | 10001 | 200.0 | 0.0 |
| east_current | 10001 | 200.0 | 0.0 |
| west_current | 10001 | 200.0 | 0.0 |
| vary_reference | 10001 | 200.0 | 0.0 |

每通道max_abs/RMS及单位见metrics.json与method_code_map.md。
状态/执行器/指令使用atol=rtol=1e-10；时间≤1e-9秒；角度比较原始弧度。
真实实例10000步停在200秒；末行指令只在克隆预览，不多推进主实例。

## 快照、分支与重置

完成100组，每组100步内存恢复与磁盘JSON恢复。
内存恢复最大绝对差：0.0。
JSON恢复最大绝对差：0.0。
分支后继续运行最大绝对差：0.0。
每组验证主环境/A/B隔离、外部快照修改、读取/保存无副作用，计数、标识、RNG状态精确一致。
五种工况重置各100步，最大绝对差：0.0。
完整状态序列和分支前后快照保存在原始目录，不仅保存末值。

## 原S0与检查器

本次原S0：10001点/200.0秒；深度29.74975474011636m，航向49.998898184993784度，航程453.5508894768102m。
全部第一方测试实际收集93项；指定上游4项；Ruff/格式/mypy/pip check通过。
故障注入覆盖真实子进程非零/超时、必需测试缺失、证据哈希不符、轨迹扰动、JSON不完整。

## 实际命令

- `& '.\.venv\Scripts\python.exe' '-m' 'ruff' 'check' 'auv_env' 'scripts' 'tests'`；退出0，耗时0.197秒。
- `& '.\.venv\Scripts\python.exe' '-m' 'ruff' 'format' '--check' 'auv_env' 'scripts' 'tests'`；退出0，耗时0.197秒。
- `& '.\.venv\Scripts\python.exe' '-m' 'mypy' 'auv_env' 'scripts'`；退出0，耗时1.555秒。
- `& '.\.venv\Scripts\python.exe' '-m' 'pytest' 'tests' '-q' '--junitxml=.\results\s1a\20261010T073420Z-dbe842ec\unit.xml'`；退出0，耗时7.283秒。
- `& '.\.venv\Scripts\python.exe' '-m' 'scripts.s1a_s0' '--source' '<source_checkout>' '--output' '.\results\s1a\20261010T073420Z-dbe842ec\s0'`；退出0，耗时17.892秒。
- `& '.\.venv\Scripts\python.exe' '-m' 'scripts.s1a_experiments' '--output' '.\results\s1a\20261010T073420Z-dbe842ec\experiments'`；退出0，耗时28.114秒。
- `& '.\.venv\Scripts\python.exe' '-m' 'scripts.demo_s1a_replay' '--input' '.\results\s1a\20261010T073420Z-dbe842ec\experiments\snapshots' '--output' '.\results\s1a\20261010T073420Z-dbe842ec\replay.png'`；退出0，耗时0.970秒。

原始记录：`results/s1a/20261010T073420Z-dbe842ec/`。
脱敏证据：`docs/reports/evidence/s1a-20261010T073420Z-dbe842ec/`。
实际导入路径、硬件/依赖、命令及全部原始产物哈希见S1A_gate_report.json。

## 未运行项与边界

未实施/验收地形、碰撞、变海流、能耗模型、Gymnasium导航任务、教师或强化学习训练。
未运行GPU/CUDA、动画和.cmd双击；S1-A不把这些未授权内容写成通过。
当前动力学确定性，RNG实验只验证状态独立与恢复。原上游限幅后更新的潜在越界行为未改。
按任务卡完成后停止，后续子任务须另行授权。
