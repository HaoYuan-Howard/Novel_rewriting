# 审校台前端 · 交付说明（mock 版）

按 `design_handoff_novel_refactor_console/` 重建。**当前是 mock 版**：数据全部来自
`src/mocks/` 里那 20 份真实响应，`VITE_USE_MOCK` 一关就走真 HTTP。

```bash
cd web
npm install
npm run dev          # http://127.0.0.1:5173
npm run typecheck    # tsc --noEmit
npm run seams        # 数据缝 / 设计缝红线（见下）
```

---

## 1. 三道缝落在哪

### 数据缝

`src/types/api.ts` 声明 `interface Api`。`src/api/mock.ts`（假后端）和
`src/api/endpoints.ts` 里的 `realApi`（真 HTTP）**都被声明为 `Api`**，
所以「两条路径必须完全同型」是 `tsc` 强制的，不是口头约定。

```ts
// src/api/endpoints.ts 最后一行
export const api: Api = USE_MOCK ? mockApi : realApi
```

屏与组件从不 fetch，只经 `src/hooks/use*` → `api.*`。
`src/mocks/*.json` **只被 `src/api/mock.ts` import**（`npm run seams` 查这条）。

`src/api/mock.ts` 不是 fixture 加载器，是一个假服务端：

- 20 份 JSON 逐字节原样（`npm run seams` 会和交接包比对）
- 一层 localStorage 覆盖层：验收记录、作业、改稿
- 120ms 模拟延迟，所以加载态是真的会看到
- 作业引擎：起一个 running 作业、定时往日志里追加行、最后翻 `ok`
- `previewJob()` 下发命令行 —— 前端不拼 argv
- `NoSampleError`：sid ≠ 样本那本、章号 ≠ 3 时抛，界面走「没有样本」屏

### 静态 / 动态分离

`src/constants/vocabulary.ts` 是**全应用唯一的状态色出口**：
`FRESH` / `SEV` / `BADGE` / `JOB` / `GATE` / `STAGES` / `RAIL`，值逐字抄自设计稿。
其余颜色是 `src/index.css` 里 `@theme` 的令牌。
非核心枚举（hinge_kinds / voice_keys / actions …）运行时从 `/api/enums` 取，不硬编码。

### 渲染缝

一屏一个纯函数。数据形状复杂的四屏拆了 `useXxxScreen.ts`
（corpus / read / stage4 / stage5），其余屏的选择逻辑短，直接在屏文件里。
组件只摆放，不做判断。

---

## 2. 状态归属（没有引入状态管理库）

| 状态 | 家 |
|---|---|
| 20 份响应 | TanStack Query |
| **`gate` / `seen` / `deep` / `writing`** | 一份 `ApprovalDoc`，也走 Query（`['approvals', sid]`） |
| `job` / `jobLog` | Query + `refetchInterval`（由服务端下发的 `running` 决定） |
| 确认框里的 argv | Query（`api.previewJob`） |
| `sid` / 章号 / 当前屏 | React Router 路径 |
| `drawerOpen` / 待确认动作 | 全应用**唯一一个** Context（`useChrome`） |
| 选中的判据 / 剧透开关 / tab / 编辑中的文本 | 屏内 `useState` |

**gate 走 Query 而不是 reducer**：它在真产品里是服务端的人工验收记录。
接后端时 `approveGate` 从「写 localStorage」变成「POST …/approve」，
只改 `mock.ts` 一个文件，`src/screens/**` 与 `src/components/**` **改动 0 行**。

**「闸门不能是全局的」是结构性保证**，不是约定：
`sid` 来自 `useParams()` → 进 query key → 进 localStorage key。
换书就是换一条缓存记录，不存在 A 书的 ✓ 能漏到 B 书的代码路径。
（已实测：换书后导航上一个 ✓ 都没有。）

---

## 3. 路由

设计稿是 12 屏；`brief/04_delivery.md` 那张「固定不要改」的 13 条路由表早于设计稿。
取的方案是：**按设计稿建屏，老路由保留为重定向**，所以那张表里每条 URL 仍然可达。

