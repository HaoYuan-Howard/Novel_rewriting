import { useNavigate } from 'react-router-dom'
import { SEV } from '@/constants/vocabulary'
import { useSampleCoverage } from '@/hooks/useSample'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { ComparePanes, PaneScroll } from '@/components/compare/ComparePanes'
import { QueryStates } from '@/components/state/QueryStates'
import { ChapterGrid } from '@/components/ui/ChapterGrid'
import { ChapterStrip } from '@/components/ui/ChapterStrip'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { cx } from '@/lib/cx'
import { DesignReviewPanel } from './DesignReviewPanel'
import { StructureApprovalPanel } from './StructureApprovalPanel'
import { useStage4Screen, type PlanSceneView } from './useStage4Screen'

/**
 * 计划 ↔ 写作上下文。
 * 左边是「Stage4 新设计了哪些场景」，右边是「写手真正拿得到的材料」——
 * 计划里想到的东西如果没进材料，写手就看不到。
 */
export function Stage4Screen() {
  const vm = useStage4Screen()
  const nav = useNavigate()
  const { hasSample } = useSampleCoverage()

  return (
    <>
      <ScreenFrame
        screen="stage4"
        showGate={false}
        onJumpChapter={(n) => nav('/n/' + vm.sid + '/stage4/' + n)}
      >
        <QueryStates
          q={vm.query}
          loadingLabel="读重构计划"
          missing={{
            title: '计划 ↔ 写作上下文',
            chapters: vm.chapters,
            ordinal: vm.ordinal,
            path: 'stage4',
          }}
        >
          {() => (
          <section className="flex flex-col">
            <header className="sticky top-0 z-10 flex flex-col gap-12 border-b border-line bg-sunken-3 px-30 pt-20 pb-14">
              <div className="flex items-end justify-between gap-24">
                <div className="flex flex-col gap-5">
                  <h1 className="m-0 text-h1 font-semibold">
                    计划 ↔ 写作上下文 · 第 {vm.ordinal} 章
                  </h1>
                  <div className="text-body text-ink-3">
                    左「新场景怎样承载章级语义契约」，右「写手手里实际会有什么」。
                  </div>
                </div>
                <ChapterStrip
                  items={vm.chapters.map((n) => ({
                    n,
                    active: n === vm.ordinal,
                    dim: !hasSample(vm.sid, n),
                    go: () => nav('/n/' + vm.sid + '/stage4/' + n),
                  }))}
                />
              </div>
              <div className="flex flex-wrap items-center gap-10">
                {/* 恒等基线 ≠ 真计划 —— 用虚线框和「查不了」的色，别画成绿的 */}
                <span
                  className="rounded-chip border px-10 py-3 text-body font-semibold"
                  style={{
                    color: vm.sourceColor,
                    background: vm.sourceBg,
                    borderColor: vm.sourceBorder,
                    borderStyle: vm.identity ? 'dashed' : 'solid',
                  }}
                >
                  {vm.sourceLabel}
                </span>
                <span className="rounded-chip bg-inset px-9 py-3 text-body text-ink-3">
                  {vm.chapterMeta}
                </span>
                <span className="ml-auto text-tag text-ink-4">{vm.bookMeta}</span>
              </div>
              {vm.identity ? (
                <div className="prose-cn text-tag text-ink-4">
                  恒等基线不是重构计划：这是按源节点列出的参考组织，不代表已经接受新的设计。
                </div>
              ) : null}
              {vm.subjectUnavailable ? <p role="alert">结构审阅身份不可用：{vm.subjectUnavailable}</p> : null}
              {vm.ctxMissing ? (
                <div className="prose-cn text-tag text-ink-4">
                  当前没有可用写作上下文；可对照蓝图与分场安排，尚不能据此开写。
                </div>
              ) : null}
            </header>

            <div className="flex flex-col gap-14 px-30 pt-14">
              <Panel className="flex flex-col gap-10 px-18 py-14">
                <ChapterGrid
                  items={vm.index}
                  onPick={(o) => nav('/n/' + vm.sid + '/stage4/' + o)}
                >
                  {(i) => (
                    <>
                      <Mono className="text-mini">{i.no}</Mono>
                      <Mono className="text-micro text-ink-5">{i.budget}</Mono>
                      <Mono className="text-micro text-ink-5">{i.scenes} 场</Mono>
                    </>
                  )}
                </ChapterGrid>
              </Panel>

              <div className="grid gap-14" style={{ gridTemplateColumns: 'repeat(3, minmax(0,1fr))' }}>
                {[
                  { k: '本章目标', v: vm.goal },
                  { k: '开篇钩子', v: vm.openHook },
                  { k: '收尾钩子', v: vm.endHook },
                ].map((x) => (
                  <Panel key={x.k} className="flex flex-col gap-6 px-16 py-13">
                    <span className="text-tag text-ink-4">{x.k}</span>
                    <span className="prose-cn text-section text-ink-2">{x.v}</span>
                  </Panel>
                ))}
              </div>

              <Panel className="flex flex-col gap-6 px-16 py-13">
                <span className="text-tag text-ink-4">章级语义契约 · {vm.chapterFunction}</span>
                <span className="prose-cn text-section text-ink-2">{vm.rewriteBrief}</span>
              </Panel>

              <div className="grid gap-1 overflow-hidden rounded-block-lg border border-line bg-line-2" style={{ gridTemplateColumns: 'repeat(4,1fr)' }}>
                {vm.budgets.map((b) => (
                  <div key={b.k} className="flex flex-col gap-3 bg-card px-14 py-12">
                    <span className="text-tag text-ink-4">{b.k}</span>
                    <Mono className="text-h2">{b.v}</Mono>
                  </div>
                ))}
              </div>
            </div>

            <ComparePanes
              className="px-30 pt-14 pb-8"
              ratio="1.15fr 0.85fr"
              left={
                <Panel>
                  <PaneHeader title="Stage4 新场景设计" meta={vm.scenes.length + ' 场'} sticky />
                  <div className="flex flex-col">
                    {vm.scenes.map((scene) => (
                      <PlanSceneRow
                        key={scene.i}
                        scene={scene}
                        selected={vm.selScene === scene.i}
                        onClick={() => vm.toggleScene(scene.i)}
                      />
                    ))}
                  </div>
                </Panel>
              }
              right={
                <Panel className="flex h-full min-h-0 flex-col">
                  <PaneHeader title="写手真正拿得到的材料" meta={vm.materialsMeta} />
                  <PaneScroll className="flex flex-col gap-12 px-16 pt-12 pb-16">
                    {vm.materialCards.map((f) => (
                      <section key={f.title} aria-label={f.title} className="flex flex-col gap-6">
                        <div className="flex items-baseline gap-8">
                          <span className="text-body font-semibold">{f.title}</span>
                          <Mono className="text-mini text-ink-4">{f.count}</Mono>
                        </div>
                        {f.lines.length ? (
                          <ul className="m-0 flex list-none flex-col gap-4">
                            {f.lines.map((l, i) => (
                              <li key={i} className="prose-cn whitespace-pre-wrap text-sm2 text-ink-2">
                                {l}
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <span
                            className="rounded-btn border border-dashed px-10 py-6 text-sm2"
                            style={{ borderColor: SEV.unver.border, color: SEV.unver.color }}
                          >
                            该类材料为空；是否合理以原文和审阅结论为准。
                          </span>
                        )}
                        {f.more ? <span className="text-tag text-ink-5">{f.more}</span> : null}
                      </section>
                    ))}

                    <div className="prose-cn text-micro text-ink-5">
                      普通分场只调整呈现安排。联合呈现是否完整、归因是否保持、是否提前揭示，由设计与结构审阅核对。
                    </div>
                  </PaneScroll>
                </Panel>
              }
            />
          </section>
          )}
        </QueryStates>
      </ScreenFrame>
      <DesignReviewPanel />
      {vm.query.data ? (
        <StructureApprovalPanel
          rows={vm.structureRows}
          identity={vm.identity}
          viewSubject={vm.structureSubject}
          onRefreshView={vm.query.refetch}
        />
      ) : null}
    </>
  )
}

function PlanSceneRow({ scene, selected, onClick }: {
  scene: PlanSceneView; selected: boolean; onClick: () => void
}) {
  return <div className={cx('flex flex-col gap-9 border-b border-line-row px-18 py-14', selected && 'bg-accent-bg')}>
    <button type="button" onClick={onClick} aria-expanded={selected} className="flex flex-wrap items-center gap-8 text-left">
      <Mono>第 {scene.i} 场 · {scene.slot}</Mono>
      <span>{scene.role} · {scene.delivery} · {scene.transition}</span>
      <span className="ml-auto">预算 {scene.budget} 字</span>
    </button>
    <p className="prose-cn">{scene.purpose}</p>
    {scene.presentations.map((delivery, i) => <div key={i} className="rounded-block border border-line-2 p-12">
      <h3 className="text-body font-semibold">{delivery.label}</h3>
      <p className="prose-cn">{delivery.instruction}</p>
      <p className="text-tag text-ink-4">证据：{delivery.evidence.join('、') || '尚未绑定'}</p>
      {delivery.stages === null ? <p className="text-tag text-ink-4">写作舞台尚不可读</p> : delivery.stages.map((stage, n) =>
        <p key={n} className="text-tag text-ink-3">{stage.time_layer ?? '时层未知'} · {stage.place ?? '地点未知'} ·
          {stage.present === null ? '在场人物未知' : stage.present.join('、') || '明确无人'}</p>)}
    </div>)}
    <p className="text-sm2">转折：{scene.turn}</p>
    {scene.context.length ? <p className="text-sm2">已完整呈现的背景：{scene.context.join('、')}</p> : null}
    {selected ? <div className="flex flex-col gap-6 text-sm2">
      <p>明确端点：{scene.endpoints.length ? scene.endpoints.join('；') : '本场没有登记端点'}</p>
      <p>状态变化：{scene.stateEvents === null ? '上下文未生成' : JSON.stringify(scene.stateEvents)}</p>
      <p>执行约束：{scene.constraints === null ? '上下文未生成' : scene.constraints.join('、') || '无登记项'}</p>
    </div> : null}
  </div>
}
