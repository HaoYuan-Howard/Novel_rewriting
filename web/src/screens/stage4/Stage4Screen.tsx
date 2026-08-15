import { useNavigate } from 'react-router-dom'
import { SEV } from '@/constants/vocabulary'
import { useSampleCoverage } from '@/hooks/useSample'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { ComparePanes, PaneScroll } from '@/components/compare/ComparePanes'
import { QueryStates } from '@/components/state/QueryStates'
import { ChapterStrip } from '@/components/ui/ChapterStrip'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { cx } from '@/lib/cx'
import { useStage4Screen, type PlanBeatView } from './useStage4Screen'

/**
 * 计划 ↔ 写作上下文。
 * 左边是「这一拍打算怎么写」，右边是「写手真正拿得到的材料」——
 * 计划里想到的东西如果没进材料，写手就看不到。
 */
export function Stage4Screen() {
  const vm = useStage4Screen()
  const nav = useNavigate()
  const { hasSample } = useSampleCoverage()

  return (
    <ScreenFrame screen="stage4" onJumpChapter={(n) => nav('/n/' + vm.sid + '/stage4/' + n)}>
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
                    左「这一拍打算怎么写」，右「写手手里实际会有什么」。
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
                <div className="flex flex-wrap items-center gap-8">
                  {vm.index.map((i) => (
                    <button
                      key={i.no}
                      type="button"
                      onClick={() => nav('/n/' + vm.sid + '/stage4/' + i.ordinal)}
                      className={cx(
                        'flex cursor-pointer items-center gap-7 rounded-btn border px-9 py-5',
                        i.active ? 'border-ink bg-inset' : 'border-line bg-card',
                      )}
                    >
                      <Mono className="text-mini">{i.no}</Mono>
                      <Mono className="text-micro text-ink-5">{i.budget}</Mono>
                      <Mono className="text-micro text-ink-5">{i.beats} 拍</Mono>
                      {i.hidden ? (
                        <Mono className="text-micro text-ink-5">瞒 {i.hidden}</Mono>
                      ) : null}
                    </button>
                  ))}
                </div>
                <div className="prose-cn text-body text-ink-2">{vm.strategy}</div>
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
                  <PaneHeader title="逐拍计划" meta={vm.beats.length + ' 拍'} sticky />
                  <div className="flex flex-col">
                    {vm.beats.map((b) => (
                      <PlanBeatRow
                        key={b.i}
                        b={b}
                        selected={vm.selBeat === b.i}
                        onClick={() => vm.toggleBeat(b.i)}
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

                    <div className="prose-cn rounded-btn-lg border border-dashed border-line-btn bg-page px-12 py-10 text-sm2 text-ink-4">
                      must_not_reveal 等只服务回写校验，不进写手 prompt——写手看不到内容才不会写出来。
                    </div>
                    <div className="prose-cn text-micro text-ink-5">
                      计划这一侧真正由 LLM 决定的只剩逐章目标 + 逐拍预算；拍怎么切是代码定的。在不在档的判定见本屏底部的 stage4 校验。
                    </div>
                  </PaneScroll>
                </Panel>
              }
            />
          </section>
        )}
      </QueryStates>
    </ScreenFrame>
  )
}

function PlanBeatRow({
  b,
  selected,
  onClick,
}: {
  b: PlanBeatView
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
        <Mono className="rounded-tag bg-inset px-7 py-1 text-mini">第 {b.i} 拍</Mono>
        <Mono className="text-micro text-ink-5">源拍 {b.fromBeats}</Mono>
        <span
          className={cx('rounded-tag px-7 py-1 text-mini', !b.layerUnknown && (
            b.layerPresent ? 'bg-inset text-ink-3' : 'bg-recall-bg text-recall'
          ))}
          style={
            b.layerUnknown
              ? { color: SEV.unver.color, background: SEV.unver.bg, border: '1px dashed ' + SEV.unver.border }
              : undefined
          }
        >
          {b.layer}
        </span>
        <span className="text-tag text-ink-3">{b.place}</span>
        <span className="ml-auto flex items-center gap-8">
          <Mono className="text-micro text-ink-4">
            预算 {b.budget} / 源文 {b.source}
          </Mono>
          <Mono className="rounded-tag px-6 py-1 text-micro">{b.ratio}</Mono>
        </span>
      </div>

      {/* 唯一需要数字对比条的地方：预算 / 源文 */}
      <div className="relative h-6 overflow-hidden rounded-nub bg-inset">
        <span
          className="absolute top-0 bottom-0 left-0"
          style={{ width: b.pct + '%', background: 'var(--color-accent-bar)' }}
        />
      </div>

      <div className="flex flex-wrap gap-6">
        {b.present.map((p) => (
          <span key={p} className="rounded-[11px] border border-line-2 bg-inset px-8 py-2 text-tag text-ink-2">
            {p}
          </span>
        ))}
      </div>

      <ul className="m-0 flex list-none flex-col gap-4">
        {b.events.map((e, i) => (
          <li key={i} className="prose-cn text-section text-ink-2">
            {e}
          </li>
        ))}
      </ul>

      {b.verbatim.map((v, i) => (
        <div key={i} className="rounded-r-btn border-l-3 border-l-hinge bg-hinge-bg px-12 py-8">
          <div className="text-micro text-hook-ink">必须逐字写进去（{b.verbatimChars} 字）</div>
          <div className="prose-cn text-sm2 text-ink-2">{v}</div>
        </div>
      ))}

      {b.holds.map((h, i) => (
        <div key={i} className="flex items-baseline gap-8 text-sm2">
          <span className="flex-none rounded-tag bg-inset px-6 py-1 text-micro text-ink-4">{h.k}</span>
          <span className="prose-cn text-ink-2">{h.v}</span>
        </div>
      ))}

      {b.segments.length ? (
        <div className="flex flex-col gap-4 rounded-btn border border-dashed border-line-btn px-10 py-8">
          <span className="text-micro text-ink-5">这一拍内部还要分段（时间层 / 地点会变）</span>
          {b.segments.map((s, i) => (
            <div key={i} className="flex items-center gap-7 text-tag text-ink-3">
              <span
                className={cx(
                  'rounded-tag px-6 py-1 text-micro',
                  s.layerPresent ? 'bg-inset text-ink-3' : 'bg-recall-bg text-recall',
                )}
              >
                {s.layer}
              </span>
              <span>{s.place}</span>
              <span className="text-ink-4">{s.present}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  )
}
