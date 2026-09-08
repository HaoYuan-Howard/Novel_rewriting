import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { FRESH, SEV } from '@/constants/vocabulary'
import type { ReadingBeat, ReadingNarration, ReadingSegment } from '@/types/stages'
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
  order: number
  layer: string
  layerPresent: boolean
  place: string
  located: boolean
  span: string
  presentCharIds: string[]
  facts: {
    id: string
    claim: string
    meta: string
    evidence: string | null
    participantCharIds: string[]
  }[]
  hinges: { kind: string; text: string }[]
  selected: boolean
}

export function useReadScreen() {
  const sid = useSid()
  const ordinal = useOrdinal()
  const [selBeat, setSelBeat] = useState<string | null>(null)
  const approvals = useApprovals()
  const { doc, markBlueprintSeen } = approvals

  const q = useQuery({
    queryKey: qk.reading(sid, ordinal),
    queryFn: () => api.getReading(sid, ordinal),
    retry: false,
  })
  const book = useQuery({
    queryKey: qk.novel(sid),
    queryFn: () => api.getNovel(sid),
    enabled: !!sid,
    retry: false,
  })

  const d = q.data
  const workpack = useQuery({
    queryKey: qk.blueprintReviewWorkpack(sid),
    queryFn: () => api.getBlueprintReviewWorkpack(sid),
    enabled: !!sid && (!!d?.blueprint || !!d?.blueprint_state.invalid),
    retry: false,
  })
  const reviewPack = workpack.data
  const reviewChapter = reviewPack?.chapters.find((row) => row.chapter_ordinal === ordinal)
  const anomalyChapters = reviewPack?.chapters.filter((row) => row.anomalous) ?? []
  const blueprintReview = {
    blueprintExists: !!d?.blueprint || !!reviewPack,
    seen: doc?.seen.read ?? false,
    approvalLoading: approvals.isLoading,
    approvalError: approvals.isError ? approvals.error : null,
    pending: markBlueprintSeen.isPending,
    submitError: markBlueprintSeen.isError ? markBlueprintSeen.error : null,
    workpackLoading: workpack.isPending && workpack.fetchStatus !== 'idle',
    workpackError: workpack.isError ? workpack.error : null,
    workpackReady:
      workpack.isSuccess && !!reviewPack && reviewPack.binding.subject_kind === 'formal',
    canConfirm:
      !!doc && !doc.seen.read && !approvals.isLoading && !approvals.isError
      && !markBlueprintSeen.isPending && workpack.isSuccess && !!reviewPack
      && reviewPack.binding.subject_kind === 'formal',
    confirm: () => {
      if (!reviewPack || reviewPack.binding.subject_kind !== 'formal') return
      markBlueprintSeen.mutate({
        review_token: reviewPack.review_token,
      })
    },
  }

  // 一旦完整工作包到达，页面的原文分区与语义骨架都只从这同一份 hash-bound
  // 快照渲染。旧的逐章 reading 响应只作工作包尚未到达时的只读占位，绝不能
  // 出现“页面主要展示 B1、确认请求却签 B2”的两请求拼版。
  const reviewBeatKey = (order: number) => 'review-beat-' + order
  const beatOrder = new Map((d?.beats ?? []).map((beat, index) => [beat.beat_id, index + 1]))

  const readingSegments: SegView[] = (d?.segments ?? []).map((s: ReadingSegment, i) => {
    // beat_id=null 即空隙（hole）—— kind 由可空性表达，不再有字符串判别字段
    if (s.beat_id !== null) {
      return {
        key: 'b' + i,
        kind: 'beat' as const,
        beatId: s.beat_id,
        label: '第 ' + (beatOrder.get(s.beat_id) ?? i + 1) + ' 拍 · ' + s.chars + ' 字',
        paras: s.text.split(/\n+/).filter((t) => t.trim()),
        flagged: false,
        selected: selBeat === s.beat_id,
      }
    }
    return {
      key: 'h' + i,
      kind: 'hole' as const,
      beatId: null,
      label: s.flagged ? '没有被任何 beat 覆盖 · ' + s.chars + ' 字' : '空隙 ' + s.chars + ' 字',
      paras: s.flagged ? s.text.split(/\n+/).filter((t) => t.trim()) : [],
      flagged: s.flagged,
      selected: false,
    }
  })

  const readingBeats: BeatView[] = (d?.beats ?? []).map((b: ReadingBeat, index) => ({
    id: b.beat_id,
    order: index + 1,
    layer: LAYER[b.time_layer] ?? b.time_layer,
    layerPresent: b.time_layer === 'present',
    place: b.place || '—',
    located: b.located,
    span: b.located ? '原文已闭合定位' : '未定位',
    presentCharIds: b.present_char_ids,
    facts: b.fact_atoms.map((fact) => ({
      id: fact.fact_id,
      claim: fact.claim,
      meta: fact.kind + ' · ' + fact.obligation,
      evidence: fact.evidence_text,
      participantCharIds: fact.participant_char_ids,
    })),
    hinges: b.hinges.map((h) => ({
      kind: h.kind,
      text: h.text ?? '（未定位——这条保真材料没有原文可引）',
    })),
    selected: selBeat === b.beat_id,
  }))

  const segments: SegView[] = reviewChapter
    ? reviewChapter.coverage.segments.map((s, i) => {
        if (s.beat_order !== null) {
          const beatId = reviewBeatKey(s.beat_order)
          return {
            key: 'review-b' + i,
            kind: 'beat' as const,
            beatId,
            label: '第 ' + s.beat_order + ' 拍 · ' + s.chars + ' 字',
            paras: s.text.split(/\n+/).filter((t) => t.trim()),
            flagged: false,
            selected: selBeat === beatId,
          }
        }
        return {
          key: 'review-h' + i,
          kind: 'hole' as const,
          beatId: null,
          label: s.flagged ? '没有被任何 beat 覆盖 · ' + s.chars + ' 字' : '空隙 ' + s.chars + ' 字',
          paras: s.text.split(/\n+/).filter((t) => t.trim()),
          flagged: s.flagged,
          selected: false,
        }
      })
    : readingSegments

  const beats: BeatView[] = reviewChapter
    ? reviewChapter.beats.map((b) => {
        const id = reviewBeatKey(b.beat_order)
        return {
          id,
          order: b.beat_order,
          layer: LAYER[b.time_layer] ?? b.time_layer,
          layerPresent: b.time_layer === 'present',
          place: b.place || '—',
          located: b.located,
          span: b.located ? '原文已闭合定位' : '未定位',
          presentCharIds: b.present_char_ids,
          facts: b.facts.map((fact) => ({
            id: fact.fact_id,
            claim: fact.claim,
            meta: fact.kind + ' · ' + fact.obligation,
            evidence: fact.evidence_text,
            participantCharIds: fact.participant_char_ids,
          })),
          hinges: b.hinges.map((hinge) => ({
            kind: hinge.kind,
            text: hinge.text ?? '（未定位——这条保真材料没有原文可引）',
          })),
          selected: selBeat === id,
        }
      })
    : readingBeats

  const narrationLabel = (nar: ReadingNarration) =>
    (nar.grammatical_person === 'first' ? '第一人称' : nar.grammatical_person)
    + ' · 叙述者 ' + (nar.narrator_char_id ?? '（未知）')
    + ' · 聚焦者 ' + (nar.focalizer_char_id ?? '（未知）')
    + ' · 代词「' + (nar.pronoun_surfaces.join('/') || '（无）') + '」'
  const narrations = (reviewChapter?.narration ?? d?.narration ?? []).map(narrationLabel)
  const loose = reviewChapter?.coverage.loose_chars ?? d?.loose ?? 0
  const textChars = reviewChapter?.coverage.text_chars ?? d?.text_chars ?? 0
  const uncoveredMinChars = reviewChapter?.coverage.uncovered_min_chars ?? d?.uncovered_min_chars
  const hasDisplayedBlueprint = !!reviewChapter || !!d?.blueprint

  return {
    query: q,
    sid,
    ordinal,
    chapters: book.data?.chapters
      ? Array.from({ length: book.data.chapters }, (_, i) => i + 1)
      : [ordinal],
    segments,
    beats,
    blueprintReview,
    reviewWorkpack: {
      query: workpack,
      data: reviewPack,
      current: reviewChapter,
      anomalyChapters,
      attentionCount: reviewPack?.check.attention.length ?? 0,
    },
    selBeat,
    toggleBeat: (id: string) => setSelBeat((cur) => (cur === id ? null : id)),
    clearSel: () => setSelBeat(null),
    looseLabel:
      loose === 0 ? '没有漏下的原文（loose 0 字）' : '漏下 ' + loose + ' 字没被任何 beat 覆盖',
    looseColor: loose === 0 ? FRESH.fresh.color : FRESH.stale.color,
    looseBg: loose === 0 ? FRESH.fresh.bg : FRESH.stale.bg,
    counts: d ? textChars + ' 字 · ' + segments.length + ' 块 · ' + beats.length + ' 拍' : '',
    narrations,
    // 阈值公开写出来，不藏在实现里
    minNote: d && uncoveredMinChars !== undefined
      ? '低于 ' + uncoveredMinChars + ' 字的空隙不判为「漏」'
      : '',
    selHint: selBeat ? '选中 ' + selBeat + '，左边已定位' : '点一个 beat 或一段原文，两边一起定位',
    leftMeta: d ? '服务端同一工作包切好的 ' + segments.length + ' 块，按 beat 边界分' : '',
    rightMeta: d ? beats.length + ' 拍 · blueprint ' + (reviewChapter || d.blueprint ? '已抽' : '未抽') : '',
    /** 定位问题归判据（/check/stage2 的 span_located 一族），本屏只看覆盖高亮 */
    problems: d
      ? hasDisplayedBlueprint
        ? '定位/覆盖类的判定见本屏底部的 stage2 校验；漏没漏也要靠左边的高亮块自己看。'
        : 'stage2 还没跑——右半边整块缺席，左边是 stage1 的原文。'
      : '',
    problemsEmpty: !!d && hasDisplayedBlueprint,
    unverColor: SEV.unver.color,
  }
}