| 路由 | 屏 |
|---|---|
| `/` | 语料库 |
| `/n/:sid` | → `stage1` |
| `/n/:sid/stage0` | 剔除番外 **（新增，brief 表里没有）** |
| `/n/:sid/stage1` | 分章报告 |
| `/n/:sid/read/:o` | 原文↔骨架 |
| `/n/:sid/global` | 全局设定 |
| `/n/:sid/style` | 文风画像 |
| `/n/:sid/stage4/:o` | 计划↔上下文 |
| `/n/:sid/depth` | 挑深度章节 **（新增）** |
| `/n/:sid/stage5/:o` | 成稿↔材料（吃掉了 S-09 验收/修订）；`writing=false` 时守卫跳 `depth` |
| `/n/:sid/review/:o` | → `stage5/:o` |
| `/n/:sid/stage6` | 开篇文案 |
| `/n/:sid/stage7(/:o)` | 人名+成书 |
| `/n/:sid/inbox` | 待办（吃掉了 S-13 校验报告；校验下沉到各阶段屏的面板） |
| `/n/:sid/check` | → `inbox` |

---

## 4. 组件清单

```
components/
  app/    AppShell  Rail  BookLayout  ScreenFrame  ScreenBoundary
  vocab/  FreshnessDot/Tag/Badge/Legend  SevMark/Chip/Badge/Counts
          CheckBadge  CheckMark  JobStateChip  GateChip  GateMark
  ui/     Panel  PaneHeader  SectionTitle  ActionButton  LinkButton
          Mono  Prose  HighlightedText  ChapterStrip
  compare/ ComparePanes（粘性侧栏）  PaneScroll  CompareRows（行配对）
  state/  LoadingPane  LoadFailed  NotRunYet  MissingSample
          EmptyIsSuspicious  EmptyIsGood  HatchNote  QueryStates
  gate/   GateBar        check/  CheckPanel        job/  JobDrawer  ConfirmDialog
```

两个并排壳覆盖全部六屏对照需求：

- **`ComparePanes`**：一栏正常流 + 一栏粘性侧栏。页面唯一的滚动条归长文那一栏
  （Ctrl-F / PageDown / 滚动条即进度不能坏），结构化那栏钉在旁边内滚。
  **不做滚动同步** —— 两侧长度无关，线性比例是错的，按偏移量算又撞 U-13。
- **`CompareRows`**：两侧有已知对应关系时，做一个栅格、每个对齐单位是横跨两列的一行。
  两格自动等高，对齐精确且与文本长度无关，零 JS。

stage5 的「三栏」= `ComparePanes(sticky=left)` 的右栏里装 `CompareRows`，不是第三种版式。

---

## 5. 从设计稿里修掉的东西

设计稿是导出的演示文件，有几处 markup 被剥掉但逻辑层还在算；也有几处真 bug。

| 位置 | 现象 | 处理 |
|---|---|---|
| 校验面板头（dc.html 1834–1837） | 空的，但 `chkVals` 算了 badge / counts / explain / missing-note | 按逻辑层补回来（否则整块 U-00 内容不见了） |
| 抽屉体（1908–1911） | 空的，但算了 label / argv / log / 四态图例 | 补回来 |
| 语料库 135/146/180/184 | 空槽，算了 `verdict`（有产物坏了 / N 份过期 / N 份无从判断）、`mtimeLabel`、`legend` | 补进右侧那格 |
| `s1HasWarn` | 模板绑了但没人算，warning 框永不出现 | 按 `warnings.length > 0` 补上 |
| 全局设定 2780 行 | `a.by` 是对象，拼串出 `[object Object]` | 改用 `a.by.display` |
| `argvOf()` 1997 行 | 前端自己拼命令行 | 改成 `api.previewJob()` 由服务端下发（U-09） |
| `chapterStrip` 的 `n===3`、`sidOk` | 「样本只抓了第 3 章」漏进了渲染层 | 移进数据层（`api.getSampleCoverage()`，真实模式返回 null） |
| 写盘判定 2306 行 | 对 `extra` 做正则 | 改成对结构化 `extra` / `kind` 的精确集合判定（U-12） |
| `partial_mask_note` | 完整那句只在 `title=` 里 | 常驻显示（U-06 明说不能收进 tooltip） |
| 一屏崩掉 | 变成白板 | 加了 `ScreenBoundary`：崩了要说「这一屏崩了」，白板会被当成「已经看过」 |

