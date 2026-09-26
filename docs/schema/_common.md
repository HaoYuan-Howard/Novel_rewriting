# 所有产物共有的部分

**载体已定：Pydantic v2**——
但本文只定**内容**，模型在骨架阶段照它写，**方向别搞反**。
所以下表里不出现任何 Python 类型标注，只出现「类型 / 必填 / 取值面 / 证据」。
证据列的四个取值见 `docs/schema/_README.md`。

七份现役 stage 文档把来源账共有键留给本文。本文管五样东西：

| # | 管什么 | 为什么必须在这里定一次，而不是在七个现役层各定一次 |
|---|---|---|
| 一~三 | `_provenance` 的六个共有来源键 | 它们**逐层同形**。分层定 = 保证漂：`code` 的口径对全链生效 |
| 四 | 新鲜度 `fresh/stale/unknown` | `Q-2` 四个落点里最贵的一个。它由 `inputs` 算出来，算法只能有一份 |
| 五 | `.INVALID` 与三条 0 成本提升路 | 全链**三处**实测伪造 + **一处**实测丢失，都是同一个形状 |
| 六~七 | 轮次账 与 `Finding` | `Finding` 统一为 15 字段，并显式携带章序 |
| 八~九 | `usage` 与记账键的出口 | 出口规则是 `Q-1` 的形式要求，各现役产物层照办 |

---

## 一、`_provenance` 的六个共有来源键

| 键 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `prompts` | `{名字: 指纹}` | 付费节点**是**；0 成本节点**只在有尺子时**有 | 插值**之后**的 system prompt 指纹。见 §1.1 | **三源** |
| `model` | `str` | 付费节点**是**；0 成本节点**键不在** | 本次运行配置的**主模型**；保留既有 A/B 比较轴。见 §1.2 | **契·码** |
| `adopted_models` | `[str, …]`（去重、字典序） | 现役付费 producer **是**；模型兜底落地前旧产物可缺 | 真正被内容闸采纳并进入本产物的响应模型集合；聚合产物可同时含 Flash / Pro。见 §1.2 | **契·码** |
| `code` | `str` | **是** | 完整提交 SHA + 可复现的工作树摘要。见 §1.3 | **三源** |
| `root_clean_sha1` | 40 位 sha1 | **是** | 本产物最终建立在哪一版 `stage0/clean_text.txt` 上；与运行上下文根哈希不一致时拒绝写盘 | **契·码** |
| `inputs` | `{键: 边}` | **是** | 这份产物照哪几份上游产的。见 §三 | **三源** |
| `usage` | `obj` | **否** | 这一份花了多少 token。见 §八 | **设计** |

### 1.1 `prompts` —— 存的是**插值后**的指纹，不是模板

一个模板 + 一份枚举表 = 一个 prompt。**枚举表变了而模板没变，发出去的东西就变了**——
stage6 已经证明这条：`APPROACHES` 一改，`HOOK_SYS` 的指纹必须跟着变，否则
「这份候选是拿哪套写法产的」永远说不清。⇒ 指纹算在**渲染结果**上。

⚠ **判据表自身的指纹统一叫 `RULES`：** **同一个语义在不同节点上必须叫同一个名字**（`Q-6`）。
它答的是「这份报告是拿哪一版尺子量的」：判据表改了而报告没重跑，这个指纹是唯一说得出来的地方。
Stage5/6/7 都调用 `novel.prov.rules_fingerprint(stage)`；指纹覆盖 `Rule` 当前声明的全部字段，
字段集合从 dataclass 本身派生。新增判据元数据不会只在某一层漏算，注册顺序变化也不会制造假 stale。

### 1.2 `model` / `adopted_models` —— 主配置与实际采纳不能混成一格

