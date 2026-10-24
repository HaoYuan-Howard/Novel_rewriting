# Stage2 人物绑定与认知边界修复执行计划

## 一、目标与交付

保持现有心智模型：Characters 负责人物身份与开场基线；Blueprint 记录事件及其引起的状态、认知和悬念变化。保留现有节点及职责，不增加第三份故事真源或独立 Judge。必要的字段扩展、协议升版和下游修改在本批完成。

验收分为工程正确性和真实 Stage2 质量：前者验证契约、生产、编译、恢复及写手材料消费链；后者重跑原问题小说，分别核读首答、返修和正式产物。

## 二、契约与行为修正

### 人物身份与称谓

- 删除人物出场不得早于最早称谓章的接受及最终编译硬闸；称谓窗口继续约束名称使用。
- 现有人物条目增加 `identity_bindings`，记录匿名或歧义指称的描述、出场证据段及同一性证据段，普通明确指称允许空数组。
- Book 判断人物同一性；compiler 只验证人物闭集、证据定位和引用闭合。出场统计从章观察派生，不由姓名首现推算。
- 章节人物材料携带本章相关指称与内部人物 ID，后文身份衔接证据不成为本章人物或读者已经知情的依据。
- 同一个“孩子”允许指向不同人物，不增加全书唯一的通用婴儿别名；身体、血缘等事实保留实际主体。

### 命题词表与证据

- `facts` 保持共享命题词表职责，一项只表达一个可独立确认或否定的主张，不把病因、送来者、运输方式拼成一个命题。
- 命题证据记录 `confirmed / claim / inference`、正负极性、相关人物及证据段，记录源文如何表达命题，不登记全书永久真假。
- `known_at_start` 改为带持有者和极性的记录，只表达开场已经确认的知识。每项须有同持有者、同极性的 confirmed 证据；文字证据可来自后文回述。compiler 派生 Characters 的 `initial_knowledge`，经 Preserve 的 `k0_*` 锚点和 Brief 初始锁送达目标初态，不能仅停留于 fold 读数。
- 章节词表投影提供命题及本章相关证据性质，后章反证不倒灌为本章确知。
- 只有完整语义等价才复用编号或接受 associate 的 `same`；部分支持、因果关联、否定关系不得合并。
- 原子性、证据性质及叙述确认由语义单元判断，compiler 不按关键词拆句或判真伪。

### 认知与误信纠正

- 认知增加显式 `op=set|unset`；set 保留现有六种模式并携带极性，unset 撤回当前接受，模式和极性为空。
- 增加证据性质和 `reader_visible`，线上显式返回，不用缺省字段控制流程。
- 仅有推测或未经确认的说法不得使用 `learns/confirms`；对话是否承担真实揭示仍由语义抽取依据上下文判断。
- 纠正时保留早期相信的历史，在纠正拍撤回原认知，再记录有支持的新认知；撤回不代表世界命题为假，不删除曾经听到或说过的历史。
- 单纯否认先记录为负极性说法，不自动修改其他人物知识或世界事实。

### 悬念与读者知情

- Effect 增加读者可见标记；拍级 access 只表达呈现通道，不承担真实性或完整披露判断。
- close 选择本拍具体答案原子；wire 用 change／know 加条目序号引用，拍级位置使用独立 scene_location 选择器，compiler 转正式引用并派生收束文本。
- 答案必须是已成立且向读者展示的事实或有确认依据的认知；猜测只进认知，悬念继续开放。
- 删除 close.resolution 无条件派生 reader learns 的规则。
- 读者确知从明确展示的已确认原子派生并保留极性；展示说法只产生说法层记录；人物怀疑、相信或撤回不自动成为读者确认。
- Wire 继续禁止自填 reader，正式读者认知由 compiler 确定性派生。

## 三、实施顺序与消费闭环

1. 建立匿名出场、复合命题、误信纠正和假收束反例，修改观察 wire、正式 schema、展开／逆映射及证据解析。
2. 更新 Stage2 提示与材料、关联候选、接受判据、最终编译、fold 和修订定位；语义自核留在现有生产职责内。
3. Stage4 命题锁、逐次认知变化和 Guard 身份携带操作与极性。目标知情用 set＋true/false 表达正负，用 unset 表达撤回。写手材料明确呈现“相信 P／相信非 P／已撤回”，公开说法保留极性。
4. 免费重验、人工采用、已购重放和断点补提交复用同一接受规则，同步受影响 API、只读投影、前端类型和 fixture。
5. 受影响的观察、Characters、Blueprint、Preserve、Design 和 renderer 显式升版，同步产物和 LLM 执行版本。旧版本拒识、旧根只读、新根重建，不提供跨版本迁移或缓存冒用。

