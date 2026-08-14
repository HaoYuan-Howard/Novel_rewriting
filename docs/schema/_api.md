# HTTP 响应（`/api/**`）

**载体已定：Pydantic v2**——但本文只定**内容**，模型照它写，**方向别搞反**。
所以下面的表里不出现任何 Python 类型标注。

⇒ `docs/schema/_api.md` ↔ `contracts/schema/api.py`，文件名一一对应
（同 `_common.md` ↔ `common.py`、`_report.md` ↔ `report.py`、`_orchestration.md` ↔ `orchestration.py`）。

## 本文**不定**路由表

路由表的唯一落点是 `docs/design_brief/04_delivery.md` §路由表，那张表的标题逐字写着
「**固定，不要改**」，前端已经照它接完了 14 屏。**在这里再抄一份就是第二个落点**，
而两份路由表分家的表现是：前端按 A 请求、后端按 B 注册，双方都不出声，只有 404。

本文定的是那张表右边那一列的**响应长什么样**，以及三条跨端点的纪律。

## 「证据」列

与 `_README.md` 那一列同义，但本文的三源不是「盘上/契约/代码」，而是：

| 取值 | 含义 |
|---|---|
| **`抓包`** | `docs/design_brief/05_samples/*.json` 里有它，形状逐字对得上。**那是现役原型后端的真实响应** |
| **`派生`** | 由已有模型或已有纯函数算出来的，**不是一件新事实**（改它等于改上游） |
| **`设计`** | 新形状，**零样本**——还没有任何东西验证过它 |

⚠⚠ **`05_samples/*.json` 永远不许用新后端的输出覆盖。** 它是 `tests/test_api_enums.py`
与 `tests/test_invariants.py` 全部对账的**证据基线**；覆盖之后那一族测试就变成
拿 `contracts/invariants.py` 跟它自己比——而「有测试」与「测试在查东西」的分界线就在这里。

---

## 一、出线纪律（三条，跨全部端点）

**① 键恒在，`null` 就写 `null`，不用 `exclude_none`。**
与 `novel/store.py::ArtifactStore._render` 的房规逐条相同（`by_alias=True` · 键恒在 · `None` 写成 `null`）。

> ⚠ `_orchestration.md` 与 `contracts/schema/orchestration.py` 里有几处写着
> 「`None` = 键不在」——**那说的是盘上那一份产物的纪律，不是 HTTP 出线的**。
> 两者今天不冲突（产物里省掉的键，视图层根本不会去构造），
> 但读起来会让人以为 HTTP 也该 `exclude_none`。**在这里一次说死：不。**
> 理由是消费方不同：产物的读者是下一层代码（键不在 ⇒ 它没发生过）；
> 响应的读者是**界面**，而界面要区分「这个字段是 null」与「后端这版根本没有这个字段」。

**② `by_alias=True` 不许关。**
`_provenance` 是 alias 字段（模型里叫 `provenance`）。FastAPI 的
`response_model_by_alias` 一旦被关掉，响应里那个键会**静悄悄**变成 `provenance`，
而前端读 `_provenance` 拿到 `undefined`——不报错，只是那一格空了。
⇒ 配一条测试：S-06 的响应 JSON 里必须有字面量 `_provenance` 这个键。

**③ 措辞不是契约，id 才是（`Q-6`）。**
凡是「为什么」类的格子一律出 **id**（异常类名 / `FRESHNESS_REASON` / 判据 id），
不出人读的句子。人读的句子可以**另外**出一格，但前端不许拿前缀去认它——
`check_stage5_review.json` 的 `info` 桶靠 `〔度量〕` 前缀区分度量与告警，
一次措辞改动就会把 41 条度量全部渲染成告警色。

---

## 二、`/api/enums` **刻意没有响应模型**

它的键集就是它的内容（由 `contracts/invariants.py` 的公开常量派生；键数随公开取值面增长，
不作为协议常量）。
写成一个五十来个字段的模型，等于把「自动发布」重新变回一份**手写清单**——
而那正是 `novel/api/enums.py` 开头论证过要消灭的东西：

> 手写**发布清单**会**静默地烂**（新常量前端拿不到 ⇒ 前端自己硬编码一份，没人出声）；
> 手写**不发登记表**会**响亮地烂**（新常量落不进任何桶 ⇒ 当场抛 `ValueDomainRefused`）。

⇒ 它的守卫是**超集测试**（`tests/test_api_enums.py`），不是类型。
**这一行写在这里，是为了下一个人不会来「补齐」它。**

Prompt 选型完成后，`prompt_variants`、`prompt_defaults` 与 `--prompt-variant` 已从公开契约删除；
Stage 3/5 各只有一条生产 prompt 路径，前端和调用方无需也不能选择实验臂。

