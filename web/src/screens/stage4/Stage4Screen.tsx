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
                  恒等基线不是重构计划：三个 goal 是显式标记；预算=源字数。它回答的是「上游产出够不够写手用」。
                </div>
              ) : null}
              {vm.ctxMissing ? (
                <div className="prose-cn text-tag text-ink-4">
                  writer_context 不在盘上——右半边缺席。跑 stage4.context 重建任务书。
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
                  <PaneHeader title="写手真正拿得到的材料" meta={vm.factsMeta} />
                  <PaneScroll className="flex flex-col gap-12 px-16 pt-12 pb-16">
                    {vm.factCards.map((f) => (
                      <div key={f.title} className="flex flex-col gap-6">
                        <div className="flex items-baseline gap-8">
                          <span className="text-body font-semibold">{f.title}</span>
                          <Mono className="text-mini text-ink-4">{f.count}</Mono>
                        </div>
                        {f.lines.length ? (
                          <ul className="m-0 flex list-none flex-col gap-4">
                            {f.lines.map((l, i) => (
                              <li key={i} className="prose-cn text-sm2 text-ink-2">
                                {l}
                              </li>
                            ))}
                          </ul>
                        ) : (
                          <span
                            className="rounded-btn border border-dashed px-10 py-6 text-sm2"
                            style={{ borderColor: SEV.unver.border, color: SEV.unver.color }}
                          >
                            这一项是空的 —— 空不等于「这一章没有」，也可能是没抽到
                          </span>
                        )}
                        {f.more ? <span className="text-tag text-ink-5">{f.more}</span> : null}
                      </div>
                    ))}

                    <div className="prose-cn text-micro text-ink-5">
                      语义契约与秘密时间线来自 Stage2；场景目的、边界、顺序与事实重新分布由 Stage4 编译。校验结论仍只来自 stage4 报告。
                    </div>
                  </PaneScroll>
                </Panel>
              }
            />
          </section>
          )}
        </QueryStates>
      </ScreenFrame>
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

function PlanSceneRow({
  scene,
  selected,
  onClick,
}: {
  scene: PlanSceneView
  selected: boolean
  onClick: () => void
}) {
  return (
    <div
      onClick={onClick}
      className={cx(
        'flex cursor-pointer flex-col gap-9 border-b border-line-row px-18 py-14',
        selected && 'bg-accent-bg',
      )}
    >
      <div className="flex flex-wrap items-center gap-8">
        <Mono className="rounded-tag bg-inset px-7 py-1 text-mini">
          第 {scene.i} 场 · {scene.slot}
        </Mono>
        <span className="rounded-tag bg-inset px-7 py-1 text-mini text-ink-3">{scene.role}</span>
        <span className="rounded-tag bg-inset px-7 py-1 text-mini text-ink-3">{scene.delivery}</span>
        <span className="rounded-tag bg-inset px-7 py-1 text-mini text-ink-3">{scene.transition}</span>
        <span className="ml-auto flex items-center gap-8">
          <Mono className="text-micro text-ink-4">预算 {scene.budget}</Mono>
        </span>
      </div>

      <div className="prose-cn text-section text-ink-2">
        <span className="text-tag text-ink-4">场景目的　</span>{scene.purpose}
      </div>

      <ul className="m-0 flex list-none flex-col gap-4">
        {scene.requiredFacts.map((fact, i) => (
          <li key={i} className="prose-cn text-section text-ink-2">
            <span className="text-tag text-ink-4">必须成立　</span>{fact}
          </li>
        ))}
      </ul>

      <div className="prose-cn rounded-btn bg-inset px-10 py-8 text-sm2 text-ink-2">
        <span className="text-tag text-ink-4">转折　</span>{scene.turnFact}
      </div>

      {scene.entryState.length || scene.exitState.length ? (
        <div className="grid gap-6 text-sm2" style={{ gridTemplateColumns: '1fr 1fr' }}>
          <div><span className="text-tag text-ink-4">进入状态　</span>{scene.entryState.join('；') || '—'}</div>
          <div><span className="text-tag text-ink-4">退出状态　</span>{scene.exitState.join('；') || '—'}</div>
        </div>
      ) : null}

      {scene.contextFacts.length ? (
        <div className="prose-cn text-sm2 text-ink-4">
          上下文事实（不重复宣告）：{scene.contextFacts.join('；')}
        </div>
      ) : null}

      {scene.verbatim.map((v, i) => (
        <div key={i} className="rounded-r-btn border-l-3 border-l-hinge bg-hinge-bg px-12 py-8">
          <div className="text-micro text-hook-ink">必须逐字写进去（{scene.verbatimChars} 字）</div>
          <div className="prose-cn text-sm2 text-ink-2">{v}</div>
        </div>
      ))}

      {scene.holds.map((h, i) => (
        <div key={i} className="flex items-baseline gap-8 text-sm2">
          <span className="flex-none rounded-tag bg-inset px-6 py-1 text-micro text-ink-4">{h.k}</span>
          <span className="prose-cn text-ink-2">{h.v}</span>
        </div>
      ))}

      {scene.stages.length ? (
        <div className="flex flex-col gap-4 rounded-btn border border-dashed border-line-btn px-10 py-8">
          <span className="text-micro text-ink-5">场景舞台（跨时空时按这里分段）</span>
          {scene.stages.map((stage, i) => (
            <div key={i} className="flex items-center gap-7 text-tag text-ink-3">
              <span
                className={cx(
                  'rounded-tag px-6 py-1 text-micro',
                  stage.layerPresent ? 'bg-inset text-ink-3' : 'bg-recall-bg text-recall',
                )}
              >
                {stage.layer}
              </span>
              <span>{stage.place}</span>
              <span className="text-ink-4">{stage.present}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}
