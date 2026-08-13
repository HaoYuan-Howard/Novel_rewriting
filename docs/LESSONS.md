# 工程经验与踩坑

本文件只保留可迁移的经验，不记录进度、待办或某次提交做了什么。每条都包含结论、
失败形态和本仓落点；总数固定不超过 20。

## 1. 约束只有落到可执行守卫上才算存在

写在 prompt、文档或注释里的“必须”没有遵守率。每条约束都要能回答：谁在产出、谁在检查、
失败怎样显式呈现。无法自动判断的语义也要明确写成“人判断”或“不可验证”，不能留一盏假绿灯。

落点：`contracts/rules.py`、`contracts/judges/`、`contracts/schema/report.py`。

## 2. 绿灯必须说明自己实际检查了什么

空输入、被上游修复过的输入、从未执行的判据都能稳定产出“0 问题”。报告至少要带扫描量、
分母或 unver；测试不仅要见过通过，还要有一份最小反例证明它真的会红。

落点：`contracts/schema/report.py` 的扫描字段，以及各 stage 判据测试。

## 3. 守卫的扫描面也需要守卫

参数化列表只覆盖“尚未移植的层”、fixture 只在某台机器存在、import 失败被 skip，
都会让测试随着开发推进静默缩小。扫描面应从现役注册表正向派生，并断言数量只按预期变化。

落点：`tests/test_coverage.py`、`tests/test_docs_references.py`、`tests/test_layering.py`。

## 4. “不知道”“没运行”和“没问题”必须分开建模

缺上游、产物损坏、判据无输入、服务重启后的 running、空数组和真通过不是同一种状态。
不要靠空值和默认布尔量承载这些语义；状态必须进入 schema，并在 API 与界面逐值保留。

落点：`contracts/schema/report.py`、`contracts/schema/orchestration.py`、
`novel/orchestrator/state.py`。

## 5. 少量盘上样本不能单独决定契约

“三份样本都有值”不等于字段必填，“三份样本只有一个取值”也不等于枚举只有一项。
契约要同时看生产代码、消费代码、较大语料和人工编辑/归档路径。Pydantic 结构化响应应显式
`extra='forbid'`；需要区分缺席与 null 时，不要用默认值或全局 `exclude_none` 抹平。

落点：`docs/schema/_README.md`、`tools/derive_schema.py`、`contracts/schema/`。

## 6. 删字段前先审计语义消费，不只 grep 字面量

字段可能通过整份 dict 迭代、复制、别名或模型决策间接消费；零字面命中不是零消费。
依次确认：它是否恒等于常量、能否零成本现算、消费方读的是字段还是那个常量、失败成本归谁。
派生值不落盘；常量回到单一落点；仍有语义消费就不能删。

落点：`contracts/invariants.py` 与 `tests/corpus.py` 的字段审计。

## 7. 单一落点要覆盖渲染结果，不只覆盖模板源码

枚举、词表、区间和规则只能定义一次，prompt 通过插值取得。模板逐字相同仍不够：
插入的常量可能被转述、漏项或被调用点同名参数覆盖，所以要钉最终发出的字节。

落点：`novel/llm/prompts.py`、各 stage 的 `prompts.py` 与 prompt 回归测试。

## 8. 归一、人工写回和提升必须与生成共享落盘口

落盘前修复若不记原值与修复账，报告无法区分“修好了”和“从未检查”。人工稿若绕过生成稿的
校验、轮次归档或 provenance，就会制造一条更宽松的隐藏通道。所有写回应复用同一闸门，
只把来源标成 human/free/paid 的不同。

落点：`novel/store.py`、`novel/prov.py`、`docs/schema/_common.md`。

## 9. 文档引用使用稳定锚点，不使用行号

文件会改名、需求会拆分、行号一定漂移，而 Markdown 不会为悬空引用报错。引用应写成
“仓内路径 + 稳定符号/编号”，并由测试验证路径和编号存在；失效名称作为例子时不要伪装成引用。

落点：`tests/test_docs_references.py`。

## 10. 证据基线、现役 fixture 与 mock 是三种不同资产

证据基线用于证明契约来源，现役 fixture 用于回归当前实现，mock 用于模拟服务行为。
用新后端输出覆盖证据基线，会让测试拿实现与自己比较；让真实模式回退到 mock，又会把缺失端点
伪装成成功。三者必须分目录、分版本、分消费入口。

落点：`docs/design_brief/05_samples/`、`web/src/mocks/`、
`web/scripts/check-seams.mjs`。

## 11. 前端接口声明不等于真实链路存在

TypeScript 能证明 mock 与 real 适配器同型，却不能证明 URL 已挂载、状态码语义正确或写操作真的
落到服务端。联调必须建立“Api 方法 → HTTP 路由 → 服务实现 → 页面入口”矩阵，并在关闭 mock 后
逐项走真实请求。

落点：`web/src/types/api.ts`、`web/src/api/endpoints.ts`、`novel/api/routes/`。

## 12. 前端只呈现判定，不补后端没给的结论

费用、写盘、新鲜度、严重度、阈值和闸门 DAG 一旦在前端重算，就会出现第二个判官。
服务端没给结论时应显示读数或 unknown；历史 mock 的兼容逻辑只能留在 mock 适配边界。

落点：`web/src/constants/`、`web/src/api/`、`contracts/schema/api.py`。

## 13. 没有 ground truth 时，多一条语义判据可能更危险

语义探针在少量样本上命中“真问题”不代表它判的是自己名字所说的东西。先标定命中与漏检，
再决定它是 gate、info、metric 还是 unver。差异跨书巨大时只报分布，不为了“更安全”强行定线。

落点：`novel/core/measure.py` 与各 stage 的 measure/checks 分层。

## 14. 探针要在标定后命名，迁移理由要在新上下文重证

名字会诱导人按它解释数字；复制来的理由则比复制来的代码更难报错。迁移时逐条验证输入、
消费方和失败成本是否仍成立，先写中性现象名，完成标定后再命名或定严重度。

落点：`contracts/judges/` 中 rule、detail、sev 的分离。

## 15. 产生面删除，判定面退役并标注

废弃字段、prompt 指令和可选值应从产生面彻底删除，让旧形状无法再构造；判据、坏数据和旧结论
不能静默消失，否则报告会把“没检查”画成“没问题”。判定面进入可执行的退役状态，并写清
撤销原因与复活条件。

落点：`contracts/rules.py` 的退役机制、`tests/corpus.py` 的字段裁剪清单。
