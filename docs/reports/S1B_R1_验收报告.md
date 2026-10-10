# S1-B-R1 验收报告

**本机S1-B-R1：PASS；父阶段S1：PENDING；外部复审：PENDING。**

本次运行：`20261010T103455Z-09f2f8e6`。审查锚点：`2bac576fbca5bd55b55c68da5755fbb17b02737e`。
被测HEAD：`1edf3ba044912cf6226c4ebba8bd5235c1f3298a`，测试时工作区有改动：True。
内容指纹：`bb52841b09218223856e7c19e102e24a65d1378c7dd31feefe591a66fa0e80b4`；配置原始SHA：`c36641109c903af47373c36f8e0123f5a32870235f9e6f5b6c74615ac72625a2`。
最终提交/远程/CI及ZIP SHA在交付回执中分开记录，测试前HEAD不冒称最终提交。

## 问题闭环

修复前真实Windows探针12项失败：三类几何异常半更新，九类坏摘要未被拒绝。
证据run=20261010T095423Z-0a5a96b0，完整前后快照、重试状态、堆栈及JUnit单独保留。
R1候选物理步/几何结构验证/统一提交；异常保留全快照，真实COLLISION/UNCERTAIN/越界仍提交步末并终止。
R2固定输入重新计算手算expected和实际actual；保存梯度点位/步长/解析/差分；严格有限值和类型。
从六个反例及六条诊断的每个真实步复算计数/比例/细分，初态、中性对照和内部query_count不入分母。
对未改原始证据及其临时副本先执行正向CLI，再21类篡改并同步哈希；全部由真实--check-report退出1拒绝。
R3五文件均可由Git blob转CRLF精确重建CI记录SHA；规范化内容一致，未重写旧报告。

## 实际回归

第一方200项通过，逐ID保留原159项，新增41项；指定上游4项通过。
B01—B22保留；R1完整异常对照3类；语义CLI拒绝21类。
解析距离最大误差8.881784197001252e-16 m；梯度差分误差2.8529845153002498e-11。
监控中性完整状态误差0.0；五种200秒逐步回归最大误差0.0。
100组各100步内存/JSON恢复及分支全部通过；每组完整五份证据保留。
S0：10001点/200.0s；深度29.74975474011636m，航向49.998898184993784度，航程453.5508894768102m。
真实统计：{'CLEAR': 607, 'COLLISION': 6, 'UNCERTAIN': 2}；不确定比例0.0032520325203252032；平均细分0.024390243902439025。
Ruff/格式/mypy、导入、来源36文件、依赖锁、pip check、历史哈希和指纹均通过。

## 实际命令

- `.\.venv\Scripts\python.exe -m scripts.s1b_r1_parent --source <source_checkout> --output .\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b`；退出0；100.639s。
- `.\.venv\Scripts\python.exe -m scripts.s1b_r1_atomic --output .\results\s1b_r1\20261010T103455Z-09f2f8e6\atomic`；退出0；0.982s。
- `.\.venv\Scripts\python.exe -m scripts.s1b_r1_faults --run .\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b --output .\results\s1b_r1\20261010T103455Z-09f2f8e6\fault_injection`；退出0；95.109s。
- `.\.venv\Scripts\python.exe -m scripts.s1b_parent --source <source_checkout> --output .\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b\s1a`；退出0；74.151s。
- `.\.venv\Scripts\python.exe -m scripts.s1b_experiments --output .\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b\experiments`；退出0；6.679s。
- `.\.venv\Scripts\python.exe -m scripts.demo_s1b_scene --input .\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b\experiments --output .\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b\figures`；退出0；2.297s。
- `.\.venv\Scripts\python.exe -m ruff check auv_env scripts tests`；退出0；0.172s。
- `.\.venv\Scripts\python.exe -m ruff format --check auv_env scripts tests`；退出0；0.172s。
- `.\.venv\Scripts\python.exe -m mypy auv_env scripts`；退出0；0.713s。
- `.\.venv\Scripts\python.exe -m pytest tests -q --junitxml=.\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b\s1a\unit.xml`；退出0；12.410s。
- `.\.venv\Scripts\python.exe -m scripts.s1a_s0 --source <source_checkout> --output .\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b\s1a\s0`；退出0；22.942s。
- `.\.venv\Scripts\python.exe -m scripts.s1a_experiments --output .\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b\s1a\experiments`；退出0；27.801s。
- `.\.venv\Scripts\python.exe -m scripts.demo_s1a_replay --input .\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b\s1a\experiments\snapshots --output .\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b\s1a\replay.png`；退出0；0.952s。
- `.\.venv\Scripts\python.exe -m ruff check scripts tests`；退出0；0.180s。
- `.\.venv\Scripts\python.exe -m ruff format --check scripts tests`；退出0；0.191s。
- `.\.venv\Scripts\python.exe -m mypy scripts`；退出0；0.777s。
- `.\.venv\Scripts\python.exe -m pytest tests -q --junitxml=.\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b\s1a\s0\unit.xml`；退出0；12.346s。
- `.\.venv\Scripts\python.exe -c import importlib; print(importlib.import_module('python_vehicle_simulator.vehicles.remus100').__file__)`；退出0；0.823s。
- `.\.venv\Scripts\python.exe -m pytest PythonVehicleSimulator/tests/test_propeller_advance_speed.py PythonVehicleSimulator/tests/test_crossflow_drag.py PythonVehicleSimulator/tests/test_simulate.py::test_remus100 -q --junitxml=.\results\s1b_r1\20261010T103455Z-09f2f8e6\s1b\s1a\s0\upstream.xml`；退出0；2.536s。
- `.\.venv\Scripts\python.exe run_remus.py --no-animation`；退出0；2.788s。
- `.\.venv\Scripts\python.exe -m pip check`；退出0；1.524s。

## 证据与交付边界

原始数据：`results/s1b_r1/20261010T103455Z-09f2f8e6`。
小型脱敏证据：`docs/reports/evidence/s1b-r1-20261010T103455Z-09f2f8e6`。
审核ZIP从最终提交导出源码，装入本机完整原始证据及真实CI下载，随后CRC/哈希/解压反向验收。
验收与打包器缺文件/篡改/重复/越界/软链接失败传播均有测试；最终ZIP大小/SHA/校验结果另见交付回执。
原159项覆盖及历史结果保留；未放宽阈值，未改变冻结动力学、控制器或依赖锁。
未实施S1-C、变海流、能耗、规划、训练、封存集、GPU/CUDA；未进行CAD包络证明。
既有Actions Node弃用警告不因此升级；后续合并和外部复审由用户决定。