`model` 回答「这次以哪个模型为主配置」，让既有 A/B、局部修复的配置一致性检查与
存量产物保持同一轴；`adopted_models` 回答「真正进入这份产物的响应由谁生成」。
Flash 内容采样或可重试传输耗尽后可能由 Pro 兜底；若只记 `model=flash`，Pro 结果就会
被静默标成 Flash。逐章/多候选聚合产物还可能同时采纳两种模型，所以后一格必须是集合，
不能用单值覆盖。现役 producer 从 accepted attempt → checkpoint/session/ledger 的独立证据闭环反查，
拿不到即拒绝落盘；provider/CLI 回显 actual 时必须与请求精确相等，公开结果协议未回显时也只能走
capability registry 明确登记、带 CLI 版本的 `driver-enforced` 单模型无 fallback 证据。后一种情况的
session/ledger 继续保留 `null` / `unknown`，不能拿 adopted identity 倒填 actual；
模型兜底落地前的旧产物缺该字段时，才按当时不存在混合模型链的版本边界沿用 `model`。

0 成本节点的两格都必须是**键不在**，不是 `null`。

这条看着像洁癖，它防的是**假账**：`--apply-edit` 落的是人手写的稿，
`review_<n>.json` 是 0 成本算出来的，两者都**没有任何模型参与**。
记一个 `model` 上去就是宣称「某个模型产了它」。

⚠ 而 `null` 与「键不在」也不是一回事（`Q-2` 的三态）：
`null` 是「跑过，没有模型」，键不在是「这份产物太老，没记过这件事」。
**Pydantic 的默认值会把这两者混掉**——这是 `docs/LESSONS.md` 第 4 条的典型形状。

### 1.3 `code` —— 完整提交 SHA + 工作树内容摘要，全链一个实现

同一提交下的两份不同脏树必须可区分。格式为 `<40位提交>-dirty.<16位sha256>`：摘要覆盖 tracked 的二进制 diff，
以及未被 ignore 的 untracked 路径与文件字节；工作树干净时只发 40 位提交。Git 信息不可完整取得时
明确发 `unknown`，不伪造精确身份。

这格只进来源审计，**不进 LLM 缓存键**。缓存身份由实际请求载荷决定，其中包含插值后的 prompt；
因此 prompt 改动必然 miss，而与该请求无关的仓库文件不会击穿所有缓存。两套身份分别回答
“这份产物由哪份代码工作树生成”与“这次线上请求是否逐字相同”，不能互相代替。

### 1.4 `root_clean_sha1` —— 全链根正文身份

所有带 `_provenance` 的 artifact 必填完整 40 位根哈希。
中间 `inputs` 全都看似 fresh 也不能替代这条：Stage0 正文换过而 Stage1 尚未重建时，只有根身份
能立即把 Stage1~7 及其字节产物标为 stale。`RunContext` 从当前已批准的 Stage0 正文建立根身份；
一次运行内读到不同根、或写入来源账声明另一根，均在写盘前显式拒绝。旧 v1 不手补、不猜迁移。

源钩子独立编辑只有一条窄例外：`novel/source_identity.py::revision_proof` 逐条验证同书
Stage0 revision 哈希链、当前批准和真实原文件，复用 Stage1 审计确认两份当前物化准确。
旧根必须是当前 revision 的祖先；从首个章标记起的全部字节、确定性 MainText 原始编码和
业务 payload 均未改变，才可对现役 Stage2–5 的已声明正文输入继续运行原 freshness。
未知输入、HookCopy / clean 依赖、未批准或坏链均无此许可。来源 root、产物、模型费用与
原审批不重写；稳定盲句柄及审批仍绑定经证明 current 的正文产物来源。未变输入的有效
审批可以继续生效，任一业务、证据或决定变化仍按原完整身份失效。
Stage1 原切分确认还须存在：旧票的 MainText 与 HookCopy 两份 SHA 必须均由该祖先
revision 的确定性切分精确重建，并与当前不变正文及切点相符。只读面保留原票，不
重签当前根；无原确认、旧任一 SHA 损坏、未批准或切分改变都不能继承。

Prepare 实际消费新钩子，不取得正文等价例外。它只通过完整 typed draft 重编当前
MainText / HookCopy，保存专属 `source_rebuild`（见 `stage6.md`）；通用局部修订不能
借此跨源重挂。未完成提交遇源再次变化时拒绝补尾，不采用旧回答或继承旧接受。

---

## 二、两把尺子：40 位与 10 位，**故意的**

盘上内容身份：`stage0/revisions.json::source.normalized_source_sha1`
与 revision 内的 `clean_sha1` 是 **40 位全宽**，而 `_provenance.inputs` /
`draft_sha1` / `judged[].sha1` 是 **10 位**。Stage1 不再为这些身份抄写第二份记录。

