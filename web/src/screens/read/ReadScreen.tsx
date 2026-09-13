import { useNavigate } from 'react-router-dom'
import { errorBody } from '@/api/errors'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { ComparePanes, PaneScroll } from '@/components/compare/ComparePanes'
import { QueryStates } from '@/components/state/QueryStates'
import { ErrorHint } from '@/components/state/States'
import { ChapterStrip } from '@/components/ui/ChapterStrip'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { useReadScreen } from './useReadScreen'
import { BlueprintEditPanel } from './BlueprintEditPanel'
import type { ActorRef, PlotBlueprint } from '@/types/plot.generated'

/**
 * 完整剧情与原文并排的只读对照；Stage2 语义审阅已退役，
 * 机械可用性看校验报告，编辑入口由 BlueprintEditPanel 单独提供。
 */
export function ReadScreen() {
  const vm = useReadScreen()
  const nav = useNavigate()
  return <ScreenFrame screen="read" onJumpChapter={(n) => nav('/n/' + vm.sid + '/read/' + n)}>
    <QueryStates q={vm.query} loadingLabel="读取原文与剧情蓝图"
      missing={{ title: '原文 ↔ 剧情蓝图', chapters: vm.chapters, ordinal: vm.ordinal, path: 'read' }}>
      {() => <section className="flex flex-col gap-14 px-30 py-20">
        <header className="flex flex-wrap items-center justify-between gap-12">
          <div>
            <h1 className="text-h1 font-semibold">原文 ↔ 剧情蓝图 · 第 {vm.ordinal} 章</h1>
            <p className="text-body text-ink-3">对照阅读完整剧情、归因、持续状态与揭示时序。</p>
          </div>
          <ChapterStrip items={vm.chapters.map((n) => ({
            n, active: n === vm.ordinal, dim: false, go: () => nav('/n/' + vm.sid + '/read/' + n),
          }))} />
        </header>
        <label className="text-body">阅读范围 <select aria-label="蓝图阅读范围"
          value={vm.cross ? 'cross_chapter' : 'chapter'}
          onChange={(e) => vm.setCross(e.target.value === 'cross_chapter')}>
          <option value="chapter">当前章节</option><option value="cross_chapter">跨章通读</option>
        </select></label>
        {vm.workpack.isFetching ? <p role="status">正在读取当前范围的阅读材料…</p> : null}
        {vm.workpack.isError ? <div role="alert">
          阅读材料暂不可用：{errorBody(vm.workpack.error)}<ErrorHint error={vm.workpack.error} />
          <button type="button" onClick={() => void vm.workpack.refetch()}>重新读取</button>
        </div> : null}
        {vm.pack ? <p className="text-body">
          已生成 {vm.pack.progress.generated_chapters ?? '未知'} / {vm.pack.progress.total_chapters ?? '未知'} 章 ·
          机械已提交 {vm.pack.progress.submitted_chapters ?? '未知'} 章 ·
          产物状态 {vm.pack.progress.artifact_state}
          {vm.pack.progress.unavailable_reason ? ` · ${vm.pack.progress.unavailable_reason}` : ''}
        </p> : <p>当前范围的阅读材料未取回；这里只显示该章原文只读视图。</p>}
        {vm.rows.map((row) => <ComparePanes key={row.chapter_ordinal}
          left={<Panel><PaneHeader title={'原文 · 第 ' + row.chapter_ordinal + ' 章'} meta="完整原文" />
            <div className="prose-cn whitespace-pre-wrap px-20 py-16">{row.text}</div></Panel>}
          right={<Panel className="flex h-full min-h-0 flex-col"><PaneHeader title="完整剧情节点" />
            <PaneScroll className="px-16 py-14">
              {row.blueprint ? <PlotChapter chapter={row.blueprint} /> : <p>
                蓝图尚不可读。{vm.query.data?.blueprint_state.reason}
              </p>}
            </PaneScroll></Panel>} />)}
        <BlueprintEditPanel key={vm.sid + ':' + vm.ordinal} sid={vm.sid} ordinal={vm.ordinal} />
      </section>}
    </QueryStates>
  </ScreenFrame>
}

