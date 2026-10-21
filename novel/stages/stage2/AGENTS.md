# Stage2 事实层开发

本目录只负责对原小说的有证据理解。正式契约为
`contracts/schema/source_story.py` 的 `story-v3`，对外唯一故事产物是
`stage2/story/source_story.json`。旧协议、旧产物和旧流水线没有运行时读取或迁移入口；
旧根保持原样，新根重建。跨层规则见根 AGENTS，产品行为见
`docs/BACKEND_CONTRACT.md`，字段见 `docs/schema/str06.md`。

## 五个 owner 的边界

执行顺序为 chapters → book → 条件 associate → names → compile；节点唯一登记在
`contracts/invariants.py::NODES`。业务函数住 `story.py`，索引设施住
`novel/core/source_index.py`，副作用与编排住 `novel/stages/chain.py`。

| owner | 语义或确定性职责 | 不能承担 |
|---|---|---|
| chapters | 本章原文的局部指称、阅读序事件、局部命题与认知、因果/悬念线索；记录需上下文的问题 | 接收全书真相词表；判断未来章才揭示的身份；让孤立close在本地被误拒 |
| book | 固定一次全书语义综合；统一实体，整合命题世界状态、知情、披露、具体状态槽、时间/因果与悬念 | 暗改/补写局部事件；重新生成可继承的摘要、参与者、证据或机械编号 |
| associate | 只答book显式候选：同一性分支same/distinct；时间锚分支明确frame+候选锚；均支持source_unknown/unresolved；无候选零调用 | 正常路径重复综合全书；无证据默认合并、不同或已解决 |
| names | 对已统一人物做姓名/称呼结构解析 | 创建、删除或合并人物；决定出场或揭名时点 |
| compile | 校验输入身份和闭集，分配正式ID、显式引用解析、应用已决关联、发布SourceStory | 根据自然语言猜身份、因果、状态、命题拆分或事实正确性 |

## 不变量

- 原文证据由代码按非空自然段生成，包含章序、段序与原文；模型引用现有证据段，
  不生成路径、hash、ordinal或正式ID。
- 局部事件由章观察唯一承载。book事件映射必须恰覆盖全部现有局部事件，compiler
  从章观察继承摘要、参与者及证据；事件漏抽、错抽只能提交定位到章节、item、path、
  显式operation及原文证据的source_issue，再由该章节owner窄修。replace选择既有条目或字段，
  append选择真实容器，remove仅删除events/referents字典中的完整语义条目；删除数组或固定字段
  需显式scope不足，禁止从kind/message猜操作。发现位置不扩大修改权限。
- 实体的原文指称、故事身份、角色知情和读者揭示各自表达。未具名人物可以早于揭名
  参与事件；禁止以first_appearance或名字出现时间拒绝正确身份，禁止移为世界事实逃闸。
- 命题保留完整语义，不隐含拆分复合命题。world_status区分established/refuted/
  undetermined；角色的猜测、误信、否认和纠正不自动成为世界真相或读者确认。
- 初始知情显式使用story_start；更新可显式supersedes。具体状态槽由subjects、aspect、
  focus区分，多目标不得被同一抽象轴覆盖。过去/未来、计划/条件与未知持久性不冒充当前稳定状态。
- 悬念在全书综合时检查完整生命周期；章内孤立resolve可能依赖前章open，不得误派局部硬闸。
- source_unknown是原著材料无法确定，可保留含混；unresolved是系统尚未完成，阻断正式发布。
  机械检查通过只证明形状、引用与身份，不证明故事理解正确；未经标定语义保持unver。
- 时间通常由book完成；仅剩精确时间问题使用temporal_anchor候选。回答必须走独立tagged
  分支，只选择已给事件锚及明确frame，不以同一性same/distinct代替时间关系，不改已决时间。
- wire和观察是中间产物，Stage4只消费SourceStory；中间atlas、prompt和runtime账不得成为
  隐藏的第二份全书真相源。

## 材料、恢复与验证

章节首答只含本章原文。材料不足时经显式need_materials协议查询确定性原文索引：
章节只能读取截至本章，全书综合可全书检索。结果必须标注complete/truncated/empty、
分母与游标；空命中不证明不存在。容量预检失败时发送完整章观察索引并按需回查，
禁止截断原文字符串后冒充完整输入。所有补充材料进入请求身份，不隐藏全局状态。

纯compiler返回typed结果或带owner/item/path的结构化异常；validator不得修改输入。
下游事实修订先用正式记录与相对字段路径定位唯一writer，再以真实BookObservation和冻结wire
重编译验证后映射原语义键；不得按证据所属章或句柄中的数字猜owner/列表位置。姓名部件、
既有称谓语义段与文字字段经原names wire定位；kind改型须定位整个既有segment。
replace可精确定位一个既有称谓的分段数组，由names的typed授权执行原子重新分段，
允许段数增长或缩短；append也只能定位既有称谓分段容器。其他列表布局仍冻结，
不替换姓名整表、不删除分段列表或修改compiler派发的alias_ref，不以空text或改姓名声明绕过权限。
关联合并复合值或缺少原wire不能反猜修订地址，须明确scope不足。
同节点修复只改授权条目；上游回派只写当前story-v3/V3账并保留终身轮数与指纹。
book的source_issue回派前保存未发布的已购草稿、原响应引用和章观察绑定；章owner修复后，
仅按具体变化的引用/证据闭包发送book_repair逐项set/remove。新增关联须显式留证并登记
扩大路径，不能默认整书重做，也不能直接采用失效草稿。草稿及补丁必须能从本根耐久响应
重建；运行时记录不是跨层事实源。已解决回派的同输入重跑沿用当前修订请求身份。
免费恢复经当前请求身份的已购结果重放、纯compiler复验与补提交；尚未购买的身份
显式需要授权，不用旧账或默认值伪造采用。

定向反例在`tests/test_source_story.py`，当前持久化/恢复消费在
`tests/test_story_store.py`，真实离线全链在`tests/test_story_pipeline.py`。
测试不调用线上模型；执行前遵守`tests/AGENTS.md`的沙箱与串行槽规则。
