# S1-B-R1：监控异常事务性、验收证据复算与审核包交付

版本：1.0｜日期：2026-10-10｜目标仓库：Whsjbwy/wl-rl-zl

## 0. 本轮决定与授权

本轮是S1-B的定点修订，不是新的物理研究阶段，也不表示S1-B原有工作全部失败。已有B01—B22、159项第一方测试、4项指定上游测试，以及S0/S1-A回归证据可保留。外部审查发现两处需要补齐的异常/验收路径，并要求解释本机与CI的内容指纹差异。

仅授权：复现、修订、增加针对性测试、完整回归、提交/推送，以及生成可供审查的ZIP。不得进入S1-C、变海流、执行器修订、能耗、规划、Gymnasium任务、强化学习或蒸馏。

阶段状态分开：原S1-B历史报告保留；新报告写S1-B-R1实际结果；父阶段S1仍PENDING；外部审核尚未完成不能自行填为通过。

本次审查基准：
- PR：#3，审查时open、未合并。
- 已读head：2bac576fbca5bd55b55c68da5755fbb17b02737e。
- PR base：06abee4e5392bce63b1faa84b4b9faa55ed4a87c。
- 本机S1-B运行：20261010T084432Z-bd6122fd。
- 已读取的PR CI运行：38039119774；CI证据运行：20261010T084849Z-e5659b12。
- CI工件：11665405579，s1b-windows-evidence，30617047字节；其ZIP SHA-256：82ccbe87bf417e64e83ee8716653699b502330d76e4ae5593e5583dbb0facb3a。

这些都是历史审查锚点。执行前重新读取实际PR/head，不把旧CI套用到新提交。

## 1. 执行分支，避免重复卡在“先合并”

1. 读取AGENTS.md、适用子目录规则、S1-B任务卡、代码映射、S0/S1-A/S1-B报告、来源清单和CI；再读取本任务卡及审查探针。
2. 检查origin、当前分支、未提交修改和PR #3状态。保护用户文件，不强推、不清理未提交内容，不写旧AUVWorkPlace。
3. 如果PR #3仍开放：继续其head分支codex/s1b-terrain-collision追加修订提交并更新同一PR。此次明确不要求先合并有待修订的PR。不要创建相互重复的PR，不自动合并。
4. 如果用户已合并PR #3：从同步后的main建立codex/s1b-r1-review-fixes，再创建修订PR；不要回滚或重写已合并历史。
5. 如果远端比审查SHA更新：记录差异，先确认待修问题是否已解决；不覆盖新提交。
6. 本任务卡保存为docs/tasks/S1B_R1_定点修订与审核包.md，附加修订记录；不能覆盖S0/S1-A/S1-B原始报告。

## 2. R1：WorldMonitor.step的异常事务性（必修）

### 2.1 源码依据与问题

当前auv_env/world_monitor.py先执行self._adapter.step(reference)，再调用query_sweep。后者抛异常时，底层物理状态已经推进，last_certified/terminated/event仍是原值。调用者捕获异常再调用step时，会跳过未认证的那段运动。

审查端对这个函数体做了隔离故障注入，观察到physical_step由0变1、last_certified仍0、terminated仍false；清除故障后还可继续变2。该探针使用替身验证调用顺序，不替代真实REMUS/Windows复现。必须在你的真实仓库上再复现，并保存修复前的失败结果。

### 2.2 需要实现的语义

- 合法输入且几何查询返回有效结果时，物理与监控状态一起提交。
- 真正COLLISION、domain_boundary或UNCERTAIN是有效的监控结果：保留真实步末状态并终止，不反弹、不夹回、不回滚真实碰撞。
- 几何计算抛ValueError/FloatingPointError/RuntimeError、返回非法结构/非法状态/非有限必要数值时，不允许留下半更新状态。
- 推荐“在候选适配器推进→完成几何与结果校验→统一提交”。可用等价的可靠原子实现，但必须解释；不能悄悄忽略异常或伪造CLEAR。
- 异常时完整对外快照与调用前一致；日志可保存候选步前/步后和异常堆栈，但日志不是有效碰撞事件。不得消费或错置真实RNG。
- 同样的合法动作在故障解除后重试，只应推进一次；与没有失败的对照实例一致。
- 不改变已冻结动力学、积分步长、控制器和实际执行器限幅。

