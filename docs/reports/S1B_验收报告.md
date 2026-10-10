# S1-B 验收报告

**S1-B：PASS；父阶段 S1：PENDING。**

运行编号：`20261010T084432Z-bd6122fd`；实际base：`06abee4e5392bce63b1faa84b4b9faa55ed4a87c`。
被测HEAD：`06abee4e5392bce63b1faa84b4b9faa55ed4a87c`；测试工作区有改动：True。
被测内容SHA-256：`aec4b9309b1f9a32247c38c6b65ebcb2faac31c7341ce2e34720d67ca8fd53d2`。
配置SHA-256：`712e675c3ce7d6ae4ad870395f7393e2040cb54b39bc30f567c8786ce838f763`。
最终提交SHA、PR和CI另行交付，不将测试前HEAD伪称最终提交。

## 实现与边界

解析地形包含平底、斜面、丘陵、山脊、弯曲通道；静态球体和实体AABB；总长1.6m、半径0.095m、中心线半长0.705m的姿态胶囊。
WorldMonitor组合原适配器，每0.02s保存真实步前/步后和扫掠事件；终止不回滚、不反弹，再次step拒绝。
运动表示为位置线性+最短SO(3)；只对该几何插值认证，不声称连续动力学真解。见证时刻不是精确首次接触。
一般曲面返回有效下界或UNCERTAIN，负下界不等于碰撞；1m软裕度和1e-6m接触带分开。
冻结36文件、依赖锁、动力学、控制器与原S0/S1-A历史报告未改。未做海底绕流耦合。

## 实际测试与误差

全部第一方测试：159项；其中S1-B新增66项，旧93项覆盖保留；指定上游4项。
解析距离最大绝对误差：8.881784197001252e-16 m（门槛1e-6m）。
梯度差分最大绝对误差：2.8529845153002498e-11（步长1e-4m，atol/rtol=1e-6）。
监控中性1000步完整状态误差：0.0（门槛1e-10）；RNG与离散字段一致。

|编号|实际展开用例数|结果|
|---|---:|---|
|B01|3|PASS（JUnit全部通过）|
|B02|5|PASS（JUnit全部通过）|
|B03|3|PASS（JUnit全部通过）|
|B04|1|PASS（JUnit全部通过）|
|B05|3|PASS（JUnit全部通过）|
|B06|1|PASS（JUnit全部通过）|
|B07|5|PASS（JUnit全部通过）|
|B08|8|PASS（JUnit全部通过）|
|B09|2|PASS（JUnit全部通过）|
|B10|1|PASS（JUnit全部通过）|
|B11|1|PASS（JUnit全部通过）|
|B12|1|PASS（JUnit全部通过）|
|B13|1|PASS（JUnit全部通过）|
|B14|2|PASS（JUnit全部通过）|
|B15|1|PASS（JUnit全部通过）|
|B16|3|PASS（JUnit全部通过）|
|B17|6|PASS（JUnit全部通过）|
|B18|1|PASS（JUnit全部通过）|
|B19|2|PASS（JUnit全部通过）|
|B20|2|PASS（JUnit全部通过）|
|B21|6|PASS（JUnit全部通过）|
|B22|8|PASS（JUnit全部通过）|

## 碰撞反例与不确定

|合成几何用例|预登记|实测|两端均为CLEAR|
|---|---|---|---|
|thin|COLLISION|COLLISION|True|
|rotation|COLLISION|COLLISION|True|
|safe|CLEAR|CLEAR|True|
|budget|UNCERTAIN|UNCERTAIN|True|
|negative_bound|UNCERTAIN|UNCERTAIN|False|
|terrain_witness|COLLISION|COLLISION|False|

反例标签错分数：0；预登记安全/碰撞反例无误报或漏报，UNCERTAIN单列。
薄障碍及原地旋转反例均找到中途接触见证；这些给定端态不冒充物理可达航行。
三类固定开发场景各运行200步安全轨迹及真实物理撞击诊断；全部保存完整快照，终止重放一致。
诊断与反例统计：{'CLEAR': 607, 'COLLISION': 6, 'UNCERTAIN': 2}；UNCERTAIN比例=0.003252，平均细分次数=0.024390。
这些是已登记诊断输入的分类，不是导航成功率；预期不确定单独统计，不当碰撞或安全。

## 实测成本

|操作|样本数|p50(ms)|p95(ms)|最大(ms)|
|---|---:|---:|---:|---:|
|static|12|0.077350|15.299755|15.428400|
|swept|6|0.401900|38.593525|51.282200|
|monitor|609|2.912200|4.426280|6.081200|

CPU单环境；计时原值保留，不据小样本宣称实时保证。

## 原阶段完整回归

