import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import {
  FRESH,
  MISSING,
  SEV,
  STAGES,
  freshnessOf,
  kb,
  when,
  type FreshnessDisplay,
} from '@/constants/vocabulary'
import type { NovelRow } from '@/types/novels'

/**
 * 一屏一个纯函数：输入服务端数据，输出这一屏要显示的一切。
 * 组件只负责摆放，不做判断。
 */

export interface ArtifactView {
  name: string
  size: string
  freshness: FreshnessDisplay
  issue: string | null
}

export interface StageColumn {
  n: string
  label: string
  key: string
  items: ArtifactView[]
  isStage5: boolean
  modes: string[]
  done: boolean | null
  stateLabel: string
}

export interface BookView {
  sid: string
  title: string
  chaptersLabel: string
  columns: StageColumn[]
  verdict: string
  verdictDetail: string
  verdictColor: string
  verdictBg: string
  mtimeLabel: string | null
  artifactCount: number
}

function viewOf(n: NovelRow): BookView {
  const all = STAGES.flatMap((s) => n.stages[s.key] ?? [])
  const modes = (n.stage5_modes ?? []).filter((x) => x.charAt(0) !== '_')

  const columns: StageColumn[] = STAGES.map((s) => {
    const artifacts = n.stages[s.key] ?? []
    const declared = n.stage_done?.[s.key]
    // 旧 mock 没有 StageState.done；兼容推导只服务离线样本，真实模式永远走服务端值。
    const done =
      declared !== undefined
        ? declared
        : s.key === 'stage5'
          ? modes.length > 0
          : artifacts.length
            ? artifacts.every(
                (a) => a.exists && a.freshness !== 'stale' && !a.invalid && a.readable !== false,
              )
            : false

    return {
      n: s.n,
      label: s.label,
      key: s.key,
      items: artifacts.map((a) => ({
        name: a.name.replace(/\.json$/, ''),
        size: a.bytes === null ? '—' : kb(a.bytes),
        freshness: freshnessOf(a),
        issue: a.invalid ? '已隔离' : a.exists && a.readable === false ? '无法读取' : null,
      })),
      isStage5: s.key === 'stage5',
      modes,
      done,
      stateLabel: done === true ? '已完成' : done === false ? '待继续' : '判不了',
    }
  })

  const staleN = all.filter((a) => a.exists && a.freshness === 'stale').length
  const unknownN = all.filter((a) => a.exists && a.freshness === 'unknown').length
  const missingN = all.filter((a) => !a.exists).length
  const brokenN = all.filter((a) => a.invalid || (a.exists && a.readable === false)).length
  const pending = columns.find((column) => column.done === false)
  const unknownStageN = columns.filter((column) => column.done === null).length

  let verdict: string, verdictColor: string, verdictBg: string, verdictDetail: string
  if (n.any_broken || brokenN) {
    verdict = brokenN + ' 份产物不可用'
    verdictColor = SEV.hard.color
    verdictBg = SEV.hard.bg
    verdictDetail = '盘上存在，但当前版本读不回来'
  } else if (staleN || n.any_stale) {
    verdict = staleN + ' 份过期'
    verdictColor = FRESH.stale.color
    verdictBg = FRESH.stale.bg
    verdictDetail = '上游变了，下游要重跑'
  } else if (pending) {
    verdict = '待继续'
    verdictColor = FRESH.fresh.color
    verdictBg = FRESH.fresh.bg
    verdictDetail = '下一步：' + pending.n + ' · ' + pending.label
  } else if (unknownN) {
    // 「无从判断」不是「没问题」：它只是没有来源记录
    verdict = unknownN + ' 份无从判断'
    verdictColor = FRESH.unknown.color
    verdictBg = FRESH.unknown.bg
    verdictDetail = missingN
      ? missingN + ' 份尚未落盘'
      : '其余 ' + (all.filter((a) => a.exists).length - unknownN) + ' 份新鲜'
  } else if (unknownStageN) {
    verdict = unknownStageN + ' 层完成度判不了'
    verdictColor = FRESH.unknown.color
    verdictBg = FRESH.unknown.bg
    verdictDetail = '不是没跑，需要打开书逐层确认'
  } else {
    verdict = '全链已有产物'
    verdictColor = FRESH.fresh.color
    verdictBg = FRESH.fresh.bg
    verdictDetail = all.filter((a) => a.exists).length + ' 份产物可读'
  }

  const mt = all.reduce((m, a) => Math.max(m, a.mtime ?? 0), 0)

  return {
    sid: n.sid,
    title: n.title,
    chaptersLabel: n.chapters === null ? '章数待分章' : n.chapters + ' 章',
    columns,
    verdict,
    verdictDetail,
    verdictColor,
    verdictBg,
    mtimeLabel: mt ? '最近落盘 ' + when(mt) : null,
    artifactCount: all.filter((a) => a.exists).length,
  }
}

export function useCorpusScreen() {
  // 10s 一轮：agent 跑书时产物矩阵自己跟进，不用人点「重新读取」
  const q = useQuery({
    queryKey: qk.novels(),
    queryFn: () => api.getNovels(),
    refetchInterval: 10000,
  })

  const books = (q.data?.novels ?? []).map(viewOf)
  const artifacts = books.reduce((s, b) => s + b.artifactCount, 0)

  return {
    query: q,
    base: q.data?.base ?? '',
    books,
    // 取不到 / 还没取到时**不报数**：「0 本书」是一个断言，而这时我们并不知道
    countLabel: q.isSuccess ? books.length + ' 本书 · ' + artifacts + ' 份产物' : null,
    legend: [
      { f: 'fresh' as FreshnessDisplay, desc: FRESH.fresh.desc },
      { f: 'stale' as FreshnessDisplay, desc: FRESH.stale.desc },
      { f: 'unknown' as FreshnessDisplay, desc: FRESH.unknown.desc },
      { f: 'missing' as FreshnessDisplay, desc: MISSING.desc },
    ],
    /** 上传后这本书会走的路 */
    flow: [
      { n: '0', t: '扫番外 / 后记 / 作者有话说，确认删除范围' },
      { n: '1', t: '切分正文与文案 —— 左右对完确认' },
      { n: '2·3', t: '并行：全局设定 + 逐章骨架 ／ 文风画像' },
      { n: '4', t: 'stage2 一确认就发起重构计划' },
      { n: '5', t: '文风 + 计划齐了 → 挑深度章节 → 写作 → 对着判据改' },
      { n: '6', t: '开篇文案' },
      { n: '7', t: '人名替换 → 拼装成书 txt' },
    ],
  }
}