修复归属：人物同一性和词表归 Book，章内事实／认知／悬念归 Chapters，关联选择归 Associate，机械判据错误归代码。不得通过删除真实参与者、改挂 world 或吞掉未决项满足检查。

## 四、工程验证

先定向反例与契约检查，再跑真实 producer／consumer、恢复测试；本批共享 schema、状态投影和 renderer 跨阶段修改完成后，运行一次默认非 corpus 回归。前端变化补 test、typecheck、seams 和 build。

- 匿名先出场、后命名：人物身份和事实主体不变，姓名与身份揭示时间仍受约束。
- 两婴儿共用“孩子”：身份不合并，无效人物／证据引用准确拒绝。有效人物间的误绑保留为语义反例，不冒充机械可证。
- 部分支持不得整体接受复合命题；同义、包含、否定分别处理。
- believes(P) → unset(P) → learns(非P) 保留历史并更新当前认知；单纯否认不自动完成此链。
- 假答案不能关闭悬念或派生确知；真答案正常收束。
- 第一人称推测、隐藏答案、极性经过锁、Design、IR、正文及 Hook 材料不失真。
- 旧版本拒绝、当前版本免费重放／采用／中断恢复不重复购买或发布半份真源。

新增 HARD 同时提供红绿例、路由及免费重验入口。未经标定的语义判断保持 unver。测试统一使用 novel_refactor，按仓内要求绕开 pytest 文件系统沙箱，不并行启动共享测试根的批次。报告命令、基线、通过／失败／skip 和 JUnit 分母。

## 五、真实 Stage2 验证与验收

源文件为 `E:/practice_group/Novel_rewriting/Test_data/Workbench/夫君不行_但我有孕啦_2062949822106228156.txt`，由 `NOVEL_CORPUS=E:/practice_group/Novel_rewriting/Test_data/Workbench` 显式定位：22 章，SHA-256 为 817ff838156baeab8d8858180136d7af67782a2eb4208f744cfed101c20eeba2。
`NOVEL_OUTPUT` 默认使用独立根 `E:/practice_group/Novel_rewriting/outputs/stage2_semantics_verify_20261022`，不覆盖源文或已有输出。

- 使用现有确定性分章和开发输入通路，同一个 Stage2 引擎完整执行 Book、全部章节、Names、Associate、Compile，止于 Stage2。
- 默认 DeepSeek Flash、high、thinking on，调用前冻结无密钥配置、输入摘要和请求清单；沿用现有有界返修及单元／整书额度，不自动追加。
- 工程验证和具体预览完成后核对付费、正文外发两份授权，缺一则停在真实调用前。
- 保留首答、拒绝、返修和正式产物，分别报告首答机械接受率、首答语义质量、返修后质量。
- 重点核读第 7、8、10、11、17 章及相关人物和命题的全部引用，并通读词表及所有章观察检查回归。
- 婴儿身份／事实主体保留；中毒、送来者、迷晕分别有据；误信及行动动机保留，后文纠正完整，错误答案不进入世界事实或读者确知。
- 最后免费重验，确认零新购及正式盘面、派生结果一致。

真实失败保留证据并回通用规则修复，人工补正不能代替首答验收。交付代码、本文、工程结果、真实核读结论及成本账引用。单书不外推总体错误率，不宣称成书质量已验收。

## 接口落实约定

- 极性统一取 positive／negative；证据性质统一取 confirmed／claim／inference。
- identity_bindings 条目使用 description、appearance_refs、identity_refs；字段属于人物身份，引用范围由代码验证，不作为称谓释放证据。
- Fact.evidence 为带 kind、polarity、holder、basis 的证据项，wire 的 basis 改为 evidence_refs；known_at_start 条目为 holder＋polarity。
- Cognition 的 op、mode、polarity、support、reader_visible 均显式返回；unset 的 mode／polarity 为 null，basis 保留撤回依据。
- close 的 wire answer_refs 为 kind(change／know)＋index(1-based) 的数组；拍级位置以 kind=scene_location、index=1 独立选择，不占 change 索引，且必须存在并向读者展示。观察展开为本拍 eN／cN，正式编译转为 bNN_NN.eN／cN；resolution 由答案原子派生，不接受模型自由填入答案文本。位置展开与逆映射同批闭合，不以伪造人物知情替代位置答案。