function PlotChapter({ chapter }: { chapter: PlotBlueprint }) {
  const actor = (value: ActorRef) => {
    const binding = chapter.actor_bindings.find((row) => row.presentation_id === value.presentation_ref)
    return binding ? binding.surfaces.join(' / ') : value.char_id + (value.presentation_ref ? ' · ' + value.presentation_ref : '')
  }
  const moment = (value: PlotBlueprint['states'][number]['established_at']) =>
    value.kind === 'at_entry' ? '章首' : '节点 ' + value.beat_ref
  const evidence = (refs: string[]) => refs.map((ref) => {
    const row = chapter.evidence_anchors.find((e) => e.evidence_id === ref)
    return row ? '第 ' + row.start_para + (row.end_para === row.start_para ? '' : '–' + row.end_para) + ' 段' : '证据引用不可用'
  }).join('、')
  return <div className="flex flex-col gap-12">
    <p className="text-body text-ink-3">{chapter.chapter_function}</p>
    {chapter.beats.map((beat, i) => <article key={beat.beat_id} className="rounded-block border border-line-2 p-12">
      <h3 className="font-semibold">剧情节点 {i + 1}</h3>
      <p className="prose-cn">{beat.content}</p>
      <p className="text-tag text-ink-4">原文证据：{evidence(beat.evidence_refs)}</p>
      {beat.source_contexts.map((context, n) => <p key={n} className="text-tag text-ink-3">
        {context.time_layer ?? '时层未知'} · {context.place ?? '地点未知'} ·
        在场人物 {context.present === null ? '未知' : context.present.map(actor).join('、') || '明确为空'}
      </p>)}
    </article>)}
    <details><summary>持续状态（{chapter.states.length}）与约束（{chapter.constraints.length}）</summary>
      {chapter.states.length ? chapter.states.map((state) => <article key={state.state_id}
        className="my-8 rounded-block border border-line-2 p-12" aria-label={'持续状态 ' + state.state_id}>
        <p className="prose-cn">{state.modality} · {state.content ?? state.global_ref?.ref} · {state.holder ? actor(state.holder) : '无持有者'}</p>
        <p className="text-tag text-ink-3">{state.state_id} · 成立：{moment(state.established_at)} ·
          结束：{state.ended_at === null ? '未登记结束' : moment(state.ended_at)}</p>
        <p className="text-tag text-ink-3">取代状态：{state.supersedes_state_refs.join('、') || '无登记项'}</p>
        <p className="text-tag text-ink-4">原文证据：{evidence(state.evidence_refs)}</p>
      </article>) : <p>没有登记新的持续状态。</p>}
      <pre className="overflow-auto whitespace-pre-wrap text-sm2">{JSON.stringify({
        constraints: chapter.constraints, entry_state_refs: chapter.entry_state_refs,
        carry_out_refs: chapter.carry_out_refs, inherited_state_endings: chapter.inherited_state_endings,
        narration: chapter.narration,
        actor_bindings: chapter.actor_bindings,
      }, null, 2)}</pre>
    </details>
    <details><summary>证据定位引用</summary>
      <pre className="overflow-auto whitespace-pre-wrap text-sm2">{JSON.stringify({
        beats: chapter.beats.map((b) => ({ beat_ref: b.beat_id, evidence_refs: b.evidence_refs })),
        states: chapter.states.map((s) => ({ state_ref: s.state_id, evidence_refs: s.evidence_refs })),
      }, null, 2)}</pre>
    </details>
    <p className="text-tag text-ink-4">证据共享与范围重叠不判定剧情重复；此处为对照阅读，不是语义审阅结论。</p>
  </div>
}
