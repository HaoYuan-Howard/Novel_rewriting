import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { FRESH, SEV } from '@/constants/vocabulary'
import type { ReadingBeat, ReadingSegment } from '@/types/stages'
import { useApprovals } from '@/hooks/useApprovals'
import { useOrdinal, useSid } from '@/hooks/useRouteParams'

const LAYER: Record<string, string> = { past: '过去', present: '现在', future: '将来' }

export interface SegView {
  key: string
  kind: 'beat' | 'hole'
  beatId: string | null
  label: string
  /** 空隙且没被判为「漏」时，正文不渲染 —— 它不是内容，是间隙 */
  paras: string[]
  flagged: boolean
  selected: boolean
}

export interface BeatView {
  id: string
  layer: string
  layerPresent: boolean
  place: string
  located: boolean
  span: string
  present: { name: string; resolved: boolean }[]
  event: string
  hinges: { kind: string; text: string }[]
  startsWith: string
  endsWith: string
  selected: boolean
}

export function useReadScreen() {
  const sid = useSid()
  const ordinal = useOrdinal()
  const [selBeat, setSelBeat] = useState<string | null>(null)
  const { doc, markSeen } = useApprovals()

  const q = useQuery({
    queryKey: qk.reading(sid, ordinal),
    queryFn: () => api.getReading(sid, ordinal),
    retry: false,
  })

  // stage2 要两屏都看过才能确认。看过这件事发生在导航时，不在点击时。
  useEffect(() => {
    if (doc && !doc.seen.read && !markSeen.isPending) markSeen.mutate('read')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc?.seen.read])

  const d = q.data

  const segments: SegView[] = (d?.segments ?? []).map((s: ReadingSegment, i) => {
    if (s.kind === 'beat') {
      return {
        key: 'b' + i,
        kind: 'beat',
        beatId: s.beat_id,
        label: s.beat_id + ' · ' + s.chars + ' 字 · ' + s.start + '–' + s.end,
        paras: s.text.split(/\n+/).filter((t) => t.trim()),
        flagged: false,
        selected: selBeat === s.beat_id,
      }
    }
    return {
      key: 'h' + i,
      kind: 'hole',
      beatId: null,
      label: s.flagged ? '没有被任何 beat 覆盖 · ' + s.chars + ' 字' : '空隙 ' + s.chars + ' 字',
      paras: s.flagged ? s.text.split(/\n+/).filter((t) => t.trim()) : [],
      flagged: s.flagged,
      selected: false,
    }
  })

  const beats: BeatView[] = (d?.beats ?? []).map((b: ReadingBeat) => ({
    id: b.beat_id,
    layer: LAYER[b.time_layer] ?? b.time_layer,
    layerPresent: b.time_layer === 'present',
    place: b.place || '—',
    located: b.located,
    span: b.start + '–' + b.end,
    present: b.present.map((p) => ({
      // 没解析出来的人名带一个 ?，且用「查不了」的虚线框——不是普通人名
      name: p.display + (p.resolved ? '' : ' ?'),
      resolved: p.resolved,
    })),
    event: b.event,
    hinges: b.hinges,
    startsWith: b.span?.starts_with ?? '',
    endsWith: b.span?.ends_with ?? '',
    selected: selBeat === b.beat_id,
  }))

  const nar = d?.narration?.[0]
  const loose = d?.loose ?? 0

  return {
    query: q,
    sid,
    ordinal,
    segments,
    beats,
    selBeat,
    toggleBeat: (id: string) => setSelBeat((cur) => (cur === id ? null : id)),
    clearSel: () => setSelBeat(null),
    summary: d?.summary ?? '',
    looseLabel:
      loose === 0 ? '没有漏下的原文（loose 0 字）' : '漏下 ' + loose + ' 字没被任何 beat 覆盖',
    looseColor: loose === 0 ? FRESH.fresh.color : FRESH.stale.color,
    looseBg: loose === 0 ? FRESH.fresh.bg : FRESH.stale.bg,
    counts: d ? d.text_chars + ' 字 · ' + d.segments.length + ' 块 · ' + d.beats.length + ' 拍' : '',
    narration: nar
      ? (nar.grammatical_person === 'first' ? '第一人称' : nar.grammatical_person) +
        ' · ' +
        (nar.narrator?.display ?? '') +
        ' · 「' +
        nar.pronoun_surfaces.join('/') +
        '」'
      : '',
    // 阈值公开写出来，不藏在实现里
    minNote: d ? '低于 ' + d.uncovered_min_chars + ' 字的空隙不判为「漏」' : '',
    selHint: selBeat ? '选中 ' + selBeat + '，左边已定位' : '点一个 beat 或一段原文，两边一起定位',
    leftMeta: d ? '服务端切好的 ' + d.segments.length + ' 块，按 beat 边界分' : '',
    rightMeta: d ? d.beats.length + ' 拍 · blueprint ' + (d.blueprint ? '已抽' : '未抽') : '',
    /** problems 为空绝不画成「抽全了」 */
    problems: d
      ? d.problems.length
        ? d.problems.join('；')
        : '本章 problems 为空：抽取过程没有报出问题。这不代表抽全了——漏没漏要靠左边的高亮块自己看。'
      : '',
    problemsEmpty: !!d && d.problems.length === 0,
    unverColor: SEV.unver.color,
  }
}