三个合成键与抓包的关系：

| 键 | E 期的变化 | 依据 |
|---|---|---|
| `invent` | 无（`allow`/`forbid`/`principle` 逐值相同） | `抓包` |
| `job_kinds` | 键从原型 12 个 `n1..n9` 换成**节点 id**，值加 5 格（`stage`/`section`/`modes`/`flags`/`produces`） | `docs/BACKEND_CONTRACT.md`「命名体系」第 5 条：形状不变、只有 `kind` 的取值变 |
| `actions` | `kind` 取值同上；**新增** `mode` / `writes` / `writes_disk` / `needs_value` 四格 | 同上；`writes_disk` 是前端在 `web/DELIVERY.md` 里点名要的那一格 |

⚠ `job_kinds` 里的 `paid` 说的是**默认路径**贵不贵，**不许拿它做贵贱对账**。
真正的贵贱由 `(node, flags)` 决定，唯一落点 `novel/orchestrator/nodes.py::is_paid`（`R-11.7`），
出口在 `actions[]`。实测：抓包的 `check_blueprints` 是 `paid=false`，
而它映到的 `stage2.blueprint` 节点 `paid=true`——那不是不一致，
是原型把复检做成了独立 kind，而两层命名下它是一个 flag。

---

## 三、端点 → 响应

**当前落地的二十五个**（其余端点见 §七）：