**裁决：不统一成一个宽度，统一成两个类型。**

| 类型 | 宽度 | 用在哪 | 为什么是这个宽度 |
|---|---|---|---|
| **内容身份** | 40 | `stage0/revisions.json::source.normalized_source_sha1`、运行时 clean 指纹、`stage7.book_txt` 的指纹 | 它回答「**有人换过原文或正文吗**」，人可能手工复核。截断买不到任何东西。⚠ `normalized_source_sha1` 算的是归一化文本，不是原文件字节 digest |
| **来源指纹** | 10 | `inputs` 的两格 · `prompts.*` · `draft_sha1` · `judged[].sha1` | 40 bit。本项目量级（一本书几十份产物）碰撞概率约 1e-6，而它天天出现在报告里，短一点可读 |

**让两把尺子安全的规则只有一条：10 位永不与 40 位比较。**
⇒ 把它做成**载体层**的两个受约束类型，混用在建模期就是类型错误、在加载期就是校验失败。
`docs/BACKEND_CONTRACT.md` 的「谁在查」那一列，这一格填 `载体`，并点名字段。

⚠ 别把这条读成「宽度随意」。**同一个字段在两处用不同宽度**才是病——
同一份文件的指纹在两份产物里长得不一样，谁也不敢拿它们对比。

---

## 三、`inputs` 的一条边 = `{file, consumed}`

| 格 | 类型 | 必填 | 它回答什么 | 证据 |
|---|---|---|---|---|
| `file` | 10 位指纹 | **是** | **这份上游产物动过吗**（整份文件的字节） | **三源** |
| `consumed` | 10 位指纹 ｜ `null` | **是**（可为 `null`） | **我依赖的那部分动过吗**（本节点真正读到的字段投影） | **设计** |

**为什么要第二格。** `Q-4` 已经写死了这条：
「指纹是**文件级**的：改一个不相干的字段会让整份产物的指纹失配，
把一批结论降级成『读数不可用』，且没有重新挂钩的路。
**字段级指纹是一次契约改版，值得在新骨架里就做对。**」
而 stage5 已经被逼出来过一次同形的东西（`draft_sha1` 整份 vs `draft_text_sha1` 只管正文，
理由逐字是「0 成本元数据修复会改整份指纹，但写手一个字都没再写」）。

`consumed` 仍允许是 `null`，含义是**「本节点没有声明自己读了哪些字段」**；
这种边的新鲜度只能是 `unknown`，不能冒充 `fresh`。现役节点的投影统一登记在
`novel/consumption.py`，`ArtifactStore.read` 写边与 `ArtifactStore.freshness` 重算
必须取得同一个函数；调用点不再临时传第二份投影。存量产物的空槽和未登记的新边仍按
上述 `unknown` 语义处理，所以这次收口没有放宽三态判定，也没有改 `InputEdge` schema。

### 3.1 边的键：`<stage>/<文件名>`，**两级，不含书名**

⚠ **这个键不含书名，而那不是笔误——它是原型被迫用子进程的直接原因。**
原型的来源账是**进程级**的（一个模块全局的 dict），键只有两级，
于是同一个进程里读过 A 书再写 B 书的产物，会落下一条**看不出来的假匹配边**。
`R-11.3` 至今写着「服务进程只读，一切写操作走子进程」，理由就是这一条。

⇒ 新骨架把来源账做成 **run 级的实例字段**（一本书一个节点一次跑 = 一个实例），
串扰在结构上不存在。键保持两级即可，因为作用域已经由实例边界给出。
⚠ 但 `R-11.3` 这条规则**仍然留着**——只是它的理由换了。
**已按 `Q-8` 标注为退役**并换成独立成立的那三条
（长作业不阻塞 / argv 可粘进终端 / 一个节点崩了不带走队列），见 `docs/BACKEND_CONTRACT.md`
的 `R-11.3` 与它下面那个框。
**只换一句话而不标注，将来读的人会以为那个坑还在**。

### 3.2 成稿**不进** `inputs`，进 `judged`