### 2.3 必需新增测试

对真实WorldMonitor分别注入query_sweep的ValueError、FloatingPointError和RuntimeError，检查完整快照不变；再检查恢复正常后仅前进一物理步，状态等于未失败的对照。

另外覆盖：Pose转换失败/非法查询返回、非法输入、正常CLEAR、真实COLLISION、UNCERTAIN、越界、已终止实例拒绝推进，以及异常后clone/JSON恢复。对真实碰撞仍要求保留已发生的步末，不得用“一律回滚”通过异常测试。

## 3. R2：验收器的独立复算和有限值（必修）

### 3.1 源码依据与问题

scripts/s1b_checks.py::inspect_results目前没有独立复算status_counts、uncertain_ratio、mean_subdivisions；读取这些键存在不等于验证了值。diagnostics虽要求长度6，但未严格校验六个(kind,intent)组合唯一且齐全；labels/subdivisions等摘要也未逐项回对原记录。

解析部分目前主要比较报告中的actual与expected是否自洽；两列同时改成相同错误数值也可能满足该段检查。gradient_max_abs_error只做“>阈值”判断，NaN或负值可能漏过这一段。

审查端已从CI原始记录独立复算本次统计，607/6/2及相应比例是相符的。这是检查器覆盖缺口，不是指控现有数据造假或宣称现有几何结果错误。

### 3.2 需要实现

1. 所有物理量、误差、比率、耗时必须有明确类型/范围与isfinite检查。误差、次数、耗时应非负；计数是整数且不接受bool冒充；比率在[0,1]。
2. JSON读取与输出均拒绝NaN/Infinity，或在任何进入PASS路径前递归拒绝。不能靠NaN比较结果为false来放行。
3. 从真实诊断JSONL中的每步sweep/query及反例原始文件独立重算status_counts、uncertain_ratio、mean_subdivisions。
4. 写清统计分母与范围。当前协议为三种地形×safe/impact的实际物理步查询，加六个合成扫掠反例；不计初态，不把neutral回归和内部递归调用混入统计。统计协议如需变更须版本化，不能静默换分母。
5. 逐段比对summary.labels、summary.subdivisions、步数、scene_hash、终止原因及终止重放事实。实际几何重放结果不能仅复制摘要标签。
6. diagnostics必须恰好包含{hills,ridge,channel}×{safe,impact}六个唯一组合；counterexamples也应检查唯一性、标签、端点是否CLEAR，而不是只检查集合或总数。
7. 解析距离按预登记独立输入重新计算expected并复算被测函数actual。保存case_id、输入和结果，避免只比较两列自报数值。梯度实验保存查询点、差分步长、解析梯度、有限差分梯度与误差，不只保存一个max。
8. validate_report中的数值details必须与原始证据重新计算得到的结果对应。不仅核对metrics.json和自身副本，也校验gate_report对应摘要。
9. 原始文件SHA核验继续保留，但哈希不能代替语义复核。错误数据自己生成一个新哈希，仍必须被数值/语义校验拒绝。

### 3.3 必需故障注入

只在临时证据副本中测试，不修改已有运行结果：
- gradient_max_abs_error改成NaN/负数；
- actual与expected同时换成错误同值；
- status_counts改错、uncertain_ratio越界或不匹配、mean_subdivisions改错；
- 单个diagnostic的labels/subdivisions篡改；
- 用重复的safe记录替换一个impact摘要，总数仍为6；
- 反例的endpoint_only_clear改错、反例重复；
- gate_report数值摘要与正确原始文件不一致；
- 缺文件、缺测试、无效JSON、非零子进程、超时继续保留。

每个故障都必须由真正的--check-report入口返回非零。对于内容篡改测试，可在临时副本更新其artifact哈希以证明错误被语义检查检出，而不只是被哈希差异拦下；这些副本明确标fault_injection，不作为正式结果。

## 4. R3：本机、提交与CI指纹对账（须解释，不预判算法代码变更）

本机报告指纹：aec4b9309b1f9a32247c38c6b65ebcb2faac31c7341ce2e34720d67ca8fd53d2。
CI报告指纹：b192b60561b40c8af02f8271d6106cb99be450248abbffd5c325612d7772137d。

