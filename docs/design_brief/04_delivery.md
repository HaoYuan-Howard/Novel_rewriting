# 交付规格

照这个落地，接后端时就是**改一个开关**的事；不照这个落地，要返工。

---

## 技术栈（已定）

| | |
|---|---|
| 框架 | **React 18 + TypeScript** |
| 构建 | **Vite** |
| 样式 | **Tailwind CSS**（视觉风格你定，Tailwind 只是实现手段） |
| 路由 | **React Router**（真实路径，**不要 hash 路由**） |
| 数据 | **TanStack Query**（作业状态要轮询，它天然合适） |

**不要引入**：UI 组件库（除非你有充分理由并写在交付说明里）、图表库、
状态管理库（Redux/Zustand/Jotai——这个应用没有需要它们的全局状态）、
i18n、鉴权、SSR、测试框架以外的任何后端相关依赖。

---

## 唯一的数据缝（**这一条是接后端的全部关键**）

```
src/
  api/
    client.ts        ← fetch 封装：base URL、X-Novel-UI 头、错误归一
    endpoints.ts     ← 每个 endpoint 一个函数，返回类型化数据
    mock.ts          ← USE_MOCK 为真时，从 src/mocks/ 读同名文件
  mocks/             ← 把 05_samples/*.json 原样放这里，一个字节都不要改
  types/             ← 从 mocks 推出来的 TypeScript 类型
  screens/           ← 14 屏
  components/
  routes.tsx
```

**铁律：组件里出现任何内联假数据都算不合格。**
所有屏拿数据只有一条路：`src/api/endpoints.ts` 里的函数。
`USE_MOCK`（`import.meta.env.VITE_USE_MOCK`）为真时它们返回 `src/mocks/` 里的 JSON，
为假时它们真的去 `fetch`。**除了这个开关，两条路径的返回值必须完全同型。**

接后端时我要做的事就是把开关关掉——如果哪个组件自己造了数据，那一屏就会当场空掉，
而这是我在联调时最不想找的一类 bug。

**Vite dev proxy**：`/api` → `http://127.0.0.1:8765`（后端固定绑 127.0.0.1）。

---

## 路由表（**固定，不要改**）

| 路由 | 屏 | GET endpoint | mock 文件 |
|---|---|---|---|
| `/` | S-01 语料库 | `/api/novels` | `novels.json` |
| `/n/:sid` | S-02 总览 | `/api/novels/:sid`<br>`/api/enums`<br>`/api/jobs?sid=:sid` | `novel_overview.json`<br>`enums.json`<br>`jobs.json` |
| `/n/:sid/stage0` | **S-00 剔除审阅** | `…/stage0/segments`<br>`…/stage0/report` | **无 mock**，形状同 `reading_ch3.json` 的 `segments[]` |
| `/n/:sid/stage1` | S-03 原文↔切分 | `…/stage1/segments`<br>`…/stage1/chapters`<br>`…/stage1/hook_copy`<br>〔`…/stage1/report` 已废〕 | **无 mock**，形状同 `reading_ch3.json` 的 `segments[]`<br>`stage1_chapters.json`<br>`stage1_hook_copy.json`<br>〔`stage1_report.json` 不再上屏〕 |
| `/n/:sid/read/:o` | S-04 原文↔骨架 | `/api/novels/:sid/reading/:o` | `reading_ch3.json` |
| `/n/:sid/global` | S-05 全局设定 | `/api/novels/:sid/stage2/global` | `stage2_global.json` |
| `/n/:sid/style` | S-06 文风 | `/api/novels/:sid/stage3/style` | `stage3_style.json` |
| `/n/:sid/stage4/:o` | S-07 计划↔上下文 | `/api/novels/:sid/stage4/:o` | `stage4_ch3.json` |
| `/n/:sid/stage5/:o` | S-08 成稿↔材料 | `/api/novels/:sid/stage5/:o` | `stage5_ch3.json` |
| `/n/:sid/review/:o` | S-09 验收/修订 | `/api/novels/:sid/review/:o` | `review_ch3.json` |
| `/n/:sid/inbox` | S-10 待人工 | `/api/novels/:sid/inbox` | `inbox.json` |
| `/n/:sid/stage6` | S-11 开篇文案 | `/api/novels/:sid/stage6` | `stage6_hook.json` |
| `/n/:sid/stage7` | S-12 人名表 | `/api/novels/:sid/stage7` | `stage7_names.json` |
| `/n/:sid/stage7/:o` | S-12 逐章对照 | `/api/novels/:sid/stage7/:o` | `stage7_ch3.json` |
| `/n/:sid/check` | S-13 校验报告 | `/api/novels/:sid/check/:kind` ×8 | `check_stage2.json`<br>`check_stage5_review.json` |