stage5/6/7 都有一份「我判了哪几份稿」的清单，它**不是** `inputs`。
现役 canonical `stage5/ch01.json` 虽然恰好只有两级，仍是**被审判的主体**，不是
producer 的上游边；`judged` 还要同时保存完整文件指纹与正文消费投影，才能区分
「元数据变了」和「真正进书的字节变了」。⇒ 逐份成稿记进 `judged`（完整相对路径 +
整份/消费指纹），`inputs` 只记全书级上游。历史双臂路径曾进一步证明不能把这份主体账
塞进两级 `inputs`：`stage5/deep/ch01.json` 与 `stage5/shallow/ch01.json` 会塌成同一个键；
这些路径如今只作 `legacy_incompatible` 反例，不是现役布局。

---

## 四、新鲜度：`fresh` / `stale` / `unknown`

`Q-2` 的第一个落点，也是本系统最贵的那条。**优先级 `stale` > `unknown` > `fresh`。**

| 情况 | 结论 | 理由 |
|---|---|---|
| 没有 `_provenance` | **`unknown`** | **永不 `fresh`**。缺失记成匹配就是替人断言一件没人验证过的事 |
| 有 `_provenance`，但 `inputs` 缺失或为空 | **`unknown`** | 同上 |
| 某条边指向的文件**不在盘上** | **`unknown`** ＋ **点名报出这条幽灵边** | 见下 |
| 任一条边的 `file` 对不上 | **`stale`** | |
| 某条边有 `consumed` 而对不上 | **`stale`** | |
| 某条边 `consumed` 是 `null` | **`unknown`**（该边） | 「没声明读了什么」不是「读的东西没变」 |
| 全部边都取到且相等 | **`fresh`** | |

旧链 Stage4 的章级 B 曾另有登记的 producer 输入契约结论（`binding_input_contract_obsolete`
⇒ `stale`、`binding_input_contract_unproven` ⇒ `unknown`）：判定器
（stage4/binding_input_contract.py 的 inspect_binding_input_contract）与 Store 的附加边
随旧链整层退役（F8b-3），两个 reason 键仍由 `contracts/invariants.py` 与 `novel/prov.py` 的
freshness 映射保留，只服务存量盘面的历史解码；新链 Binding 的输入契约由候选 runner 自身
（`novel/stages/stage4/`）在编译期证明，不重建第二套层级结论。
具体识别、授权与恢复边界见 `docs/BACKEND_CONTRACT.md::R-5.10`。

> ### ⚠ 幽灵边必须点名，这是 `Q-4` 亲自点过的一个 bug
>
> 原型的比对函数对「取不到指纹」的条目**静默跳过**——
> 于是一条指向已删文件的边**既不报错、也不报「不知道」**，只是安静地当作一条正常的上游边。
> 盘上真有一条：某本书的写作上下文，`inputs` 里挂着一份 `.INVALID` 的计划，
> 而那个文件已经不在了（0 成本提升路读完问题稿就把它删了，见 §五）。
>
> ⇒ **取不到指纹 = 那份上游已经不在盘上，要报出来，并按名字说是哪一条。**

⚠ **校验器先报来源，再报判定**（`Q-4`）。对不上时报出来的一堆违规是「拿新尺子量旧布」，
不说破就会被当成一次回归。⇒ 报告的第一节恒是新鲜度 + 幽灵边 + 指纹对照，
**空也要报**（`R-5.10` 就是补这一行的）。

---

## 五、`.INVALID`、`gates_skipped`、以及三条 0 成本提升路

本节只约束仍生产 `.INVALID` 的产物族。机械测量已经折叠进 Stage5/6 的即时消费，
没有独立 artifact、`.INVALID`、`--revalidate` 或局部 `--remeasure` 面。

### 5.1 先校验后落盘（`Q-5`）

阻断级错误**不覆盖已有产物**，问题稿单独存一份带 `.INVALID` 标记的文件。
配 0 成本提升路（`--revalidate`）：按**当前**判据重判，合格即提升。
重算时**只对本次新引入的错误入终态**（`REVALIDATE_ONLY_NEW_ERRORS`）——
否则一条不相干的旧问题会把重算这条路永久堵死。

