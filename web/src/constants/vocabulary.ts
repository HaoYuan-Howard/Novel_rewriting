/**
 * 静态设计契约。不是数据 —— 数据只有服务端响应。
 *
 * 值源自历史原型，现役语义统一登记在 web/DELIVERY.md §6（UI 硬规则 U-01～U-20）。
 * 状态色**只从这里取**：组件里不许写死十六进制或 oklch。
 * 消费者只有 src/components/vocab/ 下那几个叶子组件（它们把 color/bg 内联出来），
 * 其余一切走 Tailwind class。
 *
 * 状态语言 = 色 + 形 + 文字，三者齐备。只靠颜色区分状态是不合格的。
 */

export type Freshness = 'fresh' | 'stale' | 'unknown'
export type Badge = 'pass' | 'amber' | 'fail'
export type Severity = 'hard' | 'info' | 'metric' | 'unver'
export type JobState = 'running' | 'ok' | 'landed_red' | 'failed' | 'unknown'
export type Mode = 'deep' | 'shallow'
export type Op = 'keep' | 'compress' | 'amplify' | 'rewrite'
export type InboxKind =
  | 'invalid'
  | 'rejected'
  | 'stalled'
  | 'repairable'
  | 'human_only'
  | 'verification'
  | 'upstream'
  | 'approval'

/** 产物新鲜度的形状：实心圆 / 实心方块 / 虚线空心圆 */
export type DotShape = 'circle' | 'square' | 'hollow'

export interface FreshnessToken {
  color: string
  bg: string
  /** 大面积区域的浅色底（bg 太重时用它），仍然只有这一处定义 */
  wash: string
  label: string
  desc: string
  shape: DotShape
}

/**
 * 产物新鲜度三态。
 * unknown 必须与 fresh 一眼可分 —— 画成同一个绿点就等于替用户宣布「已验证」。
 * `missing`（不在盘上）是服务端 ArtifactState 直接下发的显示第四态，
 * 前端不再拿 `!exists` 重算业务状态。
 */
export const FRESH: Record<Freshness, FreshnessToken> = {
  fresh: {
    color: 'oklch(0.50 0.10 152)',
    bg: 'oklch(0.95 0.03 152)',
    wash: 'oklch(0.985 0.02 152)',
    label: '新鲜',
    desc: '上游指纹对得上',
    shape: 'circle',
  },
  stale: {
    color: 'oklch(0.48 0.16 52)',
    bg: 'oklch(0.94 0.06 70)',
    wash: 'oklch(0.97 0.04 70)',
    label: '过期',
    desc: '上游变了，这份没跟上',
    shape: 'square',
  },
  unknown: {
    color: 'oklch(0.45 0.03 260)',
    bg: 'oklch(0.94 0.01 260)',
    wash: 'oklch(0.975 0.005 260)',
    label: '无从判断',
    desc: '没有来源记录',
    shape: 'hollow',
  },
}

export const MISSING: FreshnessToken = {
  color: '#8a8479',
  bg: '#f1eee9',
  wash: '#f6f4f1',
  label: '不在盘上',
  desc: '这份产物还没落盘',
  shape: 'hollow',
}

export type FreshnessDisplay = Freshness | 'missing'

export function freshToken(f: FreshnessDisplay): FreshnessToken {
  return f === 'missing' ? MISSING : (FRESH[f] ?? FRESH.unknown)
}

export interface SevToken {
  color: string
  bg: string
  border: string
  /** 大面积区域的浅色底 */
  wash: string
  label: string
}

/**
 * 结论严重度四级，其中一级不是问题。
 * metric = 度量（「篇幅 1135/1120 = 1.01×」这类事实），**不能用警告色**：
 * 样本里一本书 61 条结论有 41 条是 metric，画成警告整屏都是红黄，真正的 info 就淹了。
 * unver = 查不了，独立的第四种，不是 pass 也不是 fail。
 */
export const SEV: Record<Severity, SevToken> = {
  hard: {
    color: 'oklch(0.45 0.17 28)',
    bg: 'oklch(0.95 0.04 28)',
    border: 'oklch(0.62 0.15 28)',
    wash: 'oklch(0.98 0.02 28)',
    label: '阻断',
  },
  info: {
    color: 'oklch(0.42 0.11 250)',
    bg: 'oklch(0.955 0.025 250)',
    border: 'oklch(0.68 0.09 250)',
    wash: 'oklch(0.985 0.012 250)',
    label: '提示',
  },
  metric: { color: '#57534c', bg: '#f1eee9', border: '#ddd8d0', wash: '#faf8f5', label: '度量' },
  unver: {
    color: 'oklch(0.45 0.13 305)',
    bg: 'oklch(0.955 0.03 305)',
    border: 'oklch(0.66 0.12 305)',
    wash: 'oklch(0.985 0.012 305)',
    label: '查不了',
  },
}

