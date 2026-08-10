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
}

export interface StageColumn {
  n: string
  label: string
  key: string
  items: ArtifactView[]
  isStage5: boolean
  modes: string[]
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

  const columns: StageColumn[] = STAGES.map((s) => ({
    n: s.n,
    label: s.label,
    key: s.key,
    items: (n.stages[s.key] ?? []).map((a) => ({
      name: a.name.replace(/\.json$/, ''),
      size: kb(a.bytes),
      freshness: freshnessOf(a),
    })),
    isStage5: s.key === 'stage5',
    // 以 _ 开头的是目录名泄漏进了模式列表，不是一条臂
    modes: (n.stage5_modes ?? []).filter((x) => x.charAt(0) !== '_'),
  }))

  const staleN = all.filter((a) => a.freshness === 'stale').length
  const unknownN = all.filter((a) => a.freshness === 'unknown').length
  const missingN = all.filter((a) => !a.exists).length

  let verdict: string, verdictColor: string, verdictBg: string, verdictDetail: string
  if (n.any_broken || missingN) {
    verdict = '有产物坏了'
    verdictColor = SEV.hard.color
    verdictBg = SEV.hard.bg
    verdictDetail = missingN + ' 份不在盘上'
  } else if (staleN || n.any_stale) {
    verdict = staleN + ' 份过期'
    verdictColor = FRESH.stale.color
    verdictBg = FRESH.stale.bg
    verdictDetail = '上游变了，下游要重跑'
  } else if (unknownN) {
    // 「无从判断」不是「没问题」：它只是没有来源记录
    verdict = unknownN + ' 份无从判断'
    verdictColor = FRESH.unknown.color
    verdictBg = FRESH.unknown.bg
    verdictDetail = '其余 ' + (all.length - unknownN) + ' 份新鲜'
  } else {
    verdict = '暂时不用管'
    verdictColor = FRESH.fresh.color
    verdictBg = FRESH.fresh.bg
    verdictDetail = all.length + ' 份全部新鲜'
  }

  const mt = all.reduce((m, a) => Math.max(m, a.mtime || 0), 0)

  return {
    sid: n.sid,
    title: n.title,
    chaptersLabel: n.chapters + ' 章',
    columns,
    verdict,
    verdictDetail,
    verdictColor,
    verdictBg,
    mtimeLabel: mt ? '最近落盘 ' + when(mt) : null,
    artifactCount: all.length,
  }
}

export function useCorpusScreen() {
  const q = useQuery({ queryKey: qk.novels(), queryFn: () => api.getNovels() })

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