| 键 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `gates_skipped` | `str[]` | **否**（只写在 `.INVALID` 上） | 阻断级判据里**没跑成**的那些。**空列表也要写** | **契·码** |
| `revalidated` | `bool` | **否** | 这份稿是被重判提升上来的。`False` 与「键不在」不是一回事 | **盘·码** |
| `invalid_baseline_sha256` | 64 位 sha256 | **否**（只写在活动 `.INVALID` 上） | `write_invalid` 首次落下的问题稿定位键；提升时清除 | **契·码** |
| `revalidation_audit` | `RevalidationAudit` | **否**（提升后必有） | actor/reason、不可变基线定位、候选 raw/canonical SHA-256、RFC 6901 改动路径及三项变化布尔 | **契·码** |
| `origin` | `str` | **否** | `human` = 由人工编辑入口形成的新稿；`--revalidate` 不借它表达修改事实 | **三源** |
| `input_basis` | `str` | **否** | 「这份 `inputs` 是本次重判时读的，不是对历史来源的追认」 | **盘·码** |
| `partial_rewrite` | `str` | **否** | ⭐ 这份产物被一条 0 成本路径**局部改写**过，值 = 那条路的 flag。见 5.1b | **盘·码** |

`gates_skipped` 空列表也要写，是因为「这次没有闸门缺料」与「这份产物早于这套机制」
必须分得开。它记的是判据 36 那整套设计的**唯一痕迹**——
而「闸门没跑成」与「闸门跑过没事」正是 `Q-2` 要求必须能区分的两件事。

`--revalidate` 现在必须同时给非空 `--actor` / `--reason`。`write_invalid` 保存不可变基线，
提升前分别比较原始字节与去掉 `_provenance` 后的 canonical 业务 JSON：纯排版变化只令
`raw_changed=true`；业务内容变化令 `content_changed=true`、`edited_by_human=true`，并列出排序去重的
RFC 6901 `changed_json_pointers`。因此「判据变了、原稿原样提升」与「人改过问题稿后提升」不再同形。
该审计仍不把提升伪造成新生成：原 prompts/model/adopted_models/inputs/usage/round 原样继承。

### 5.1b `partial_rewrite` —— 机制已随旧链退役（F8a）

有一类 0 成本路径**只改产物的一部分**（`--recheck` 刷指标并重拼 · `--renormalize`
重算派生字段 · `--identity` 刷新身份投影 · stage7 `--apply-edit` 只改人工决策 patch）。
这类「混版产物」在旧链用 `partial_rewrite` 带值键记录来源。

**STR-06 切换后这条机制整体退役**：上述 flag 全部随旧链节点删除，
`contracts/invariants.py::PARTIAL_REWRITE_FLAGS` 为空表，`Provenance.partial_rewrite`
在类型上结构不可达（`Literal[None]`，只能为 `None`）。新链没有「同一份产物被 0 成本
路部分改写」的形状——免费恢复入口（saved_response／historical_adoption／owner_patch／
chapter_prefix_replay／deterministic_replay）全部整份重写本节点产物，写效应即该节点
的 `produces`。字段保留在载体上只为存量产物解码，不再有新写入方。

存量盘面上带 `partial_rewrite` 的产物是旧链字节，随旧根与冻结旧代码配对读取；
新根不产生该键。

### 5.2 ⚠ 提升不许**伪造**，也不许**丢**——全链四处实测

| 层 | 病 | 实测证据 |
|---|---|---|
| stage4 | **丢**：`.INVALID` 提升路把 `_provenance` 整个丢掉 | 盘上 **1/3 本**的计划**没有任何来源信息**，而没有一行报告会说出来 |
| stage5 | **伪造**：盖上当前 SYS 指纹 + `model` + 当前 `inputs`，`round` 还 +1 | 而**没有任何一次新落笔发生过**。⚠ `--apply-edit` 在**同一个文件里、相隔 30 行**把三件事全做对了 |
| stage6 | **伪造** | 归档与正式产物的候选文本、`findings` **逐字相同**，而 `prompts.HOOK_SYS` 是两个值 |
| stage7 | **伪造** | 四条 0 成本写盘路全部重盖来源账 |

**⇒ 让它在结构上不可能。** 提升函数的签名里**没有** `prompts` / `model` / `adopted_models` / `inputs` 四个参数：

```
promote(旧的 _provenance, *, 代码版本, revalidation_audit) -> 新的 _provenance
    继承旧的 prompts / model / adopted_models / inputs 一个字不动
    记录 revalidated = true 与完整强审计账
    round 不 +1（它不是新的一轮写作）
```