另外 `overview` / `review` / `check` 三块 markup 还在设计稿里但被 `go()` 改道成死代码，
内容已并进 stage1 / stage5 / inbox，按合并后的版本建。

---

## 6. 建议后端改的数据形状（**写出来，没有自己改**）

1. **`/api/enums.actions[]` 应该带 `writes_disk: boolean`。**
   现在只有 `free_flag`（CLI flag 串），U-08 那第三类（免费但会改盘上的东西）
   没法从服务端推。前端只好留一张 `constants/actions.ts` 的精确集合表；
   查不到的组合画成「未标注」，不默认成只读。这张表迟早会和后端漂移。

2. **产物应直接下发 `freshness: 'missing'`**，别让前端用 `!exists` 推第四态。
   现在这个推导集中在 `vocabulary.ts` 的 `freshnessOf()` 一处，但它仍然是前端判定。

3. **stage0 与 depth 两屏在 endpoint 表里没有条目。** 建议：
   - `GET /api/novels/:sid/stage0/extras`（现在按这个名字接的）
   - 深度章节选择并入人工验收记录：`GET/POST /api/novels/:sid/approvals`
     （现在前端定义的形状：`{v, sid, gate{s0..s7}, seen{read,global}, deep[], writing, spawns}`）
   - `spawns`：确认某道闸门之后服务端会发起哪些动作。
     现在由 mock 下发，界面据此决定闸门按钮是不是橙的 ——
     **否则「谁会花钱」就要在前后端各存一份。**

4. **`null` 与 `[]` 在两层之间不一致，前端要写两套判空**：
   - `stage4.chapter.beats[]`：`keep_value` / `must_appear` / `must_hold` 缺省是 `[]`；
     但 `time_layer` / `place` 会是 `null`
   - `stage5.chapter.materials.beats[]`：**同名字段缺省是 `null`**，
     而且 `time_layer` / `place` / `present` 三个也会是 `null`
     （样本第 7 拍就是。这条在联调时炸过一次：`present.join()` 直接崩屏。）

5. **`/api/jobs` 的元素形状样本里没有**（`jobs.json` 是空数组）。
   前端按 `04_delivery.md` 的 `JobState` 契约定义了
   `{id, kind, label, argv, state, paid, started, finished, sid, ordinal}`，
   并让 `/api/jobs` 额外下发 `running`（「一本书同时只跑一个」这个判断归服务端）。
   请确认。

6. **`/jobs/:id/stream?offset=N`（SSE）** 现在按 `getJobLog(id, offset) → {lines, offset, done}`
   的签名做的轮询。换成 SSE 时签名不用变。

7. **`n0_extras` 这个 job kind 后端没有**（stage0 是设计稿新增的一层）。
   现在 mock 里临时补了一条 `{label:'stage0 剔除番外', paid:false}`。

---

## 7. U-00 ~ U-20 自查