`:sid` 是书 id（例 `7615069877019300633`），`:o` 是输出章号（1 起）。
**八个** `:kind`：**`stage0`** `stage1` `stage2` `stage3` `stage4` `stage5_review` `stage6` `stage7`
（**只给了两份 mock，其余六份复用这两份的形状即可**——它们同型）。

### 写操作（设计阶段只要 UI，不必真发请求）

全部要求请求头 **`X-Novel-UI: 1`**（后端的 CSRF 闸门，没有它一律 403）。

| 动作 | endpoint |
|---|---|
| 预览一条命令（确认框内容） | `POST /api/jobs/preview` |
| 启动作业 | `POST /api/jobs` |
| 作业详情 / 日志 / **实时流** | `GET /api/jobs/:id`、`/log`、**`/stream?offset=N`（SSE）** |
| 保存修订后的成稿 | `POST /api/novels/:sid/review/:o/edit` |
| 文案定稿 / 编辑 | `POST /api/novels/:sid/stage6/approve`、`/stage6/edit` |
| 人名表定稿 / 编辑 | `POST /api/novels/:sid/stage7/approve`、`/stage7/edit` |
| **stage0 批准 / 编辑剔除范围** | `POST /api/novels/:sid/stage0/approve`、`/stage0/edit` |
| 上传语料 | `POST /api/upload` |

---

## 类型

从 `src/mocks/*.json` 推 TypeScript 类型，放 `src/types/`。
**字段名一律照抄，不许改名、不许压平嵌套、不许把两个 endpoint 的数据合成一个。**

有几个字段是联合类型，请显式写出来（它们的所有取值在 `03_ui_rules.md` 里）：

```ts
type Freshness = 'fresh' | 'stale' | 'unknown'
type Badge     = 'pass'  | 'amber' | 'fail'
type Severity  = 'hard'  | 'info'  | 'metric' | 'unver'
type JobState  = 'running' | 'ok' | 'failed' | 'unknown'
type InboxKind = 'invalid' | 'rejected' | 'stalled' | 'human_only' | 'upstream' | 'approval'
type Op        = 'keep' | 'compress' | 'amplify' | 'rewrite'   // 见 enums.json 的 ops
type Mode      = 'deep' | 'shallow'
```

其余枚举（`hinge_kinds` / `time_layers` / `style_words` / `voice_keys` / `hook_keys` …）
**从 `/api/enums` 运行时取，不要硬编码进前端**。后端改了枚举，前端不该跟着改代码。

---

## 每屏四态，一个都不能少

| 态 | 要求 |
|---|---|
| **加载中** | 骨架屏或明确的加载指示 |
| **出错** | 后端没起来 / 404 / 500。要能区分「后端没起来」和「这本书没有」 |
| **还没跑** | **不是「暂无数据」**，是「这一步还没做，下一步点这里」。见 `U-17` |
| **正常** | mock 数据 |

有三处的「还没跑」不是空白，务必按 `02_screens.md` 处理：
- **S-06 文风**：`exists: false` 时左半边（实测）仍有内容
- **S-07 计划**：没跑过 stage4 时显示恒等基线（`plan_source: "identity"`）
- **S-08 成稿**：`ctx_exists: false` 时给「先跑 stage4」，**不显示假材料**

---

## 交付检查清单

- [ ] `npm i && npm run dev` 能起来，**14 屏**全部可点、可通过 URL 直达
- [ ] `src/mocks/` 里是 `05_samples/` 原样的 19 个文件
- [ ] 全局搜 `src/screens` 和 `src/components`，**0 处内联假数据**、0 处 `fetch(`
- [ ] `VITE_USE_MOCK=false` 时代码路径能走通（后端没起来会报错，这是对的）
- [ ] `03_ui_rules.md` 的 U-00~U-20 逐条自查过，不满足的写进交付说明并说明理由
- [ ] 一份简短的交付说明：设计意图、组件清单、哪些地方你觉得后端数据形状该改（**写出来，不要自己改**）
