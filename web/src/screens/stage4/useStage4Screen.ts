import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { FRESH, SEV, chNo } from '@/constants/vocabulary'
import type { PlanBeat } from '@/types/stages'
import { useOrdinal, useSid } from '@/hooks/useRouteParams'

const LAYER: Record<string, string> = { past: '过去', present: '现在', future: '将来' }

export interface PlanBeatView {
  i: number
  fromBeats: string
  budget: number
  source: number
  verbatimChars: number
  ratio: string
  pct: number
  outOfBand: boolean
  layer: string
  layerPresent: boolean
  layerUnknown: boolean
  place: string
  present: string[]
  events: string[]
  verbatim: string[]
  holds: { k: string; v: string }[]
  segments: { layer: string; layerPresent: boolean; layerUnknown: boolean; place: string; present: string }[]
}

export interface FactCard {
  title: string
  count: string
  lines: string[]
  more: string
}

export function useStage4Screen() {
  const sid = useSid()
  const ordinal = useOrdinal()
  const [selBeat, setSelBeat] = useState<number | null>(null)

  const q = useQuery({
    queryKey: qk.stage4(sid, ordinal),
    queryFn: () => api.getStage4(sid, ordinal),
    retry: false,
  })

  const d = q.data
  const ch = d?.chapter
  const facts = ch?.facts
  // plan_source === 'identity' 表示盘上还没有计划，这是恒等基线，不是真计划
  const identity = d?.plan_source === 'identity'

  const beats: PlanBeatView[] = (ch?.beats ?? []).map((b: PlanBeat) => {
    const holds: { k: string; v: string }[] = []
    b.keep_value.forEach((v) => holds.push({ k: '硬数字', v }))
    b.must_appear.forEach((v) => holds.push({ k: '必须出现', v }))
    b.must_hold.forEach((v) => holds.push({ k: '必须保持', v }))
    const raw = b.budget_chars / Math.max(1, b.source_chars)
    const pct = Math.min(100, Math.round(raw * 100))
    return {
      i: b.i,
      fromBeats: b.from_beats.join('、') || '—',
      budget: b.budget_chars,
      source: b.source_chars,
      verbatimChars: b.verbatim_chars,
      ratio: Math.round(raw * 100) / 100 + '×',
      pct,
      outOfBand: raw > 1.1 || raw < 0.9,
      layer: b.time_layer === null ? '时间层未标注' : (LAYER[b.time_layer] ?? b.time_layer),
      layerPresent: b.time_layer === 'present',
      layerUnknown: b.time_layer === null,
      place: b.place ?? '地点未标注',
      present: b.present,
      events: b.events,
      verbatim: b.verbatim,
      holds,
      segments: b.segments.map((s) => ({
        layer: LAYER[s.time_layer] ?? s.time_layer,
        layerPresent: s.time_layer === 'present',
        layerUnknown: false,
        place: s.place,
        present: s.present.join('、') || '（无人）',
      })),
    }
  })

  const charKeys = Object.keys(ch?.characters ?? {})
  const factCards: FactCard[] = ch
    ? [
        {
          title: '人物名册',
          count: charKeys.length + ' 人',
          lines: charKeys
            .slice(0, 5)
            .map((k) => ch.characters[k].display_name + ' — ' + ch.characters[k].role.slice(0, 22) + '…'),
          more: '其余 ' + Math.max(0, charKeys.length - 5) + ' 人同样下发',
        },
        {
          title: '关系',
          count: (facts?.relations.length ?? 0) + ' 条',
          lines: (facts?.relations ?? [])
            .slice(0, 5)
            .map((r) => r.subject + ' ' + r.predicate + ' ' + r.object),
          more: '其余 ' + Math.max(0, (facts?.relations.length ?? 0) - 5) + ' 条同样下发',
        },
        {
          title: '世界规则 / 弧线',
          count: (facts?.world_rules.length ?? 0) + ' 条',
          lines: (facts?.world_rules ?? [])
            .slice(0, 3)
            .map((w) => w.description.slice(0, 40) + '…')
            .concat(
              facts?.arc.current_stage.stage
                ? ['当前冲突阶段：' + facts.arc.current_stage.stage]
                : [],
            ),
          more: '',
        },
        {
          title: '秘密',
          count:
            (facts?.secrets.length ?? 0) +
            ' 条下发 / ' +
            (ch.must_not_reveal.length ?? 0) +
            ' 条只留 id',
          lines: (facts?.secrets ?? []).map(
            (s) =>
              s.secret_id +
              '：' +
              s.fact.slice(0, 30) +
              '…（读者此时' +
              (s.reader_knows_by_now ? '已知' : '未知') +
              '）',
          ),
          more: 'must_not_reveal 只有裸 id：' + (ch.must_not_reveal ?? []).join(' '),
        },
      ]
    : []

  return {
    query: q,
    sid,
    ordinal,
    identity,
    selBeat,
    toggleBeat: (i: number) => setSelBeat((cur) => (cur === i ? null : i)),
    sourceLabel: identity
      ? '恒等基线：盘上还没有计划，这是「如果计划什么都不改」的样子'
      : '来自盘上的 plan_chapters.json + writer_context.json',
    sourceColor: identity ? SEV.unver.color : FRESH.fresh.color,
    sourceBg: identity ? SEV.unver.bg : FRESH.fresh.bg,
    sourceBorder: identity ? SEV.unver.border : 'transparent',
    chapterMeta: ch
      ? 'op ' + ch.op + ' · 源章 ' + ch.from_source_ordinals.join(',') + ' · ' + ch.beats.length + ' 拍'
      : '',
    bookMeta: d
      ? d.book.chapters +
        ' 章 · 原文 ' +
        d.book.span_chars +
        ' 字 → 预算 ' +
        d.book.budget_chars +
        ' 字（' +
        d.book.ratio +
        '×）· 拍密度 ' +
        d.book.beat_density
      : '',
    strategy: d?.book.reconstruction_strategy ?? '',
    index: (d?.index ?? []).map((i) => ({
      no: chNo(i.output_ordinal),
      ordinal: i.output_ordinal,
      op: i.op,
      budget: i.budget_chars + '字',
      inBand: i.in_band,
      active: i.output_ordinal === ordinal,
    })),
    goal: ch?.chapter_goal ?? '',
    openHook: ch?.open_hook_goal ?? '',
    endHook: ch?.end_hook_goal ?? '',
    budgets: ch
      ? [
          { k: '源章字数', v: String(ch.chapter_source_chars) },
          { k: '本章预算', v: String(ch.budget_chars) },
          { k: '档区间', v: ch.band.join('–') },
          {
            k: '写手目标',
            v: ch.writer_target.join('–') + (ch.writer_target_estimated ? '（估）' : ''),
          },
        ]
      : [],
    beats,
    scopeNote: d?.plan_scope_note ?? '',
    notInPrompt: d?.not_in_writer_prompt ?? '',
    identityNote: d?.identity_note ?? '',
    factsMeta: ch
      ? '人物 ' +
        charKeys.length +
        ' · 关系 ' +
        (facts?.relations.length ?? 0) +
        ' · 世界规则 ' +
        (facts?.world_rules.length ?? 0) +
        ' · 称呼 ' +
        (facts?.address.length ?? 0) +
        ' · must_not_reveal ' +
        ch.must_not_reveal.length
      : '',
    factCards,
  }
}
