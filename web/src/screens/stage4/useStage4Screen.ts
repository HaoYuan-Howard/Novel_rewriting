import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { FRESH, SEV, chNo } from '@/constants/vocabulary'
import type { CtxScene, PlanGoal, PlanScene } from '@/types/stages'
import { useOrdinal, useSid } from '@/hooks/useRouteParams'

const LAYER: Record<string, string> = { past: '过去', present: '现在', future: '将来' }

export interface PlanSceneView {
  i: number
  slot: string
  purpose: string
  role: string
  delivery: string
  transition: string
  budget: number
  requiredFacts: string[]
  contextFacts: string[]
  turnFact: string
  entryState: string[]
  exitState: string[]
  knowledge: CtxScene['knowledge'] | null
  dialogue: CtxScene['dialogue'] | null
  verbatimChars: number
  verbatim: string[]
  holds: { k: string; v: string }[]
  stages: { layer: string; layerPresent: boolean; place: string; present: string }[]
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

function sceneView(plan: PlanScene, ctx: CtxScene | undefined): PlanSceneView {
  const holds: { k: string; v: string }[] = []
  values(ctx?.keep_value).forEach((v) => holds.push({ k: '硬数字', v }))
  values(ctx?.must_appear).forEach((v) => holds.push({ k: '必须出现', v }))
  values(ctx?.must_hold).forEach((v) => holds.push({ k: '必须保持', v }))
  const verbatim = values(ctx?.verbatim)
  return {
    i: plan.i,
    slot: plan.slot,
    purpose: ctx?.purpose ?? plan.purpose,
    role: plan.role,
    delivery: plan.delivery,
    transition: plan.transition,
    budget: plan.budget_chars,
    requiredFacts: ctx?.required_facts ?? plan.land_fact_refs,
    contextFacts: ctx?.context_facts ?? plan.context_fact_refs,
    turnFact: ctx?.turn_fact ?? plan.turn_fact_ref,
    entryState: ctx?.entry_state ?? plan.entry_state_refs,
    exitState: ctx?.exit_state ?? plan.exit_state_refs,
    knowledge: ctx?.knowledge ?? null,
    dialogue: ctx?.dialogue ?? null,
    verbatimChars: verbatim.reduce((n, v) => n + [...v].length, 0),
    verbatim,
    holds,
    stages: (ctx?.stages ?? []).map((stage) => ({
      layer: LAYER[stage.time_layer] ?? stage.time_layer,
      layerPresent: stage.time_layer === 'present',
      place: stage.place ?? '地点未标注',
      present: stage.present.join('、') || '（无人）',
    })),
  }
}

export function useStage4Screen() {
  const sid = useSid()
  const ordinal = useOrdinal()
  const [selScene, setSelScene] = useState<number | null>(null)
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
  const ctxBySlot = new Map((ctx?.scenes ?? []).map((scene) => [scene.slot, scene]))
  const scenes: PlanSceneView[] = (plan?.scenes ?? []).map((scene) =>
    sceneView(scene, ctxBySlot.get(scene.slot)),
  )

  const characters = ctx?.characters ?? []
  const factCards: FactCard[] = ctx
    ? [
        {
          title: '人物名册',
          count: characters.length + ' 人',
          lines: characters.slice(0, 5).map((character) => {
            const role = character.role
            return character.display_name + ' — ' +
              (role.length > 22 ? role.slice(0, 22) + '…' : role)
          }),
          more: '其余 ' + Math.max(0, characters.length - 5) + ' 人同样下发',
        },
        {
          title: '章首关系',
          count: (facts?.relations.length ?? 0) + ' 条',
          lines: (facts?.relations ?? []).slice(0, 5)
            .map((r) => r.subject + ' ' + r.predicate + ' ' + r.object),
          more: '其余 ' + Math.max(0, (facts?.relations.length ?? 0) - 5) + ' 条同样下发',
        },
        {
          title: '章首世界规则',
          count: (facts?.world_rules.length ?? 0) + ' 条',
          lines: (facts?.world_rules ?? []).slice(0, 3)
            .map((w) => w.description.length > 40 ? w.description.slice(0, 40) + '…' : w.description)
,
          more: '',
        },
        {
          title: '章首已知内情',
          count: (facts?.secrets.length ?? 0) + ' 条下发',
          lines: (facts?.secrets ?? []).map((secret) =>
            (secret.fact.length > 30 ? secret.fact.slice(0, 30) + '…' : secret.fact) +
            '（读者章首已知）'),
          more: '',
        },
        {
          title: '场景披露许可',
          count: ctx.disclosure_windows.length + ' 项',
          lines: ctx.disclosure_windows.map((row) => row.allowed_slots.join('、') + '：' + row.content),
          more: '',
        },
      ]
    : []

  const budgetTotal = (d?.index ?? []).reduce((n, row) => n + (row.budget_chars ?? 0), 0)

  return {
    query: q,
    sid,
    ordinal,
    chapters: d?.chapters ?? [ordinal],
    identity,
    structureSubject: d?.structure_subject_sha256 ?? null,
    ctxMissing: !!d && !identity && ctx === null,
    selScene,
    toggleScene: (i: number) => setSelScene((cur) => cur === i ? null : i),
    sourceLabel: identity
      ? '恒等基线：盘上还没有计划，这是「如果不重新组场」的显式基线'
      : '来自盘上的 plan_chapters.json + writer_context.json',
    sourceColor: identity ? SEV.unver.color : FRESH.fresh.color,
    sourceBg: identity ? SEV.unver.bg : FRESH.fresh.bg,
    sourceBorder: identity ? SEV.unver.border : 'transparent',
    chapterMeta: plan
      ? '源章 ' + plan.from_source_ordinals.join(',') + ' · ' + plan.scenes.length + ' 场'
      : '',
    bookMeta: d
      ? d.chapters.length + ' 章 · 预算 ' + (budgetTotal || '—') + ' 字'
      : '',
    structureRows: d?.index ?? [],
    index: (d?.index ?? []).map((row) => ({
      no: chNo(row.output_ordinal),
      ordinal: row.output_ordinal,
      budget: row.budget_chars === null ? '—' : row.budget_chars + '字',
      scenes: row.scenes,
      active: row.output_ordinal === ordinal,
    })),
    goal: goalLabel(plan?.chapter_goal),
    openHook: goalLabel(plan?.open_hook_goal),
    endHook: goalLabel(plan?.end_hook_goal),
    chapterFunction: ctx?.semantic_contract.chapter_function ?? plan?.semantic_contract.chapter_function ?? '',
    rewriteBrief: ctx?.chapter_rewrite_brief ?? plan?.chapter_rewrite_brief ?? '',
    budgets: ctx
      ? [
          { k: '源章字数', v: ctx.source_chars === null ? '未知' : String(ctx.source_chars) },
          { k: '本章预算', v: String(ctx.budget_chars) },
          { k: '写手目标', v: d?.writer_target ? d.writer_target.join('–') : '—（没有任务书）' },
        ]
      : [],
    scenes,
    factsMeta: ctx
      ? '人物 ' + characters.length + ' · 关系 ' + (facts?.relations.length ?? 0) +
        ' · 世界规则 ' + (facts?.world_rules.length ?? 0) + ' · 称呼 ' +
        (facts?.address.length ?? 0)
      : '',
    factCards,
  }
}
