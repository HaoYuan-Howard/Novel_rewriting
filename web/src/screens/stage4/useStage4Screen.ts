import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { FRESH, SEV, chNo } from '@/constants/vocabulary'
import type { PlanGoal, CtxScene } from '@/types/stages'
import type { ChapterCharacter } from '@/types/plot.generated'
import { useOrdinal, useSid } from '@/hooks/useRouteParams'

export interface PlanSceneView {
  i: number
  slot: string
  purpose: string
  role: string
  delivery: string
  transition: string
  budget: number
  presentations: { label: string; instruction: string; evidence: string[]; stages: CtxScene['deliveries'][number]['stages'] | null }[]
  context: string[]
  turn: string
  stateEvents: CtxScene['state_events'] | null
  constraints: string[] | null
  endpoints: string[]
}
function goalLabel(goal: PlanGoal | undefined): string {
  return goal ? goal.intent + ' · ' + goal.subject_refs.map((ref) => ref.kind + ':' + ref.ref).join('、')
    + ' · 场景 ' + goal.anchor_scene_ref : ''
}
function allowedScenes(slots: string[] | undefined): string {
  if (slots === undefined) return '许可缺失'
  return slots.join('、') || '无许可'
}
function characterMaterial(character: ChapterCharacter): string {
  return [
    character.display_name + ' · ' + character.actor_ref,
    '人物可出现场景：' + allowedScenes(character.allowed_slots),
    ...character.surfaces.map((surface) => '称谓「' + surface + '」可用场景：'
      + allowedScenes(character.surface_allowed_slots?.[surface])),
    '公开身份：' + (character.role ?? '未提供公开身份描述'),
    '身份描述可用场景：' + allowedScenes(character.role_allowed_slots),
  ].join('\n')
}
export function useStage4Screen() {
  const sid = useSid()
  const ordinal = useOrdinal()
  const [selScene, setSelScene] = useState<number | null>(null)
  const query = useQuery({ queryKey: qk.stage4(sid, ordinal), queryFn: () => api.getStage4(sid, ordinal), retry: false })
  const d = query.data
  const plan = d?.plan_chapter
  const ctx = d?.ctx_chapter ?? null
  const identity = d?.identity_plan ?? false
  const beatLabel = (ref: string) => {
    const i = d?.blueprint.beats.findIndex((b) => b.beat_id === ref) ?? -1
    return i < 0 ? ref : '剧情节点 ' + (i + 1)
  }
  const scenes: PlanSceneView[] = (plan?.scenes ?? []).map((scene) => {
    const writing = ctx?.scenes.find((s) => s.slot === scene.slot)
    return {
      i: scene.i, slot: scene.slot, purpose: scene.purpose, role: scene.role,
      delivery: scene.delivery, transition: scene.transition, budget: scene.budget_chars,
      presentations: (plan?.beat_placements ?? []).flatMap((placement) => placement.deliveries
        .filter((p) => p.scene_ref === scene.slot).map((delivery) => ({
          label: beatLabel(placement.beat_ref),
          instruction: delivery.presentation_instruction ?? '本场完整呈现',
          evidence: delivery.evidence_refs,
          stages: writing?.deliveries.find((p) => p.beat_ref === placement.beat_ref)?.stages ?? null,
        }))),
      context: scene.context_beat_refs.map(beatLabel), turn: beatLabel(scene.turn_beat_ref),
      stateEvents: writing?.state_events ?? null,
      constraints: writing?.constraint_refs ?? null,
      endpoints: (plan?.anchor_bindings ?? []).filter((a) => a.scene_ref === scene.slot).map((a) => JSON.stringify(a)),
    }
  })
  const materialCards = ctx ? [
    { title: '章级剧情目录', count: ctx.beats.length + ' 个完整节点', lines: ctx.beats.map((b) => beatLabel(b.beat_ref) + '：' + b.content), more: '正文只下发一份；场景按呈现指令引用。' },
    { title: '人物名册', count: ctx.characters.length + ' 个公开呈现', lines: ctx.characters.map(characterMaterial), more: '称谓与身份描述分别按所列场景使用；人物标识仅用于对照本章材料。' },
    { title: '持续状态', count: ctx.states.length + ' 项', lines: ctx.states.map((s) => s.modality + ' · ' + (s.content ?? s.global_ref?.ref)), more: '性质不等于传播权限。' },
    { title: '稀疏约束', count: ctx.constraints.length + ' 项', lines: ctx.constraints.map((c) => JSON.stringify(c)), more: '' },
    { title: '共享主体与表达许可', count: ctx.global_subjects.length + ' 项', lines: ctx.global_subjects.map((g) => JSON.stringify(g)), more: '' },
  ] : d ? [
    { title: '蓝图剧情目录', count: d.blueprint.beats.length + ' 个完整节点', lines: d.blueprint.beats.map((b) => beatLabel(b.beat_id) + '：' + b.content), more: '当前没有可用写作上下文；这里只显示蓝图。' },
  ] : []
  const sourceToken = identity ? SEV.unver : FRESH[d?.plan_state.freshness ?? 'unknown']
  const knownBudgets = d?.index.every((r) => r.budget_chars !== null)
  return {
    query, sid, ordinal, chapters: d?.chapters ?? [ordinal], identity,
    structureSubject: d?.structure_subject_sha256 ?? null,
    subjectUnavailable: d?.structure_subject_unavailable_reason ?? null,
    ctxMissing: !!d && ctx === null, selScene,
    toggleScene: (i: number) => setSelScene((cur) => cur === i ? null : i),
    sourceLabel: identity ? '恒等参考计划 · 尚无正式设计' : d?.plan_state.reason ?? '计划状态未知',
    sourceColor: sourceToken.color, sourceBg: sourceToken.bg,
    sourceBorder: identity ? SEV.unver.border : 'transparent',
    chapterMeta: plan ? '源章 ' + plan.from_source_ordinals.join(',') + ' · ' + plan.scenes.length + ' 场' : '',
    bookMeta: d ? d.chapters.length + ' 章 · 预算 ' + (knownBudgets
      ? d.index.reduce((n, r) => n + r.budget_chars!, 0) + ' 字' : '未完整核算') : '',
    structureRows: d?.index ?? [],
    index: (d?.index ?? []).map((r) => ({ no: chNo(r.output_ordinal), ordinal: r.output_ordinal,
      budget: r.budget_chars === null ? '未知' : r.budget_chars + '字', scenes: r.scenes, active: r.output_ordinal === ordinal })),
    goal: goalLabel(plan?.chapter_goal), openHook: goalLabel(plan?.open_hook_goal), endHook: goalLabel(plan?.end_hook_goal),
    chapterFunction: plan?.semantic_contract.chapter_function ?? '', rewriteBrief: plan?.chapter_rewrite_brief ?? '',
    budgets: ctx ? [
      { k: '源章字数', v: ctx.source_chars === null ? '未知' : String(ctx.source_chars) },
      { k: '本章预算', v: String(ctx.budget_chars) },
      { k: '写手目标', v: d?.writer_target?.join('–') ?? '未知' },
    ] : [],
    scenes, materialCards, materialsMeta: ctx ? '当前章级写作上下文' : '写作上下文缺席',
  }
}