export interface BadgeToken {
  color: string
  bg: string
  label: string
}

/**
 * 校验徽章三态。amber = 通过了，但有 N 项查不了（passed 且 unver_count > 0）。
 * 它不是绿色 —— 文案必须写出「它不是绿的」。
 */
export const BADGE: Record<Badge, BadgeToken> = {
  pass: { color: 'oklch(0.42 0.10 152)', bg: 'oklch(0.94 0.04 152)', label: '通过' },
  amber: { color: 'oklch(0.45 0.14 62)', bg: 'oklch(0.94 0.07 72)', label: '通过，但有查不了的' },
  fail: { color: 'oklch(0.45 0.17 28)', bg: 'oklch(0.94 0.05 28)', label: '有阻断' },
}

/** badge 为 null = 这一层还没跑过。空白不代表通过。 */
export const NOT_RUN: BadgeToken & { mark: string } = {
  color: SEV.unver.color,
  bg: SEV.unver.bg,
  /** 没跑过的那个虚线圆点是中性灰，不借用任何一种「结论」的颜色 */
  mark: '#b3ada2',
  label: '没跑过',
}

/** metric 那条底线比文字浅一档 —— 它是事实，不是结论 */
export const METRIC_RULE_COLOR = '#a09a8f'

/**
 * 闸门的状态色。过了是绿的圆，没过是橙的方 ——
 * 和产物新鲜度不是一回事（那是「对不对得上」，这是「人认没认」），所以单独一套。
 */
export const GATE = {
  ok: {
    mark: 'oklch(0.58 0.14 150)',
    ring: 'oklch(0.94 0.05 150)',
    chipColor: 'oklch(0.40 0.09 150)',
    chipBg: 'oklch(0.95 0.04 150)',
  },
  wait: {
    mark: 'oklch(0.62 0.16 55)',
    ring: 'oklch(0.95 0.06 70)',
    chipColor: '#8a8479',
    chipBg: '#f1eee9',
  },
  doneStrip: { color: 'oklch(0.40 0.09 150)', bg: 'oklch(0.97 0.03 150)' },
  /** 逐项检查里「查过、通过」的边框；「查过、可疑」用 FRESH.stale */
  okLine: 'oklch(0.87 0.04 152)',
  warnLine: 'oklch(0.85 0.06 62)',
} as const

/** 剧透开关打开时的告警色。它不是「结论」，是一个危险动作的状态。 */
export const SPOILER_ON = {
  border: 'oklch(0.70 0.13 30)',
  bg: 'oklch(0.97 0.03 30)',
  dot: 'oklch(0.55 0.19 28)',
} as const

/** 中性弱化色（「这里没有值」而不是「这里是 0」） */
export const MUTED = '#c4bdb2'

/** 花钱那条橙在小字上的深一档，用来在文本里点出「这一条要花钱」 */
export const SPEND_INK = 'oklch(0.45 0.16 38)'

/** 弹窗遮罩 */
export const SCRIM = 'rgba(27,26,24,0.34)'

/** 「深度 / 浅层」两条臂的底色。它不是状态，是分类。 */
export const ARM = {
  deep: 'oklch(0.42 0.09 250)',
  shallow: 'oklch(0.45 0.06 200)',
} as const

export interface JobStateToken {
  color: string
  bg: string
  label: string
  shape: DotShape
}

/**
 * 作业四态。服务重启后原本 running 的一律改判 unknown —— 不是 ok。
 * 重启前它是死是活，我们真的不知道。
 */
export const JOB: Record<JobState, JobStateToken> = {
  running: {
    color: 'oklch(0.50 0.10 152)',
    bg: 'oklch(0.94 0.04 152)',
    label: 'running 在跑',
    shape: 'circle',
  },
  ok: { color: 'oklch(0.45 0.03 260)', bg: '#f1eee9', label: 'ok 跑完了', shape: 'circle' },
  landed_red: {
    color: 'oklch(0.45 0.14 62)',
    bg: 'oklch(0.94 0.07 72)',
    label: 'landed_red 已落盘但有红项',
    shape: 'square',
  },
  failed: {
    color: 'oklch(0.45 0.17 28)',
    bg: 'oklch(0.95 0.04 28)',
    label: 'failed 失败',
    shape: 'square',
  },
  unknown: {
    color: FRESH.unknown.color,
    bg: FRESH.unknown.bg,
    label: 'unknown 不知道',
    shape: 'hollow',
  },
}

