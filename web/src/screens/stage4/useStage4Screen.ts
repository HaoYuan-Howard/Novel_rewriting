import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { FRESH, SEV, chNo } from '@/constants/vocabulary'
import type { CtxBeat, PlanBeat, PlanGoal } from '@/types/stages'
import { useOrdinal, useSid } from '@/hooks/useRouteParams'

const LAYER: Record<string, string> = { past: '过去', present: '现在', future: '将来' }

export interface PlanBeatView {
  i: number
  slot: string
  fromBeats: string
  budget: number | null
  source: number | null
  verbatimChars: number
  ratio: string
  pct: number
  layer: string
  layerPresent: boolean
  layerUnknown: boolean
  place: string
  present: string[]
  events: string[]
  verbatim: string[]
  holds: { k: string; v: string }[]
  segments: {
    layer: string
    layerPresent: boolean
    layerUnknown: boolean
    place: string
    present: string
  }[]
}

export interface FactCard {
  title: string
  count: string
  lines: string[]
  more: string
}

function values(v: string[] | null | undefined): string[] {
  return v ?? []
}

function goalLabel(goal: PlanGoal | null | undefined): string {
  if (!goal) return ''
  const refs = goal.fact_refs.map((ref) => ref.kind + ':' + ref.ref).join('、')
  return [goal.intent, refs].filter(Boolean).join(' · ')
}

/** 顶层 present=null 只表示这拍按 segments 分舞台；平铺且无人由服务端给 []。 */
function flatPresent(c: CtxBeat | undefined): string[] {
  return c?.segments == null ? values(c?.present) : []
}

/** 左（计划）与右（写手任务书）逐拍并排：两边同源同序（同一个 group_beats）。 */
function beatView(p: PlanBeat, c: CtxBeat | undefined): PlanBeatView {
  const holds: { k: string; v: string }[] = []
  values(c?.keep_value).forEach((v) => holds.push({ k: '硬数字', v }))
  values(c?.must_appear).forEach((v) => holds.push({ k: '必须出现', v }))
  values(c?.must_hold).forEach((v) => holds.push({ k: '必须保持', v }))
  const budget = p.budget_chars
  const source = c?.source_chars ?? null
  const raw = budget !== null && source ? budget / source : null
  const verbatim = values(c?.verbatim)
  return {
    i: p.i,
    slot: p.slot,
    fromBeats: p.from_beats.join('、') || '—',
    budget,
    source,
    verbatimChars: verbatim.reduce((n, v) => n + [...v].length, 0),
    ratio: raw === null ? '—' : Math.round(raw * 100) / 100 + '×',
    pct: raw === null ? 0 : Math.min(100, Math.round(raw * 100)),
    layer: !c || c.time_layer == null ? '时间层未标注' : (LAYER[c.time_layer] ?? c.time_layer),
    layerPresent: c?.time_layer === 'present',
    layerUnknown: !c || c.time_layer == null,
    place: c?.place ?? '地点未标注',
    present: flatPresent(c),
    events: c?.events ?? [],
    verbatim,
    holds,
    segments: (c?.segments ?? []).map((s) => ({
      layer: LAYER[s.time_layer] ?? s.time_layer,
      layerPresent: s.time_layer === 'present',
      layerUnknown: false,
      place: s.place ?? '地点未标注',
      present: s.present.join('、') || '（无人）',
    })),
  }
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
  const plan = d?.plan_chapter
  const ctx = d?.ctx_chapter ?? null
  const facts = ctx?.facts
  const identity = d?.identity_plan ?? false

  const ctxBeats = ctx?.segments ?? []
  const beats: PlanBeatView[] = (plan?.beats ?? []).map((p, i) => beatView(p, ctxBeats[i]))

  const charKeys = Object.keys(ctx?.characters ?? {})
  const factCards: FactCard[] = ctx
    ? [
        {
          title: '人物名册',
          count: charKeys.length + ' 人',
          lines: charKeys
            .slice(0, 5)
            .map((k) => {
              const role = ctx.characters[k].role
              return (
                ctx.characters[k].display_name +
                ' — ' +
                (role.length > 22 ? role.slice(0, 22) + '…' : role)
              )
            }),
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
            .map((w) =>
              w.description.length > 40 ? w.description.slice(0, 40) + '…' : w.description,
            )
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
            ctx.must_not_reveal.length +
            ' 条只留 id',
          lines: (facts?.secrets ?? []).map(
            (s) =>
              s.secret_id +
              '：' +
              (s.fact.length > 30 ? s.fact.slice(0, 30) + '…' : s.fact) +
              '（读者此时' +
              (s.reader_knows_by_now ? '已知' : '未知') +
              '）',
          ),
          more: 'must_not_reveal 只有裸 id：' + ctx.must_not_reveal.join(' '),
        },
      ]
    : []

  const budgetTotal = (d?.index ?? []).reduce((n, r) => n + (r.budget_chars ?? 0), 0)
  const spanTotal = (d?.index ?? []).reduce((n, r) => n + (r.span_chars ?? 0), 0)

  return {
    query: q,
    sid,
    ordinal,
    chapters: d?.chapters ?? [ordinal],
    identity,
    ctxMissing: !!d && !identity && ctx === null,
    selBeat,
    toggleBeat: (i: number) => setSelBeat((cur) => (cur === i ? null : i)),
    sourceLabel: identity
      ? '恒等基线：盘上还没有计划，这是「如果计划什么都不改」的样子'
      : '来自盘上的 plan_chapters.json + writer_context.json',
    sourceColor: identity ? SEV.unver.color : FRESH.fresh.color,
    sourceBg: identity ? SEV.unver.bg : FRESH.fresh.bg,
    sourceBorder: identity ? SEV.unver.border : 'transparent',
    chapterMeta: plan
      ? '源章 ' + plan.from_source_ordinals.join(',') + ' · ' + plan.beats.length + ' 拍'
      : '',
    bookMeta: d
      ? d.chapters.length +
        ' 章 · 原文跨度 ' +
        (spanTotal || '—') +
        ' 字 → 预算 ' +
        (budgetTotal || '—') +
        ' 字'
      : '',
    strategy: d?.reconstruction_strategy ?? '',
    index: (d?.index ?? []).map((i) => ({
      no: chNo(i.output_ordinal),
      ordinal: i.output_ordinal,
      budget: i.budget_chars === null ? '—' : i.budget_chars + '字',
      beats: i.beats,
      hidden: i.hidden,
      active: i.output_ordinal === ordinal,
    })),
    goal: goalLabel(plan?.chapter_goal),
    openHook: goalLabel(plan?.open_hook_goal),
    endHook: goalLabel(plan?.end_hook_goal),
    budgets: ctx
      ? [
          { k: '源章字数', v: String(ctx.source_chars) },
          { k: '本章预算', v: ctx.budget_chars === null ? '—' : String(ctx.budget_chars) },
          {
            k: '写手目标',
            v: d?.writer_target ? d.writer_target.join('–') : '—（没有任务书）',
          },
        ]
      : [],
    beats,
    factsMeta: ctx
      ? '人物 ' +
        charKeys.length +
        ' · 关系 ' +
        (facts?.relations.length ?? 0) +
        ' · 世界规则 ' +
        (facts?.world_rules.length ?? 0) +
        ' · 称呼 ' +
        (facts?.address.length ?? 0) +
        ' · must_not_reveal ' +
        ctx.must_not_reveal.length
      : '',
    factCards,
  }
}