**没有参数，就没有可以传错的东西。** 原型之所以能伪造，正是因为提升分支
够得到那个会构造一份全新来源账的函数。

⚠ **`.INVALID` 归档进 `_rounds/`，不删**（`Q-8`）。原型「读完问题稿就把它删了」
造出的正是 §四那条幽灵边——**删一个还被别人指着的文件，会同时制造两个问题**。

### 5.3 `--apply-edit` 是新修订，`--revalidate` 是强审计提升

继承上一轮的 `inputs`（改的是同一份上游下的稿）· **`pop` 掉 `prompts`、`model` 与 `adopted_models`**
（这一稿不是任何 prompt / 模型产的，记了就是假账）· 记 `origin=human` ·
上一轮 `inputs` 为空时**记下这次真实读过的那份并打上 `input_basis`**，
明说它不是对历史的追认。

两条命令都可以是 0 次 LLM 调用，但不能混称为同一动作：`--revalidate` 只提升活动
`.INVALID`，保留原生成轮次并写强审计；`--apply-edit` 消费 typed 人工输入、归档被替换版本并形成
新的人工修订。Stage 5 尤其要求提交完整单章的 `segments[{slot,text}]`，会生成新的
`RoundFreeProvenance.round`；「0 LLM 费用」不等于「无需人工重写整章」。

---

## 六、轮次账 —— **stage5 专有**，放子类不放基类

| 键 | 类型 | 必填 | 内容 |
|---|---|---|---|
| `round` | `int` | 是 | 见下 |
| `revision_of` | `str` | 否 | 上一轮**正文**的指纹 |
| `review` | `str` | 否 | 触发这一轮的报告指纹 |
| `fixes` | `[[判据, 凭据键]…]` | 否 | 这一轮针对性下发了哪几条。**只存引用不存渲染后的指令文本** |
| `request_fingerprint` | 64 位 sha256 | LLM 新产出**是**；字段引入前 v2 可缺 | 内容闸最终采纳的实际请求身份；从 accepted attempt 反查，不按默认档位估算 |
| `origin` | `str` | 否 | 同 §5.1 |
| `input_basis` | `str` | 否 | 同 §5.1 |

**放子类**是因为这六个键在另外六个现役层里**恒缺席**，而恒缺席的字段是噪声——
同 `op` 那一族的下场（一个常量伪装成 artifact 字段）。

⚠ **`round` = 盘上见过的最大轮次 + 1**，含 `.INVALID` 与 `_rounds/`；
**不是 `len(glob) + 1`**。后者在归档缺号时会重号覆盖。

`request_fingerprint` 绑定的是最终被接受的请求：若 Flash 响应不完整、随后 Pro 兜底成功，
这里必须写 Pro 请求的 64 位身份；命中旧 32 位 cache 文件名时也仍写完整 64 位身份。
现役 Stage5 producer 拿不到 accepted attempt 就拒绝落章。字段在 v2 中以 additive 形状引入，
因此 schema 允许早期 v2 缺席；缺席只表示请求身份未知，不能补算或截断后冒充。

---

## 七、`Finding` —— 全链一份，15 个字段

stage5 / stage6 / stage7 **逐字段相同**。

