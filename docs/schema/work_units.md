# 当前工作单元、预算与发布证据

## 身份与当前输出根

输出根由 `contracts/schema/workflow.py::WorkflowManifest` 标识，必填当前schema_version=1、contract_version=story-v1、pipeline_contract_epoch=story-v1、task_id和created_at。只接受当前格式；旧根、旧产物和旧账原字节保留，在独立新根重建，不提供读迁移或同根升级。

每本书的CandidateWorkflow绑定当前契约表、源文件字节身份source_identity、source_root_sha1及规范原文source_content_sha256。runtime/source_snapshot.json必须可重算，且本根Stage1存在时与正式源输入逐章及raw双hash一致。契约或源身份漂移拒绝当前根；代码修复可以滚动epoch，既有工作单元与费用不重置。

工作单元唯一身份由task_id、书sid、node、role与scope派生。当前语义role为executor；逐章scope为chapter:N，全书scope为book。一个全书整合工作单元可以含合法材料续答和窄修，每次实际送达都独立留证和计费，不新增隐形工作单元。

## 额度与授权

当前工作单元账为WorkUnitLedgerV3，位置由 `novel/paths.py::work_unit_dir` 计算。基础内容轮四次；网络attempt与内容轮分开。新run、epoch、会话、输入或模型变化不重置业务额度。显式节点协议/政策/判据版本变更由 `contracts/llm_versions.py` 登记，同一版本同步幂等，unknown、未闭合回答和整书总闸仍阻止发出请求。旧V2账不被当前运行时转换或返还额度。

BookContentBudgetV1位于_service/runs/<sid>/book_budget.json，对全部工作单元合账设绝对上限。只有reserve计内容轮，网络重试共享reservation；缓存重放和确定性补提交不追加内容调用。追加必须给actor/reason及不可变授权，不能由自动恢复暗自追加。

新购同时需要付费和正文外发授权。CLI分别为--yes与--allow-egress；上传入口的全自动授权由运行代码冻结后传递。免费--check-only不授予新购；缓存缺失则needs_authorization。kind:legacy是当前直连LLM的稳定wire名称，与退役兼容无关。

成本唯一权威为_service/ledger.jsonl，attempt逐行关联原请求、模型、用量、网络结果与费用。缓存行单列；未知用量不作零；reasoning不重复相加。

## 响应、接受与封存

`novel/llm/paid_requests.py`只管许可、持久额度、外发、原响应与来源；`novel/llm/story_requests.py`处理JSON、材料控制与有界字段补丁。原响应先按attempt耐久，随后才解析。空答在同一内容轮内有界重试；完整reasoning可按同一schema/compiler救回，但原网络error和原usage不得改写。unknown与截断不自动重购。

AcceptanceRecordV1显式response_kind=result/material/patch/format_failure，parse、salvage、errors、accepted与semantic=unver分别记录。每attempt的首次结论冻结于first_acceptance，免费复验只更新最新接受记录。合法material不算语义候选通过或失败，仍计真实调用成本。读数器 `tools/story_metrics.py` 保留未知分母，不把回放接受率当首答率。

正式输出只有当前repository发布口。CommitBundleV1保存输入、采用的真实attempt、输出字节摘要和seal；同writer输出组先完整落字节，再封存。SourceStory与TargetStory各唯一发布，章正文与回执、最终TXT与manifest按组发布。无覆盖当前字节的seal即不是正式输入；文件存在、运行退出码和文本可读均不能代替证明。

中断后同请求身份的合法已购回答免费重放，再编译、验证与补封存；不直接接纳未封存正文，不复制跨根候选或伪造来源。补丁保留初始生成来源，另记实际窄修request/response/base/result摘要。

## 已知范围

机械通过不证明源语义理解或文学质量。只有相应golden set与人工评价能够提供质量证据，未经标定保持unver。普通测试不调用线上模型，不覆盖真实成书质量。