已观察到差异的非Python文件：.gitattributes、.github/workflows/quality.yml、configs/s1b.json、docs/method_code_map.md、docs/schemas/S1B_gate_report.schema.json。已核对到的核心Python文件哈希一致。

审查端已经确认configs/s1b.json可由相同JSON文本的LF/CRLF换行得到两个对应哈希；因此不能把指纹不相同直接写成算法被改动。其余文件仍要逐字节解释。

要求输出fingerprint_reconciliation.json，逐项记录原始SHA、Git blob、文件大小、EOL类型、换行规范化后的SHA及实际语义diff。只读解释原证据，不能回写旧报告使两个SHA看起来一样。

可对明确的一方代码/配置/CI/新文档建立LF规则以改善之后的一致性；不得重整整个仓库或改变冻结36文件、旧任务卡及历史证据字节。最终提交与本次被测代码指纹须可追溯，若CI checkout另有原始字节差异也如实记录。

## 5. 回归与阶段准入

必须实际完成：
- 保留原159项第一方覆盖和B01—B22语义，真实统计新增测试数量；指定上游4项保留。
- S1-A五种200秒逐步对照、100组各100步的完整恢复/JSON/分支检查；原S0默认200秒回归。
- 本轮R1/R2故障注入，修复前失败与修复后通过分别留证。
- Ruff、格式、mypy、实际导入路径、来源文件、冻结依赖、历史文件及新指纹。
- 所有模块、配置、验收器和打包器进入实际检查覆盖；保留“不确定不是碰撞也不是安全”的语义。
- Windows Python3.12 CI针对最终head执行完整规定检查；CI尚未完成写PENDING。

不硬性要求不同平台的性能数字一样，不因性能变化放宽几何/数值阈值。新性能结果报告样本数和运行条件，不声称大规模训练吞吐已验证。

新报告：docs/reports/S1B_R1_验收报告.md、S1B_R1_gate_report.json、S1B_R1_实施记录.md、S1B_R1_问题闭环表.md。
新原始目录：results/s1b_r1/<run_id>/；原S1B历史结果保持原样。可复用并加强原检查器，但要记录变更和兼容性。

## 6. 本轮必须增加可复用的审核打包功能

实现并测试scripts/build_review_bundle.py及只读ZIP核验工具。功能完成后纳入本轮测试和提交，不能在测试结束后临时加入未检查的打包代码。

使用标准ZIP/DEFLATE，生成：review_packages/S1B_R1_review_<最终SHA前12位>_<run_id>.zip。
ZIP与SHA256旁文件留本地review_packages目录（加入忽略规则），不直接git add大体积ZIP。CI可以上传工件；不擅自发布公开Release。

### 6.1 审核包必须包含

建议结构（可以按清单映射，但必须真实齐全）：

```text
S1B_R1_review_<sha12>_<run_id>/
  README_先读.md
  bundle_manifest.json
  review_index.json
  missing_files.json
  source/                          # 最终提交对应的必要完整源码快照
    AGENTS.md
    README.md
    auv_env/
    scripts/
    tests/
    configs/
    .github/workflows/
    PythonVehicleSimulator/        # 36个冻结来源文件及许可证/来源说明
    run_remus.py
    run_remus.cmd
    requirements-lock.txt
    requirements-dev.txt
    pyproject.toml
    .gitattributes
    docs/                          # 必需任务卡、模式、映射、历史及新报告
  evidence/
    local/results/s1b_r1/<run_id>/  # 本次完整原始证据，保留结构
    ci/<workflow_run_id>/           # 最终提交的实际CI报告/JUnit/日志/配置/指纹
    regression/                    # 不在上层目录内的父阶段完整回归产物
    fault_injection/               # 修复前/后失败传播证据、输入、退出码
    fingerprint_reconciliation.json
  git/
    revision.json
    diff_from_reviewed_head.patch
    diff_stat.txt
    changed_files.txt
    tracked_source_manifest.json
    remote_receipt.json
  tools/
    verify_bundle.py               # 只读检查路径、CRC、必需成员与SHA
    README_复现命令.md
  packager/
    privacy_audit.json
    extraction_check.json
```

source必须来自明确记录的最终提交，不能混入未提交代码。包内有离线可读源文件，不能只放Github链接或要求审稿者自己找代码。保留上游来源/许可证，不复制.git。

