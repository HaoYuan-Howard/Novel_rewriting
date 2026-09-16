import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { FRESH, MUTED, SEV } from '@/constants/vocabulary'
import { useSampleCoverage } from '@/hooks/useSample'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { ComparePanes, PaneScroll } from '@/components/compare/ComparePanes'
import { CompareRows } from '@/components/compare/CompareRows'
import { QueryStates } from '@/components/state/QueryStates'
import { LoadFailed, NotRunYet } from '@/components/state/States'
import { LinkButton } from '@/components/ui/ActionButton'
import { ChapterGrid } from '@/components/ui/ChapterGrid'
import { ChapterStrip } from '@/components/ui/ChapterStrip'
import { HighlightedText } from '@/components/ui/HighlightedText'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { FreshnessBadge } from '@/components/vocab/Freshness'
import { SevChip, SevCounts, SevMark } from '@/components/vocab/Sev'
import { cx } from '@/lib/cx'
import {
  useStage5Screen,
  type FindingView,
} from './useStage5Screen'

/**
 * 成稿 ↔ 材料的只读监控屏。人工改稿 / 授权 / 闭合写面已退役。
 */
export function Stage5Screen() {
  const vm = useStage5Screen()
  const nav = useNavigate()
  const { hasSample } = useSampleCoverage()
  const draftPane = useRef<HTMLDivElement>(null)

  // 点中一条判据后，右栏成稿里第一处证据高亮滚到视野中间
  useEffect(() => {
    draftPane.current
      ?.querySelector('mark')
      ?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  }, [vm.selFinding])


  return (
    <ScreenFrame screen="stage5" onJumpChapter={(n) => nav('/n/' + vm.sid + '/stage5/' + n)}>
      <>
        <QueryStates
          q={vm.draftQ}
          loadingLabel="读这一章的成稿"
          missing={{
            title: '成稿 ↔ 材料',
            chapters: [vm.ordinal],
            ordinal: vm.ordinal,
            path: 'stage5',
          }}
        >
          {(d) => (
          <section className="flex flex-col">
            <header className="sticky top-0 z-10 flex flex-col gap-12 border-b border-line bg-sunken-3 px-30 pt-20 pb-14">
              <div className="flex items-end justify-between gap-24">
                <div className="flex flex-col gap-5">
                  <h1 className="m-0 text-h1 font-semibold">成稿 ↔ 材料 · 第 {vm.ordinal} 章</h1>
                  <div className="text-body text-ink-3">
                    左边是这一章判出来的问题，右边是成稿与原文对照；本屏只读监控。
                  </div>
                </div>
                <ChapterStrip
                  items={d.chapters.map((n) => ({
                    n,
                    active: n === vm.ordinal,
                    dim: !hasSample(vm.sid, n),
                    // 编辑中有未保存改动时，跳章先挽留
                    go: () => {
                      if (vm.guardLeave()) nav('/n/' + vm.sid + '/stage5/' + n)
                    },
                  }))}
                />
              </div>
              <div className="flex flex-wrap items-center gap-10">
                <span className="rounded-chip bg-inset px-9 py-3 text-body text-ink-3">
                  {vm.counts}
                </span>
                <span
                  className="rounded-chip px-9 py-3 text-sm2"
                  style={
                    vm.missingBad
                      ? { color: SEV.hard.color, background: SEV.hard.bg }
                      : { color: FRESH.fresh.color, background: FRESH.fresh.bg }
                  }
                >
                  {vm.missingLabel}
                </span>
                <span
                  className={cx(
                    'rounded-chip px-9 py-3 text-sm2',
                    !vm.invalidBad && 'bg-inset text-ink-3',
                  )}
                  style={
                    vm.invalidBad ? { color: SEV.hard.color, background: SEV.hard.bg } : undefined
                  }
                >
                  {vm.invalidLabel}
                </span>
              </div>
              {vm.legacyNotice ? (
                <div className="prose-cn rounded-block border border-warn-line bg-warn-bg px-12 py-8 text-sm2 text-warn-ink">
                  {vm.legacyNotice}
                </div>
              ) : null}
              {vm.reviewNotice ? (
                <div className="prose-cn rounded-block border border-warn-line bg-warn-bg px-12 py-8 text-sm2 text-warn-ink">
                  {vm.reviewNotice}
                </div>
              ) : null}
            </header>



            {vm.trimAudit ? (
              <div className="px-30 pt-14">
                <Panel>
                  <PaneHeader
                    title="篇幅裁剪核验（只读）"
                    meta={'第 ' + vm.trimAudit.output_ordinal + ' 章'}
                  />
                  <div className="flex flex-col gap-10 px-18 py-14">
                    <div className="prose-cn text-body text-ink-3">
                      裁剪核验与销账已改由自动流水线处理；本屏只展示当前审计。
                    </div>
                    <div className="grid gap-10 md:grid-cols-2">
                      <div className="rounded-block border border-line bg-card px-12 py-10">
                        <div className="mb-4 text-tag font-semibold text-ink-4">审计原因</div>
                        <div className="prose-cn text-body text-ink-2">{vm.trimAudit.reason}</div>
                      </div>
                      <div className="rounded-block border border-line bg-card px-12 py-10">
                        <div className="mb-4 text-tag font-semibold text-ink-4">核验上下文</div>
                        <div className="prose-cn whitespace-pre-wrap text-body text-ink-2">
                          {vm.trimAudit.context}
                        </div>
                      </div>
                    </div>
                  </div>
                </Panel>
              </div>
            ) : null}


            {!vm.ctxExists ? (
              <div className="px-30 pt-16">
                <NotRunYet
                  title="写作上下文还没建"
                  body={
                    vm.ctxNote ??
                    '这一章的写手上下文（writer_context）不在盘上。没有材料就没有成稿——先去 stage4 把计划跑出来并重建上下文，这一步不花钱。这里不显示假材料。'
                  }
                  action={
                    <LinkButton onClick={() => nav('/n/' + vm.sid + '/stage4/' + vm.ordinal)}>
                      去 stage4 计划屏
                    </LinkButton>
                  }
                />
              </div>
            ) : (
              <ComparePanes
                className="px-30 pt-14 pb-8"
                ratio="0.62fr 1.6fr"
                sticky="left"
                left={
                  <Panel className="flex h-full min-h-0 flex-col">
                    <PaneHeader
                      title="判据报了什么"
                      right={
                        <span
                          className={cx(
                            'rounded-tag px-8 py-2 text-tag font-semibold',
                            !vm.hasHard && 'bg-inset text-ink-3',
                          )}
                          style={
                            vm.hasHard
                              ? { color: SEV.hard.color, background: SEV.hard.bg }
                              : undefined
                          }
                        >
                          {vm.findings.length} 条
                        </span>
                      }
                    />
                    <div className="flex flex-wrap items-center gap-6 border-b border-line-soft px-16 py-9">
                      {(['chapter', 'book', 'index'] as const).map((k) => (
                        <button
                          key={k}
                          type="button"
                          onClick={() => vm.setTab(k)}
                          className={cx(
                            'cursor-pointer rounded-btn border px-11 py-5 text-sm2 whitespace-nowrap',
                            vm.tab === k
                              ? 'border-ink bg-ink text-card'
                              : 'border-line-btn bg-card text-ink-2',
                          )}
                        >
                          {vm.tabLabels[k]}
                        </button>
                      ))}
                    </div>
                    <div className="border-b border-line-soft px-16 py-9">
                      <SevCounts counts={vm.sevCounts} />
                    </div>

                    <PaneScroll>
                      {vm.reviewQ.isError ? (
                        <div className="p-14">
                          <LoadFailed error={vm.reviewQ.error} compact />
                        </div>
                      ) : vm.tab === 'chapter' ? (
                        vm.findings.length ? (
                          vm.findings.map((f, i) => (
                            <FindingRow
                              key={f.key}
                              f={f}
                              selected={i === vm.selFinding}
                              onClick={() => vm.setSelFinding(i)}
                            />
                          ))
                        ) : (
                          <div className="prose-cn p-16 text-sm2 text-ink-4">
                            这一章没有结论。空不等于「写对了」——只说明这一轮的判据没有报出东西。
                          </div>
                        )
                      ) : vm.tab === 'book' ? (
                        vm.bookFindings.map((f, i) => (
                          <FindingRow
                            key={f.key}
                            f={f}
                            selected={i === vm.selBookFinding}
                            onClick={() => vm.setSelBookFinding(i)}
                          />
                        ))
                      ) : (
                        <div className="flex flex-col">
                          {vm.reviewIndex.map((r) => (
                            <button
                              key={r.no}
                              type="button"
                              onClick={() => nav('/n/' + vm.sid + '/stage5/' + r.ordinal)}
                              className={cx(
                                'grid cursor-pointer items-center gap-6 border-b border-line-row px-16 py-9 text-left',
                                r.active && 'bg-page',
                              )}
                              style={{
                                gridTemplateColumns:
                                  'minmax(0,1fr) 34px 34px 38px 44px',
                              }}
                            >
                              <Mono className="truncate text-mini" title={r.no}>{r.no}</Mono>
                              <CountCell n={r.hard} sev="hard" />
                              <CountCell n={r.info} sev="info" />
                              <CountCell n={r.unver} sev="unver" />
                              <CountCell n={r.unresolved} sev="metric" />
                            </button>
                          ))}
                        </div>
                      )}
                      <div className="prose-cn border-t border-line-soft px-16 py-11 text-tag text-ink-4">
                        {vm.findNote}
                      </div>
                    </PaneScroll>
                  </Panel>
                }
                right={
                  <div ref={draftPane} className="flex flex-col gap-14">
                    {/* 材料（可折叠） */}
                    <Panel>
                      <PaneHeader
                        title="这一章的材料"
                        meta={vm.matNumbers.map((n) => n.k + ' ' + n.v).join(' · ')}
                        right={
                          <LinkButton onClick={vm.toggleMat}>
                            {vm.matOpen ? '收起 −' : '展开 +'}
                          </LinkButton>
                        }
                      />
                      {vm.matOpen ? (
                        <div className="flex flex-col gap-12 px-18 py-14">
                          {/* 与写手同一份下发面（build_material 的 blocks）——逐块原样展示 */}
                          {vm.matBlocks.map((block, i) => (
                            <div
                              key={i}
                              className="prose-cn whitespace-pre-wrap border-t border-line-row pt-10 text-sm2 text-ink-2 first:border-t-0 first:pt-0"
                            >
                              {block}
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </Panel>

                    {/* 成稿 */}
                    {vm.draft ? (
                      <Panel>
                        <div className="flex flex-wrap items-center gap-10 border-b border-line-soft bg-sunken-2 px-18 py-11">
                          <Mono
                            className="rounded-chip px-9 py-2 text-tag font-medium text-card"
                            style={{ background: SEV.metric.color }}
                          >
                            {vm.draft.title}
                          </Mono>
                          <FreshnessBadge f={vm.draft.freshness} />
                          <Mono className="text-micro text-ink-4">{vm.draft.numbers}</Mono>
                          {/* 锚点覆盖率常驻，不是 hover 才看得到 */}
                          <span
                            className="ml-auto rounded-chip border px-9 py-3 text-sm2 font-semibold"
                            style={{
                              color: vm.anchorColor,
                              background: vm.anchorBg,
                              borderColor: vm.anchorBorder,
                              borderStyle: vm.anchorLow ? 'dashed' : 'solid',
                            }}
                            title={vm.alignNote}
                          >
                            锚 {vm.anchorLabel}
                          </span>
                          <LinkButton onClick={vm.toggleSide}>
                            {vm.sideBySide ? '只看成稿' : '并排看原文'}
                          </LinkButton>
                        </div>

                        <div className="flex flex-wrap items-center gap-10 border-b border-line-soft px-18 py-8 text-tag text-ink-4">
                          <span>{vm.alignMeta}</span>
                          <span className="prose-cn">{vm.alignNote}</span>
                        </div>

                        {vm.evidenceHint ? (
                          <div
                            className="prose-cn border-b border-line-soft px-18 py-8 text-sm2"
                            style={
                              vm.evidenceHit
                                ? { color: SEV.metric.color, background: SEV.info.wash }
                                : { color: SEV.unver.color, background: SEV.unver.bg }
                            }
                          >
                            {vm.evidenceHint}
                          </div>
                        ) : null}

                        {vm.sideBySide ? (
                          <CompareRows
                            captions={['原文', '重构稿']}
                            rows={vm.rows.map((r) => ({
                              key: r.key,
                              left: r.left,
                              right: r.right,
                              anchored: r.anchored,
                              note: r.badge + ' · ' + r.note + ' · ' + r.meta,
                            }))}
                            emptyNote={
                              <div className="prose-cn px-18 py-14 text-sm2 text-ink-4">
                                这一份成稿没有切出对齐块。没有对齐块就没法逐块对照——只能整篇读。
                              </div>
                            }
                          />
                        ) : (
                          <div className="flex flex-col gap-8 px-18 py-16">
                            {vm.paragraphs.map((parts, i) => (
                              <p
                                key={i}
                                className="prose-cn m-0 max-w-[var(--measure-solo)] text-read-xl text-ink"
                              >
                                <HighlightedText parts={parts} />
                              </p>
                            ))}
                          </div>
                        )}

                        <Mono className="block border-t border-line-soft px-18 py-9 text-micro text-ink-5">
                          {vm.draft.prov}
                        </Mono>
                      </Panel>
                    ) : (
                      <NotRunYet
                        title="这一章还没有成稿"
                        body={
                          d.written === 0
                            ? 'Stage5 还没有 canonical 成稿。全书写作由自动流水线推进；本屏只读监控。'
                            : '计划里有这一章，但盘上没有它的 canonical 成稿。可在下方用 --chapters 定向补写。'
                        }
                        action={undefined}
                      />
                    )}


                    {/* 章索引 */}
                    <Panel>
                      <PaneHeader title="全书成稿" meta={d.index.length + ' 章'} />
                      <div className="px-18 py-14">
                        <ChapterGrid
                          items={vm.index}
                          onPick={(o) => nav('/n/' + vm.sid + '/stage5/' + o)}
                        >
                          {(i) => (
                            <>
                              {/* freshness=null = 本章还没有稿——按「不在盘上」画 */}
                              <FreshnessBadge f={i.freshness ?? 'missing'} size={8} />
                              <Mono className="text-mini">{i.no}</Mono>
                              <span className="text-tag text-ink-3">{i.status}</span>
                              <Mono className="text-micro text-ink-5">{i.chars}</Mono>
                              <Mono className="text-micro text-ink-5">重合 {i.overlap}</Mono>
                            </>
                          )}
                        </ChapterGrid>
                      </div>
                    </Panel>
                  </div>
                }
              />
            )}
          </section>
          )}
        </QueryStates>
      </>
    </ScreenFrame>
  )
}

function FindingRow({
  f,
  selected,
  onClick,
}: {
  f: FindingView
  selected?: boolean
  onClick?: () => void
}) {
  return (
    <div
      onClick={onClick}
      className={cx(
        'flex flex-col gap-6 border-b border-line-row px-16 py-12',
        onClick && 'cursor-pointer',
      )}
      style={
        selected
          ? { background: SEV.info.wash, boxShadow: 'inset 3px 0 0 var(--color-accent)' }
          : undefined
      }
    >
      <div className="flex flex-wrap items-center gap-8">
        <SevMark sev={f.sev} />
        <span className="text-body font-semibold">{f.item}</span>
        <SevChip sev={f.sev} />
        <Mono className="ml-auto text-micro text-ink-5">{f.evidenceLabel}</Mono>
      </div>
      <Mono className="text-micro text-ink-5">{f.rule}</Mono>
      <div className="prose-cn text-sm2 text-ink-2">{f.detail}</div>
      {f.wontfixLabel ? (
        <div className="prose-cn rounded-tag bg-inset px-8 py-5 text-tag text-ink-3">
          {f.wontfixLabel}
        </div>
      ) : null}
    </div>
  )
}

function CountCell({ n, sev }: { n: number; sev: 'hard' | 'info' | 'unver' | 'metric' }) {
  return (
    <Mono
      className="rounded-tag py-1 text-center text-mini"
      title={SEV[sev].label}
    >
      <span
        style={
          n
            ? { color: SEV[sev].color, background: SEV[sev].bg, padding: '1px 4px', borderRadius: 4 }
            : { color: MUTED }
        }
      >
        {n}
      </span>
    </Mono>
  )
}