export interface StageDef {
  n: string
  key: string
  label: string
}

/** 产物矩阵的八列。注意：stage5 成稿不进产物表，只记跑过哪几条臂。 */
export const STAGES: StageDef[] = [
  { n: '0', key: 'stage0', label: '剔除番外' },
  { n: '1', key: 'stage1', label: '摄入分章' },
  { n: '2', key: 'stage2', label: '抽骨架' },
  { n: '3', key: 'stage3', label: '量文风' },
  { n: '4', key: 'stage4', label: '定计划' },
  { n: '5', key: 'stage5', label: '写作+验收' },
  { n: '6', key: 'stage6', label: '开篇文案' },
  { n: '7', key: 'stage7', label: '人名+成书' },
]

/** 左导航的屏序。按流水线顺序，不是按字母。 */
export type ScreenName =
  | 'corpus'
  | 'run'
  | 'stage0'
  | 'stage1'
  | 'read'
  | 'global'
  | 'style'
  | 'stage4'
  | 'depth'
  | 'stage5'
  | 'stage6'
  | 'stage7'
  | 'inbox'

export interface RailItem {
  screen: ScreenName
  n: string
  label: string
  /** 章号屏：路径带 /:o */
  chaptered?: boolean
}

export const RAIL: RailItem[] = [
  { screen: 'stage0', n: '0', label: '剔除番外' },
  { screen: 'stage1', n: '1', label: '分章报告' },
  { screen: 'read', n: '2', label: '原文↔骨架', chaptered: true },
  { screen: 'global', n: '2', label: '全局设定' },
  { screen: 'style', n: '3', label: '文风画像' },
  { screen: 'stage4', n: '4', label: '计划↔上下文', chaptered: true },
  { screen: 'stage5', n: '5', label: '成稿↔材料', chaptered: true },
  { screen: 'stage6', n: '6', label: '开篇文案' },
  { screen: 'stage7', n: '7', label: '人名+成书' },
  { screen: 'inbox', n: '·', label: '待办' },
]

/** stage → 它的主屏。语料库「打开」与进度屏节点链接共用这一份，别在屏里各写一份。 */
export const STAGE_SCREEN: Record<string, ScreenName> = {
  stage0: 'stage0',
  stage1: 'stage1',
  stage2: 'read',
  stage3: 'style',
  stage4: 'stage4',
  stage5: 'stage5',
  stage6: 'stage6',
  stage7: 'stage7',
}

/** 路由必须带 /:o 的屏 —— 跳过去要拼章号（stage7 的章号是可选的，不在内） */
export const CHAPTERED_SCREENS: ReadonlySet<ScreenName> = new Set(['read', 'stage4', 'stage5'])

/** 每屏标题（缺样本屏与文档标题共用） */
export const SCREEN_TITLE: Record<ScreenName, string> = {
  corpus: '语料库',
  run: '跑书进度',
  stage0: '剔除番外 · stage0',
  stage1: '分章报告 · stage1',
  read: '原文 ↔ 骨架',
  global: '全局设定 · stage2',
  style: '文风画像 · stage3',
  stage4: '计划 ↔ 写作上下文',
  depth: '挑深度重构章节',
  stage5: '成稿 ↔ 材料',
  stage6: '开篇文案 · stage6',
  stage7: '人名替换 + 成书 · stage7',
  inbox: '待办',
}

/** 字节数 → 人读的大小 */
export function kb(bytes: number): string {
  if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + 'MB'
  if (bytes >= 1024) return Math.round(bytes / 1024) + 'KB'
  return bytes + 'B'
}

/** unix 秒 → 本地时间串 */
export function when(sec: number): string {
  const d = new Date(sec * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return (
    d.getFullYear() +
    '-' +
    p(d.getMonth() + 1) +
    '-' +
    p(d.getDate()) +
    ' ' +
    p(d.getHours()) +
    ':' +
    p(d.getMinutes())
  )
}

export function chNo(n: number): string {
  return 'ch' + String(n).padStart(2, '0')
}