本次原始证据至少包括：解析输入及结果、梯度原始数据、所有反例/六种诊断的scene和JSONL、neutral两条完整序列、真实计时数组、所有运行/异常日志、完整JUnit及参数、全部图像、100组快照的原始/恢复/JSON/分支数据、五工况CSV、原S0CSV与summary。可以引用包内路径去重，但不能只留图和PASS摘要。

父阶段数据若在本轮运行目录已经嵌套存在，不重复拷贝；review_index指向包内唯一位置。缺失数据必须列入missing_files.json，不得重新伪造旧run_id或拿别的运行悄悄替代。确需补跑，产生新run_id并记录替代关系。

CI原始工件不可用时如实标明，不能把本机数据放入ci目录充当CI。优先从最终head的GitHub Actions读取真实证据。保留工作流event/head/可能的测试合并SHA以及run_id。

### 6.2 清单、哈希与大小

bundle_manifest对每个载荷文件记录包内相对路径、来源run_id/角色、字节数、SHA256、是否脱敏、原SHA256及脱敏规则。

清单不能递归包含自身哈希：明确manifest本身不在其载荷表内；最终ZIP的SHA256放在ZIP外的同名.sha256旁文件。其他不纳入哈希的例外必须明确列举，不能任意省略。

不得把这些字段混为一谈：code_commit_sha、git_tree_sha、tested_content_sha256、config_sha256、artifact_sha256、bundle_sha256。

目标压缩后不超过150MB。超过时先只去除重复副本/无关缓存；不能截短CSV或100组恢复证据。如果仍超出，可生成多个独立、标准ZIP和共同索引，每包清单完整，不使用需要专用软件拼接的隐藏分卷格式。

### 6.3 隐私与防误打包

排除.git、.venv、__pycache__、工具缓存、.env、token、SSH密钥、凭据、浏览器资料、无关个人文件。只打包此实验必要文件，不扫描或拷贝整个用户磁盘。

日志有用户名/绝对目录时在打包副本中脱敏；原始本机记录不改。记录原hash、打包hash和路径映射，不改实验数值、单位、时间序列或错误堆栈的技术含义。

不将整个环境变量表、认证URL、私钥或访问令牌写进回执。打包工具对符号链接、路径越界、重复包内路径必须拒绝。

### 6.4 打包后必须反向验收

- ZIP CRC全部通过；无重复路径、绝对路径、..越界或未声明软链接。
- 清单载荷与ZIP实际成员一致，每文件大小和SHA匹配。
- 实际解压到一个新的临时目录，核验关键JSON/CSV/JUnit/图片可读取且必需项齐全。
- 确认source源码匹配最终提交清单和被测指纹的解释关系。
- 故意删掉一个必需文件/改一个字节/重复路径的临时包，应由verify_bundle非零拒绝。
- 不执行包内未知脚本来代替结构校验；可复现运行按README另行执行。

缺少必需证据时，可提供文件名含INCOMPLETE的诊断包，但bundle_complete=false，并明确阻塞项；不能宣布“完整审核包已生成”。

## 7. 提交、CI与最终交付顺序

1. 完成修订与打包器，跑测试，保存修复前/后证据。
2. 生成新的实验报告，核对被测源码未再变化；审查git diff后提交并推送。
3. 读取最终提交的远程SHA和CI，记录实际状态；不自动合并。
4. 从最终提交导出源码；读取选定run_id证据和CI产物；生成审核ZIP并反向核验。
5. ZIP、完整文件清单、SHA旁文件不能让报告产生提交自引用循环。提交/CI回执允许作为包内外部交付元数据，标明生成时间与所指代码提交。
6. 最后回复中文：修订点、修复前反例、实际测试命令/结果、旧回归、未完成项、PR/commit/CI、ZIP绝对路径、大小、SHA256、verify_bundle结果及已验证的复现命令。
7. 停止等待用户把ZIP交回审核，不自动开始下一物理阶段。

## 8. 放行条件

两项必修问题都有真实失败→修复→通过证据；指纹差异逐项解释；原几何及父阶段回归未退化；最终head的Windows CI通过；完整审核包可解压并核验。满足后由用户/外部审查决定是否合并与进入S1-C。

S1-C预计研究变海流接口、坐标/相对速度一致性及独立模型验证。本卡不提前授权该工作，也不要求一次实现所有海洋物理模块。
