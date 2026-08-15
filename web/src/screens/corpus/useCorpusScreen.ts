import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import {
  FRESH,
  MISSING,
  SEV,
  STAGES,
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
  const modes = (n.stage5_modes ?? []).filter((x) => x.charAt(0) !== '_')

  const columns: StageColumn[] = STAGES.map((s) => {
    const artifacts = n.stages[s.key] ?? []
    const done = n.stage_done[s.key] ?? null

    return {
      n: s.n,
      label: s.label,
      key: s.key,
      items: artifacts.map((a) => ({
        name: a.name.replace(/\.json$/, ''),
        size: a.bytes === null ? '—' : kb(a.bytes),
        freshness: a.display_freshness,
        issue:
          a.condition === 'invalid'
            ? '已隔离'
            : a.condition === 'unreadable'
              ? '无法读取'
              : null,
      })),
      isStage5: s.key === 'stage5',
      modes,
      done,
      stateLabel: done === true ? '已完成' : done === false ? '待继续' : '判不了',
    }
  })

  const tone =
    n.summary.verdict === 'broken'
      ? SEV.hard
      : n.summary.verdict === 'stale'
        ? FRESH.stale
        : n.summary.verdict === 'unknown'
          ? FRESH.unknown
          : FRESH.fresh

  return {
    sid: n.sid,
    title: n.title,
    chaptersLabel: n.chapters === null ? '章数待分章' : n.chapters + ' 章',
    columns,
    verdict: n.summary.text,
    verdictDetail: n.summary.detail,
    verdictColor: tone.color,
    verdictBg: tone.bg,
    mtimeLabel: n.summary.latest_mtime ? '最近落盘 ' + when(n.summary.latest_mtime) : null,
    artifactCount: n.summary.artifact_count,
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
