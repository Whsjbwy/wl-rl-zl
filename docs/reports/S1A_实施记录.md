# S1-A 实施记录

本轮只接受 S1-A 授权，不自动进入后续子任务或合并 PR。

## 实际读取的材料

- 根目录 `AGENTS.md`（适用指令，未发现下级覆盖文件）。
- `README.md`、`docs/tasks/S0_首次迁入与回归验收.md`。
- `docs/reports/S0_验收报告.md`、根 `gate_report.json`。
- `docs/provenance/source_manifest.json`、`.github/workflows/quality.yml`。
- 用户附件《S1A_单步仿真与完整状态快照_任务卡_V1.md》，逐字节保存为
  `docs/tasks/S1A_单步仿真与完整状态快照.md`。原任务卡的 PR 未合并描述是历史信息，执行前已重新核验。
- 用户提供的《AUV_实验实施方案_仓库审计版_V1.docx》可读正文；本轮范围见方法映射。
- 冻结源码 `mainLoop.py`、`remus100.py`、`gnc.py`、`control.py`、`guidance.py`。

## 仓库与基准

开始时目标工作区无未提交改动，origin 为 `https://github.com/Whsjbwy/wl-rl-zl.git`。
GitHub PR #1 确认 merged=true 后 fetch，main 快进到
`fff2f2f7aba3c4251b5684090417f1d680b23201`，再创建 `codex/s1a-step-snapshot`。
其文件树与 S0 交付提交 `6ebb95e885201df3c48452f1269cb813365b2547` 相同。
本次核对执行 `git status --short`、`git remote -v`、`git branch --show-current`；
开发基准与同步后的提交树已核验。
PR/CI 通过 GitHub 连接器读取，真实响应归纳于 `S0_交付回执.json`。

## 门槛与执行

先登记 `configs/s1a.json` 的五种200秒场景、100组恢复与误差阈值，再运行实验。
首次完整运行 `20261010T072733Z-ed337747` 通过，原始数据保留。
随后修复非法假值配置被默认值掩盖、超大步数时间溢出的输入边界问题，
补充回归测试及缺失测试/哈希错误的 CLI 非零传播测试。
第二次运行 `20261010T072859Z-86b14563` 全部通过，共93项第一方测试。
用户任务卡含原有 Markdown 硬换行空格，单独设置字节保留和对应空白检查属性，
不修改附件文本；最终被测指纹包含该属性文件。
最终验收运行、全部命令/退出码/数值见 `S1A_验收报告.md` 与 `S1A_gate_report.json`。

完整轨迹及快照位于被忽略的 `results/s1a/<run_id>/`，提交小型脱敏证据和哈希。
S0 历史报告不回填修改；来源36文件、冻结依赖和原回归入口保持不变。
本轮没有新增训练依赖。模型仍确定性运行，RNG 测试仅验证实例状态恢复与隔离。

最终提交前统一新增 Python 文件为 LF（匹配 Git 属性），完整重跑编号为
`20261010T073420Z-dbe842ec`，15项门槛、7条主命令及嵌套S0检查均通过。
此前运行及发布预览保留在各自原始目录，不作为最终提交指纹。