| 端点 | 响应 | 新旧 |
|---|---|---|
| `GET /api/enums` | 无模型（见 §二） | 新 |
| `GET /api/novels` | `NovelList` | 新 |
| `GET /api/novels/:sid` | `NovelOverview` | **复用** `_orchestration.md` |
| `GET /api/novels/:sid/stage3/style` | `StyleView` | 新 |
| `GET /api/novels/:sid/check/:kind` | `CheckReport` | **复用** `_report.md` |
| `GET /api/jobs?sid=` | `JobList` | 新 |
| `GET /api/jobs/:id` | `JobRecord` | **复用** `_orchestration.md` |
| `GET /api/jobs/:id/log?offset=N` | `LogChunk` | 新 |
| `POST /api/jobs/preview` | `JobStart`（请求体 `PreviewRequest`） | **复用** `_orchestration.md` |
| `POST /api/jobs` | `JobRecord`（202；请求体 `PreviewRequest`） | **复用** `_orchestration.md` |
| `POST /api/upload?filename=` | `UploadResult`（请求体为原始字节） | 新 |
| `GET /api/novels/:sid/stage0/report` | `RemovalReport` | **复用** `stage0.md` |
| `GET /api/novels/:sid/stage0/segments` | `Stage0Segments` | 新 |
| `POST /api/novels/:sid/stage0/edit` | `JobRecord`（请求体 `Stage0EditRequest`） | 新 |
| `POST /api/novels/:sid/stage0/approve` | `JobRecord` | 新 |
| `POST /api/novels/:sid/stage1/partition` | `JobRecord` | 新 |
| `GET /api/novels/:sid/stage1/segments` | `Stage1Segments` | 新 |
| `GET /api/novels/:sid/stage1/chapters` | `Stage1Chapters` | 新 |
| `GET /api/novels/:sid/stage1/hook_copy` | `HookCopyView` | 新 |
| `GET /api/novels/:sid/approvals` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/gate/:key` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/seen/:key` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/deep` | `ApprovalDoc`（请求体 `DeepSelectionRequest`） | 新 |
| `POST /api/novels/:sid/approvals/writing` | `ApprovalDoc` | 新 |
| `POST /api/novels/:sid/approvals/reset` | `ApprovalDoc` | 新 |

⚠ **preview 直接返回 `JobStart` 是有意的**，不是图省事：那个模型自带后置条件
`paid == 任一步 paid` / `writes == 任一步 writes`，于是「整份贵贱是逐步折叠出来的」
这条 `R-11.7` 白拿；而且**真跑时 `jobs.start()` 构造的就是这个类**——
「预览与真跑是同一个对象」从一句话变成字面事实（`R-11.8`）。

通用启动返回 202 和 `running` 作业，由后台线程逐步启动 CLI 子进程。同一 `sid` 的书级
作业互斥并返回 409，不同小说可并行；Stage5 复合写作在拿到书级键后才原子核对并锁定
人工深度选章，避免“预览一种分配、实际写另一种分配”。

⚠ **一个响应模型都不许自己算判定**（`R-11.4`）。`badge` / `passed` / `counts`
只出现在 `CheckReport` 里，且由 `contracts/schema/report.py::badge_of` 折叠、
再由 `CheckReport` 的后置条件反过来断言——**服务端不重算，前端更不许算**。

---

## 四、`StyleView` —— S-06 文风那一屏

来源：抓包 `docs/design_brief/05_samples/stage3_style.json`（真实响应，8 章那本）。

⚠ **它是一个【视图】，不是产物。** 盘上那份 `stage3/style_grounding.json` 只贡献右半边；
左半边（实测数字与档位词）是**代码现算的**，产物在不在都算得出来
——`04_delivery.md:123` 明写「S-06 文风：`exists: false` 时左半边（实测）仍有内容」。

| 字段 | 类型 | 必填 | 取值面 / 说明 | 证据 |
|---|---|---|---|---|
| `sid` / `title` | 串 | 是 | **由 `novel/paths.py` 从目录名派生** | `派生` |
| `exists` | 布尔 | 是 | 盘上有没有那份产物 | `抓包` |
| `invalid_exists` | 布尔 | 是 | 有没有一份 `.INVALID` 在等人处理 | `抓包` |
| `readable` | 布尔 | 是 | 能不能按**当前** schema 读回来 | `设计` |
| `voice` | 数组 | 是 | 八项 `{key, text, chars}`，**顺序 = `VOICE_KEYS`** | `抓包` |
| `surface_habits` | 串数组 | 是 | 产物在时非空；不在时空数组 | `抓包` |
| `hook_style` | 数组 | 是 | 七项 `{key, text}`，**顺序 = `HOOK_KEYS`** | `抓包` |
| `hook_copy_text` | 串 或 null | 是 | 开篇文案段原文（来自 stage1，与本层产物无关） | `抓包` |
| `measurable_stored` / `measured_now` | 对象 或 null | 是 | `Measurable`。前者来自产物，后者**现算** | `抓包` |
| `bands_stored` / `bands_now` | 对象 或 null | 是 | 档位词。**现算，不落盘**（`stage3.md`：两个消费方各算一次） | `抓包` |
| `measure_error` | 串 或 null | 是 | 量不出来时的**人话**。与 `measured_now` **恰好一个非空** | `抓包` |
| `measure_error_kind` | 串 或 null | 是 | **异常类名**（`UpstreamMissing` / `ArtifactUnreadable`） | `设计` |
| `drift` | 数组 **或 null** | 是 | 存/现两份实测的逐项差异 | `抓包`（形状）+ `设计`（三态） |
| `freshness` / `reason` / `ghosts` | 串 / 串 / 串数组 | 是 | `FRESHNESS` / `FRESHNESS_REASON` / 幽灵边**点名** | `设计` |
| `_provenance` | 对象 或 null | 是 | 产物里那一份，原样 | `抓包` |

### 七条形状裁决（相对抓包）

**① `voice_extra` 删掉。** 抓包里有这个数组（值是 `[]`），新契约里**没有对应的东西**：
`contracts/schema/stage3.py::Voice` 是八键闭合 + `extra='forbid'`，`StyleGrounding` 里
根本没有这个字段 ⇒ 它**结构上恒为空**。留着就是一盏保证不亮的灯
（同 stage4 那个恒为 `keep` 的 `op`）。**登记在这里，不是漏了。**

**② 算不出来时不 404、不抛，填槽。** 抓包里已经预留了 `measure_error` / `bands_error`，
那就是答案。三态由后置条件焊死：`measured_now` 与 `measure_error` **恰好一个非空**，
同空或同非空一律炸。
⚠ 另出一格 `measure_error_kind`（**id 不是措辞**）：
「stage1 还没跑」与「stage1 的产物坏了」对人的下一步是**相反**的指示。

**③ `drift` 是 `数组 | null`，不是 `数组`。**
`[]` = 「两份实测比过了，没有漂移」；`null` = 「有一边算不出来，**无从比**」。
抓包里是 `[]`，把两者压成一个 `[]` 就回到本项目一路在防的那个病
（`Q-2`：「不知道」与「没问题」必须能区分）。

**④ `voice` / `hook_style` 保持【数组】形态，且顺序由 `VOICE_KEYS` / `HOOK_KEYS` 给**，
配后置条件逐个断言。发成对象的话，顺序就落在**字段声明顺序**上——那是第二个落点，
而界面上八维的顺序是有意义的（`narrative_distance` 打头）。

**⑤ `bands_error` 也删掉，与 ① 是同一类。**
`novel/core/bands.py::bands()` 在一个**合法的** `Measurable` 上是**全函数**
（每一项都是纯算术与查表，没有失败支）。⇒ `bands_now` 为 null 当且仅当
`measured_now` 为 null，而那件事已经由 `measure_error` 说过了。
一个只可能与另一格说同一件事的格子，就是**同一条信息的第二个落点**。
⇒ 载体上改成两条后置条件：`bands_now` 与 `measured_now` 同进同出、
`bands_stored` 与 `measurable_stored` 同进同出。

**⑥ `readable` 是三态（`是 / 否 / null`），不是布尔。**
`null` ⟺ 产物不在盘上——「不在盘上的东西读不读得回来」是个没有意义的问题。
⚠ `readable=false` 与 `exists=false` **必须分开**：盘上真有一份读不回来的产物
（`成年人不做选择` 那本的 `stage4/plan_chapters.json` 根本没有 `_provenance`），
折成「不存在」会让界面说「还没跑」——**而它跑过，只是那次把来源账丢了**。

**⑦ 本视图不带 `badge` / `passed` / `counts`。** 那一屏的结论只能来自
`/api/novels/:sid/check/stage3`。视图给的是「两份数字并排放着让人自己看」
（`R-11` 节首语的原话），判定给的是「合不合格」——**混在一格里就是第二个判官**（`R-11.4`）。

---

## 五、三个小模型

| 模型 | 字段 | 说明 |
|---|---|---|
| `NovelList` | `base` · `novels[]`（`NovelOverview`） | 抓包 `novels.json` 的顶层就是 `{base, novels}`。⚠ **复用 `NovelOverview`，不另造一张轻量清单**——原型 `Web/novels.py` 那张第二清单的代价是 stage5 被迫整个排除在外，于是「有没有陈旧产物」那个徽章不覆盖成稿 |
| `JobList` | `jobs[]`（`JobRecord`） | 抓包 `jobs.json` 就是 `{"jobs":[]}`——**一个样本都没有**，所以元素形状只能从 `_orchestration.md` 来 |
| `LogChunk` | `text` · `next_byte` | 按**字节**续读。⚠ 中文一字跨 3 字节，**只在读的一侧解码**（写的一侧按字节追加，见 `_orchestration.md`）。`next_byte` 原样回传给下一次 `?offset=` |

`PreviewRequest`：`sid`（已有书为非空字符串；首次 `stage0.remove --source` 为 `null`）·
`node` · `mode` · `flags[]`。
⚠ `flags` 里的**带值 flag 要带上值**（`--apply-edit <路径>`）——
`/api/enums` 的 `actions[].needs_value` 就是用来告诉前端「这个按钮要先要一个文件」的。

---

## 六、`ErrorBody`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `kind` | 串 | 是 | **异常类名**。取值面 = `novel/errors.py` 的类名集合，由测试闭合 |
| `message` | 串 | 是 | 人话 |
| `missing` | 串 或 null | 是 | 缺的是**哪一个**（`'novel'` / `'stage3/style_grounding.json'`） |
| `hint` | 串 或 null | 是 | 下一步该做什么 |

⚠ **`kind` 用类名不用措辞**：前端的四态要求区分「后端没起来 / 这本书没有 / 这一步还没跑」
（`04_delivery.md` §每屏四态），靠句子前缀去认，一次措辞改动就静默断掉。

状态码表的唯一落点是 `novel/api/errors.py::STATUS`：**按类精确匹配再沿 MRO 上溯，没有默认格**
——`novel/errors.py` 开头写着「不设默认异常」，本表是它在 HTTP 侧的同一条纪律。
落不进表的异常说明那个失败还没想清楚，而一个默认的 500 会把它藏起来。

⚠ **`PathRefused` 是唯一一条两侧归属不同的**：CLI 侧它是退出码 3（代码自相矛盾），
HTTP 侧「书 id 不合法」的产地是**客户端**，那是 400。
解法不是给异常分子类，是**在边界上先验**——路由第一件事调 `paths.check_sid(sid)`
就地翻 400；验过之后再从深处冒出来的 `PathRefused`（例如「一个 id 匹配到两个目录」）
就真的是我们的 bug。**「边界验过之后再抛就是 bug」是一条说得清的线。**

---

## 七、仍**没有**落地的（登记，不是漏）

- **未移植的七层**：路由**全部注册**，一律 **501** 并点名 `contracts.rules.PORTED_STAGES`。
  判定由 `stage not in PORTED_STAGES` 得出，不手写清单。
  ⇒ 前端分得出「URL 打错了」（404）与「这一层还没建」（501），而这两件事该做的动作完全不同。
- **`stage5_review` 这个 check kind 已改名为 `stage5`**（E 期裁决），
  八个 kind 现在与 `invariants.STAGES` 逐值相等、从它派生。
  ⚠ `04_delivery.md:71` 那张「固定，不要改」的表要改这一格，`web/` 里的引用同理——
  但 **E 期不动 `web/`**（已裁决），账记在仓根 `README.md`。