S1-A五种各200秒/10001行：最大误差0.0；100组内存/JSON/分支重放均通过。
S0：10001点/200.0秒；深度29.74975474011636m，航向49.998898184993784度，航程453.5508894768102m。
Ruff/格式/mypy覆盖新增目录；来源、依赖、导入路径、历史哈希及被测指纹通过。

## 实际执行命令

- `& '.\.venv\Scripts\python.exe' '-m' 'scripts.s1b_parent' '--source' '<source_checkout>' '--output' '.\results\s1b\20261010T084432Z-bd6122fd\s1a'`；退出0；73.355s。
- `& '.\.venv\Scripts\python.exe' '-m' 'scripts.s1b_experiments' '--output' '.\results\s1b\20261010T084432Z-bd6122fd\experiments'`；退出0；6.163s。
- `& '.\.venv\Scripts\python.exe' '-m' 'scripts.demo_s1b_scene' '--input' '.\results\s1b\20261010T084432Z-bd6122fd\experiments' '--output' '.\results\s1b\20261010T084432Z-bd6122fd\figures'`；退出0；2.277s。
- `& '.\.venv\Scripts\python.exe' '-m' 'ruff' 'check' 'auv_env' 'scripts' 'tests'`；退出0；0.200s。
- `& '.\.venv\Scripts\python.exe' '-m' 'ruff' 'format' '--check' 'auv_env' 'scripts' 'tests'`；退出0；0.204s。
- `& '.\.venv\Scripts\python.exe' '-m' 'mypy' 'auv_env' 'scripts'`；退出0；1.578s。
- `& '.\.venv\Scripts\python.exe' '-m' 'pytest' 'tests' '-q' '--junitxml=.\results\s1b\20261010T084432Z-bd6122fd\s1a\unit.xml'`；退出0；9.543s。
- `& '.\.venv\Scripts\python.exe' '-m' 'scripts.s1a_s0' '--source' '<source_checkout>' '--output' '.\results\s1b\20261010T084432Z-bd6122fd\s1a\s0'`；退出0；22.897s。
- `& '.\.venv\Scripts\python.exe' '-m' 'scripts.s1a_experiments' '--output' '.\results\s1b\20261010T084432Z-bd6122fd\s1a\experiments'`；退出0；28.653s。
- `& '.\.venv\Scripts\python.exe' '-m' 'scripts.demo_s1a_replay' '--input' '.\results\s1b\20261010T084432Z-bd6122fd\s1a\experiments\snapshots' '--output' '.\results\s1b\20261010T084432Z-bd6122fd\s1a\replay.png'`；退出0；0.912s。
- `& '.\.venv\Scripts\python.exe' '-m' 'ruff' 'check' 'scripts' 'tests'`；退出0；0.191s。
- `& '.\.venv\Scripts\python.exe' '-m' 'ruff' 'format' '--check' 'scripts' 'tests'`；退出0；0.190s。
- `& '.\.venv\Scripts\python.exe' '-m' 'mypy' 'scripts'`；退出0；0.848s。
- `& '.\.venv\Scripts\python.exe' '-m' 'pytest' 'tests' '-q' '--junitxml=.\results\s1b\20261010T084432Z-bd6122fd\s1a\s0\unit.xml'`；退出0；9.805s。
- `& '.\.venv\Scripts\python.exe' '-c' 'import importlib; print(importlib.import_module(''python_vehicle_simulator.vehicles.remus100'').__file__)'`；退出0；0.995s。
- `& '.\.venv\Scripts\python.exe' '-m' 'pytest' 'PythonVehicleSimulator/tests/test_propeller_advance_speed.py' 'PythonVehicleSimulator/tests/test_crossflow_drag.py' 'PythonVehicleSimulator/tests/test_simulate.py::test_remus100' '-q' '--junitxml=.\results\s1b\20261010T084432Z-bd6122fd\s1a\s0\upstream.xml'`；退出0；3.703s。
- `& '.\.venv\Scripts\python.exe' 'run_remus.py' '--no-animation'`；退出0；3.834s。
- `& '.\.venv\Scripts\python.exe' '-m' 'pip' 'check'`；退出0；1.538s。

原始轨迹/快照/事件/日志：`results/s1b/20261010T084432Z-bd6122fd/`。
脱敏证据与图：`docs/reports/evidence/s1b-20261010T084432Z-bd6122fd/`。
三张场景图包含真实尺寸胶囊、海底、实体和事件；曲线明确是扫掠下界。绘图只读原始记录。

## 未运行项及限制

未开展正式封存集、变海流、模型/执行器修订、能耗、动态障碍、规划或强化学习训练；未测GPU/CUDA。
胶囊未有CAD包络证明；保守梯度界可能造成UNCERTAIN，停止原因是uncertified_clearance，不伪称碰撞。
B01—B22通过只说明本轮几何监控验收，父阶段S1仍PENDING。完成后停止，等待用户审核。