| 字段 | 类型 | 必填 | 取值面 |
|---|---|---|---|
| `output_ordinal` | `int｜null` | 是（缺省显式为 `null`） | 逐章 finding 必须是对应输出章序；真正书级 finding 为 `null` |
| `rule` | `str` | 是 | 判据表的 key。**撞名当场炸** |
| `sev` | `str` | 是 | `hard｜info｜metric｜unver`。**`metric` 与 `info` 必须分开** |
| `item` | `str` | 是 | 人读的一句话「这条在守什么」 |
| `detail` | `str` | 是 | 只写「本书哪里有问题」的纯文本；schema 边界不下发 `**` 标记 |
| `evidence` | `str[]` | 是 | **机器可比的**凭据 |
| `fix_args` | `{flag: str}` | 是 | 本次命中为 `Rule.fix` 补出的带值实参；只允许规则声明过的来源，不重复 node/无值 flag |
| `evidence_key` | `str` | 是 | `'\|'.join(evidence)`。**收敛键 = (章, rule, evidence_key)** |
| `owner` | `str｜null` | 是 | **`null` ⇔ 它不是结论**（metric/unver）——这条不变量抓出过一个 sev 写错的判据 |
| `actionable` | `bool` | 是 | **蕴含 `owner == 'stage5'`**。⚠ 不等于「会进修复」 |
| `identity_hit` | `bool` | 是 | 这一章的**源文**也触发了同一条 ⇒ 这条结论不携带信息 |
| `identity_dead` | `bool` | 否 | 书级：这条判据在这本书上**零判别力** |
| `converge` | `str` | 是 | `new｜unresolved_N｜same_draft` |
| `unresolved_rounds` | `int` | 是 | 连续未消的轮数 |
| `wontfix` | `obj` | 否 | `{why, at}`。`at` = 人做这个决定时看的是哪一版结论（`review_4` / `semantic_2:<指纹>`）；**审计账，不参与豁免的匹配** |

**三条设计要点：**

1. **只能经工厂构造。** `sev` / `item` / `owner` / `actionable` 从判据注册表取，
   调用方只给 `rule` + `detail` + `evidence`（以及规则明确声明的 `fix_args`）。
   ⇒ **一条 finding 不可能与判据表说的不一致。**
2. `evidence_key` 是 `evidence` 的函数，用载体的后置条件断言它——免费，且不可能忘。
3. **两道恒等闸都只降 `actionable`，不删结论**：
   「查过、但这一章原文自己也这样」与「没查」必须分得开。

---

## 八、`usage` —— 成本账在产物里的那一份

| 键 | 类型 | 必填 | 内容 | 证据 |
|---|---|---|---|---|
| `calls_made` | `int` | 是 | **真发出去**的调用数 | **设计** |
| `calls_cached` | `int` | 是 | 命中缓存的次数（= 0 元） | **设计** |
| `prompt_tokens` / `completion_tokens` / `reasoning_tokens` | `int｜null` | 是 | `null` = 这次响应没给 usage | **设计** |

**⚠ 两个计数器不许合并。** 只数真调用，会让
「参数不变的重跑什么都没跑」（`Q-11` / `R-12.4` 要求**显式说出来**的那条语义）
与「什么都没干」在报告上同形。

**⚠ 只存事实，不存价格。** `Q-10` 明写不显示预估花费——「猜一个数写在界面上比不显示更坏」。
本键存的是 token 数与调用数，**不存钱**。要出价钱是另一件事，要配一张带日期的费率表。

**为什么产物里还要存一份**（跨书跨时间的账本另有一份 append-only 的）：
两份答的是**两个问题**。账本答「这周 / 这本书花了多少」，产物里这份答
「**这一份**花了多少」——那是看返工回路的边际成本时会问的，而账本按时间排，答不了。
⚠ token 数**不可再生**（`Q-3` 的判准），必须落盘。

**终审成本汇总的唯一权威是 `<BASE>/_service/ledger.jsonl`。** 产物 `usage` 只解释某一份产物，
不能跨重试、失败、缓存命中和被归档轮次拼总账；作业日志、CLI 文案与 provenance 也都不是成本源。
终审按 sid 聚合 ledger，真实 wire attempt 与 `cached=true` 分列，绝不从产物 usage 反推全书成本。

### 8.1 服务账本的逐请求观测（不进入 `Usage` schema）

`Output/_service/ledger.jsonl` 的每一行对应一次真实 wire attempt 或一次缓存命中。
同一个进程里的多个写手先经线程锁，不同小说的 CLI 子进程再经
`ledger.jsonl.lock` 的操作系统文件锁；临界区只含一行已经序列化好的 append，LLM 请求本身
不被串行化。锁文件是 `_service/` 状态，不是产物，也不进入来源账。
⚠ 这两把锁的实现落点是 `novel/appendlog.py`，账本与其余四份 `_service/*.jsonl`
**共用同一份**（`_orchestration.md` §七）——不是每份各持一把。
在上表的 token 事实之外，每行还固定带以下可空字段：