| 条 | 状态 | 落在哪 |
|---|---|---|
| U-00 「不知道」≠「没问题」 | ✅ | `components/state/` 整层；`EmptyIsSuspicious` 与 `EmptyIsGood` **刻意不合并** |
| U-01 新鲜度三态 | ✅ | 圆点 / 方块 / 虚线空心圆 + 常驻文字标签；`missing` 是第四种显示态 |
| U-02 徽章三态，amber 不是绿 | ✅ | `CheckBadge`；amber 的解释文案写明「查不了不等于没问题」 |
| U-03 sev 四级，metric 不用警告色 | ✅ | `SevMark` 的 metric 是一条底线不是点，色 `#57534c` |
| U-04 作业有 unknown | ✅ | 刷新页面 = 服务重启，原本 running 的一律改判 `unknown`（不是 ok），日志里写明原因 |
| U-05 空数组 ≠ 没问题 | ✅ | `secrets: []` / `problems: []` / `voice_extra: []` / 漏网候选为空，各有各的说法 |
| U-06 部分遮蔽必须标注 | ✅ | `partial_mask_note` 全文常驻在开关旁 |
| U-07 花钱一眼可分，依据只有服务端 `paid` | ✅ | `previewJob` 下发 `paid`；`JobRequest` 里**没有** `paid` 字段，调用方带不进来 |
| U-08 第三类：免费但写盘 | ⚠️ | 做了（深色 + `写盘` 角标 + 第四种「未标注」），但分类表在前端 —— 见上面第 6 节第 1 条 |
| U-09 确认框显示完整命令 | ✅ | argv 来自 `POST /api/jobs/preview`；取不到就不让发起 |
| U-10 不显示预估花费 / 次数 | ✅ | 全屏搜不到 |
| U-11 前端不做判定 | ⚠️ | `badge`/`paid`/`freshness`/`sev`/`passed` 全部照画。**三处例外，都是设计稿原有的显示阈值**：<br>· `align.coverage < 0.6` → 锚点覆盖率画成警示<br>· 每一拍 `预算/源文` 比例 <0.9 或 >1.1 → 画成出档（章级的 `in_band` 服务端有，拍级没有）<br>· 闸门 DAG（人工验收流程，brief 自己画了这张图）<br>建议后端把前两个阈值下发 |
| U-12 不从文案正则抠结构 | ✅ | `chapters` / `fix` 全部读服务端字段；证据高亮只做 `indexOf`，且一处没命中时明说 |
| U-13 不按下标切字符串 | ✅ | `HighlightedText` 的 props 里**没有 start/end**，类型上就踩不进去 |
| U-14 中文阅读界面 | ✅ | 中文优先字体栈；行高 2.05–2.15；不 justify；段落用间距分隔；并排两栏共用一个 `measure` |
| U-15 并排是主视图 | ✅ | 两个 shell；`src/components/` 下没有在对照两侧之间切换的 tab |
| U-16 对不齐要显式标出 | ✅ | 未锚定的行有虚线左边框 + 「盖了第 N 拍…这一块的对应没有验证过」；`锚 3/8 拍` 常驻 |
| U-17 空态说「还没跑」并给下一步 | ✅ | `NotRunYet`；语料库空、待办清空各有专门文案 |
| U-18 不发明进度百分比 / 健康分 | ✅ | 没有。唯一的对比条是「预算 / 源文」两个服务端数字之比 |
| U-19 没有取消按钮 | ✅ | 没有 |
| U-20 1280~2560 桌面 | ✅ | `min-w-[1280px]`，页面**没有 max-width**，多出来的宽度流进并排两栏；正文块仍有 measure 上限 |

---

## 8. 机器可查的红线

`npm run seams` 逐条查：

```
src/screens、src/components 里 0 处 fetch(
src/screens、src/components 里 0 处颜色字面量（#xxx / oklch() / rgb()）
src/mocks/*.json 只被 src/api/mock.ts import
src/mocks/ 与交接包里那 20 份逐字节一致
```

`npm run typecheck`：mock 与真实实现都满足 `Api`。

---

## 9. 已实测的

- 17 条路由全部渲染、无崩屏（含三条重定向、两种「没有样本」）
- 一遍闸门流程：确认 → 闸门翻 ✓ → 导航解锁 → 抽屉出现作业 → 日志逐行流完翻 `ok`
- **刷新页面 ✓ 还在**；换一本书 ✓ 不跟过去
- 付费确认框摆出服务端下发的 argv、写明「发起后没有取消」；
  免费动作标「免费」、花钱动作标「调 LLM · 花真钱」
- `VITE_USE_MOCK=false` 时：一份 mock 数据都不泄漏，屏上报「后端报错了（HTTP 500）」
  并说明这个码在开发环境通常就是 8765 上没服务在听

## 10. 没做的

- 没有真的发写请求（brief 说设计阶段只要 UI）。所有写操作打到 `mock.ts` 的 localStorage。
- 没做 SSE，用轮询（签名一致，换过去不影响调用方）。
- 没做移动端 / 响应式（brief 明确不要）。
- 没引入 UI 组件库、图表库、状态管理库、i18n、SSR。
