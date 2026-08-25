# backend_v1 —— 新后端真实响应的 fixture（FE-06）

抓取方式：`tests/sandbox.py` 把原型语料按当前契约迁移到临时目录，
起 `create_app` 后逐端点 GET（脚本：会话 scratchpad `capture_fixtures.py`）。

- 书：`我把夫君捧成头牌后，全家悔疯了_7615069877019300633`（与 `src/mocks/` 的样本书同一本，sid=7615069877019300633）
- 抓取日期：2026-08-17
- 章级样本仍取第 3 章（与统一契约 §八的冻结基线对齐）

⚠ `src/mocks/` 那 20 份是**冻结的证据基线**（与 design_handoff 逐字节一致，
check-seams 钉着），本目录才是 mock 现役消费的数据源；两边不许互相覆盖。

## ⚠ Stage4/Stage5 场景契约示例（2026-08-25）

Stage2 原子事实契约与 Stage4 场景编译器替换后，旧 Stage4/Stage5 产物没有迁移路径。
`stage4_ch3.json` 改为结构有效的现役小示例；`check_stage4.json` 由当前判据注册表生成，
明确呈现「没有可重判的现役 Stage4 产物」的 `skipped` 状态。Stage5 夹具只保留展示用途，
事实落点名称与现役 `fact_landing` 契约对齐；它们都不代表新链已做付费实证。
原 `check_stage2.json` 抓包早于 v5，其中原子事实必填行因此明确标作 `unver`，不拿旧事件拍
冒充 current-schema 的 fact atom 验收。

## ⚠⚠ `global.json` 是结构有效的 v3 示例，不是新抓包（2026-08-24）

原抓包书已不在盘上，无法付费重建 `GlobalGrounding` v3。本文件因此收成一份小而完整的
现役 API 示例，专门覆盖 relation `public/via_secret` 两支、knowledge event 的
`character/group` 两支，以及证据的「已唯一回取 / `source=null` 定位失败」两态。

成功的 `SourceSpan` 均取自同目录 `reading_ch3.json` 的真实第 3 章 `segments[].text`，
offset 按拼接后的章正文核过；第 8 章 fate 与第 3 章无法唯一定位的时代背景故意保留
`source=null`，用来守 UI 不把失败态显示成“没有证据”。这份示例不代表字段分布、模型遵守率
或语料真值；下次有 v3 真产物时应由真实端点响应整份覆盖。

## ⚠ `enums.json` 不是抓包，是**生成**的（2026-08-22 改）

它是 `novel/api/enums.py::payload()` 的纯函数输出，不需要书、不需要起服务。
重生成只有一条路：

```
python tools/regen_web_enums_fixture.py          # 写盘（--check 只报差异）
```

对账守卫：`tests/test_api_enums.py::test_the_web_enums_fixture_is_the_same_payload`
（比解析后的对象，不比字节——本目录不在 `.gitattributes` 的 `-text` 名单里）。

### 2026-08-22 的一次逐键收口

守卫落地之前，这份夹具相对 `payload()` 已静默分家两个月：
**新增 18 键 · 消失 5 键 · 内容变化 14 键**。病不在「json 旧了」，在**没有守卫**
——所以先补对账、再重生成，不是反过来。

「消失的 5 键」逐个判定过（不许拿新 dump 直接覆盖：那会把「前端还在读某个已删键」
这类真问题一起冲掉）：

| 夹具里的键 | 对应常量 | 何时删的 | 前端代码读者 | 判定 |
|---|---|---|---|---|
| `nonbody_kinds_added` | `NONBODY_KINDS_ADDED` | `08240cf` | 0 | 常量真删 ⇒ 夹具跟着删 |
| `op_band` | `OP_BAND` | `08240cf` | 0 | 同上 |
| `ops` | `OPS` | `08240cf` | 0 | 同上 |
| `partial_mask_note` | `PARTIAL_MASK_NOTE` | `2a695ac` | 0 | 同上 |
| `superseded_free_entries` | `SUPERSEDED_FREE_ENTRIES` | `08240cf` | 0 | 同上 |

「前端代码读者」只数 `.ts` / `.tsx`。这 5 个键在 `web/src/mocks/enums.json` 与
`web/src/mocks/stage2_global.json` 里仍然出现——**那是冻结证据基线，不是消费方**，
它记的就是当年那个后端长什么样，一个字都不该动。

顺带查出并修掉一处真问题：`NONBODY_KINDS_ADDED` 的常量早在 `08240cf` 就删了，而
`contracts/invariants.py` 里描述它的 10 行说明**独自活到了今天**，一直写着
「加词时同时往这里追加一个词」，指着一个不存在的东西。

| fixture | 端点 |
|---|---|
| `check_stage2.json` | `/api/novels/7615069877019300633/check/stage2` |
| `check_stage3.json` | `/api/novels/7615069877019300633/check/stage3` |
| `check_stage4.json` | `/api/novels/7615069877019300633/check/stage4` |
| `check_stage5.json` | `/api/novels/7615069877019300633/check/stage5` |
| `check_stage6.json` | `/api/novels/7615069877019300633/check/stage6` |
| `check_stage7.json` | `/api/novels/7615069877019300633/check/stage7` |
| `enums.json` | `/api/enums`（⚠ **不抓包，由 `tools/regen_web_enums_fixture.py` 生成**，见上） |
| `global.json` | `/api/novels/7615069877019300633/stage2/global` |
| `inbox.json` | `/api/novels/7615069877019300633/inbox` |
| `reading_ch3.json` | `/api/novels/7615069877019300633/reading/3` |
| `review_ch3.json` | `/api/novels/7615069877019300633/review/3` |
| `stage4_ch3.json` | `/api/novels/7615069877019300633/stage4/3` |
| `stage5_ch3.json` | `/api/novels/7615069877019300633/stage5/3` |
| `stage6.json` | `/api/novels/7615069877019300633/stage6` |
| `stage7.json` | `/api/novels/7615069877019300633/stage7` |
| `stage7_ch3.json` | `/api/novels/7615069877019300633/stage7/3` |
| `style.json` | `/api/novels/7615069877019300633/stage3/style` |