| 字段 | 含义 |
|---|---|
| `attempt` / `max_attempts` | 本次真实发送是重试窗口里的第几次；缓存命中为 `null` |
| `finish_reason` | 供应商返回的结束原因；未收到响应时为 `null` |
| `latency_ms` | 仅真实传输调用的单调时钟耗时；缓存命中为 `null`。`role=commander` 行（历史，仅存量账解码）记整轮 Commander CLI 子进程墙钟（含其内部全部模型往返与工具调用，不含之后的 execution drain），进程未启动为 `null` |
| `error_kind` | 传输常见值为 `timeout｜connection｜rate_limit｜conflict｜server_error｜http_error｜transport`；**存量** Agent CLI 账另可见 `provider_transient｜authentication｜usage_limit｜permission｜agent_command｜agent_protocol`（ledger reader 只读字符串，不重建运行时对象；现役直连 LLM 不再写入后者） |
| `retryable` / `will_retry` | 错误本身是否可重试，以及本次之后是否真的还会重试 |
| `backoff_ms` | 下一次发送前计划执行的退避；没有下一次时为 `null` |
| `chapter` / `chapter_index` / `chapter_total` | 逐章调用在本批次中的身份与位置；书级调用为 `null` |
| `prompt_cache_hit_tokens` / `prompt_cache_miss_tokens` | 供应商侧 prompt 缓存命中/未命中的输入 token；`null` = 响应没有给，不能当成 0 |

`backoff_ms` 记的是**计划并实际交给 sleep 的时长**，不是把等待时间混进
`latency_ms`。这些字段只属于 append-only 服务账本；产物里的 `Usage` 仍保持上表五项，
因此本批不是产物 schema 改版。

---

## 九、记账键的出口规则（`Q-1`）

**记账不是终点，报出来才是。** 三条形式要求，七个现役层照办：

1. **空也要报**，报成 `0 处`，不是不打印这一行——「没改过」与「没在报」必须分得开；
2. **一行常驻 + 非空才展开**，否则报告变噪声，而噪声会让人不看报告；
3. **出口在校验报告，不在 stdout**——stdout 会随会话消失，那不算出口。
   ⚠ 「界面上能看到那个 blob」**也不算**。

**⇒ 骨架把它变成一张会炸的表**（每层声明自己有哪些记账键）：

| 情况 | 处置 | 它防的那次损失 |
|---|---|---|
| 产地记了某个键，声明表里没有 | **显式终态** | stage2 的 8 个记账键曾有 **5 个 0 出口**；退役 Stage3 v1 的 3 个旧键也一个出口都没有，整层现已删除 |
| 声明表里有、产地没记 | 打 `[????] 未记录` | 「记了 0 次」与「压根没记」必须分开 |
| 值为空 | 打 `… 0 处` | 形式要求① |

⚠ **反过来的那一面：不是每一次「落盘前写入」都值得记账。**
判准是**那个位置本来有没有东西**——有，才叫改写（原值被销毁 ⇒ 记账 + 判据改断言计数）；
没有，那叫**初始化**，记一份「本来是空的」只会制造恒定的噪声。
⇒ 先问「那个位置本来有东西吗」，再问「**有没有判据依赖这次改写的结果**」
（stage5 裁 `mech`、stage6 留 `mech`，是同一条判准的两个答案）。

---

## 十、已经查清楚、结论就是「查不了」的

登记在这里免得被当成漏项。

1. **`code` 说不出「脏在哪」。** `--dirty` 只报有没有未提交的改动。
   工作树脏时跑出来的产物**不可复现**，而本字段只能提示这件事，不能修它。
2. **`consumed` 投影正确与否没有通用判据。** 它记的是「本节点声称自己读了哪些字段」，
   静态机制无法证明声明与任意 Python 字段访问完全一致。现役实现把声明收口到唯一注册表，
   写边与重算共用同一函数，并用消费方回归覆盖关键排除项；但新增字段消费仍必须同步更新投影。
   **这是结构 + 回归在守，不是假装能由运行时自动推导。**
3. **来源账证明不了「这份上游当时长什么样」。** 指纹对不上只能说「变过」，
   变成了什么、能不能挂回去，都答不了。⇒ 这正是 `.INVALID` 必须归档而不是删的理由。
4. **`usage` 证明不了钱。** 它是 token 数；单价、阶梯、缓存折扣都不在本系统里。
