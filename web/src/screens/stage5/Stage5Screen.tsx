import { useNavigate } from 'react-router-dom'
import { ARM, FRESH, MUTED, SEV } from '@/constants/vocabulary'
import { useAskJob, useChrome } from '@/hooks/useChrome'
import { useSampleCoverage } from '@/hooks/useSample'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { ComparePanes, PaneScroll } from '@/components/compare/ComparePanes'
import { CompareRows } from '@/components/compare/CompareRows'
import { QueryStates } from '@/components/state/QueryStates'
import { LoadFailed, NotRunYet } from '@/components/state/States'
import { ActionButton, LinkButton } from '@/components/ui/ActionButton'
import { ChapterStrip } from '@/components/ui/ChapterStrip'
import { HighlightedText } from '@/components/ui/HighlightedText'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { FreshnessBadge } from '@/components/vocab/Freshness'
import { SevChip, SevCounts, SevMark } from '@/components/vocab/Sev'
import { cx } from '@/lib/cx'
import { useStage5Screen, type FindingView } from './useStage5Screen'

/**
 * `/api/enums` 用 `extra=['--chapters']` 标识这条带值动作，但值要到点击时才有。
 * 该 flag 已在动作身份里时，运行期只追加值；否则追加完整 flag/value 对。
 */
export function chapterSelectionArgs(extra: readonly string[] | null, value: string): string[] {
  const chapters = value.trim()
  return extra?.at(-1) === '--chapters' ? [chapters] : ['--chapters', chapters]
}

/**
 * 成稿 ↔ 材料（吃掉了原本独立的「验收 / 修订」屏）。
 * 左 = 判据报了什么，中 / 右 = 成稿 ↔ 原文，就地改。
 */
export function Stage5Screen() {
  const vm = useStage5Screen()
  const nav = useNavigate()
  const ask = useAskJob()
  const { askConfirm } = useChrome()
  const { hasSample } = useSampleCoverage()

  return (
    <ScreenFrame screen="stage5" onJumpChapter={(n) => nav('/n/' + vm.sid + '/stage5/' + n)}>
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
                    左边是这一章判出来的问题，右边是可编辑的成稿，就地改、保存落成新的一轮。
                  </div>
                </div>
                <ChapterStrip
                  items={d.chapters.map((n) => ({
                    n,
                    active: n === vm.ordinal,
                    dim: !hasSample(vm.sid, n),
                    go: () => nav('/n/' + vm.sid + '/stage5/' + n),
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
                <LinkButton
                  className="ml-auto"
                  onClick={() => nav('/n/' + vm.sid + '/depth')}
                  title="回去改深度 / 浅层的分配"
                >
                  重挑深度章节
                </LinkButton>
              </div>
              <div className="prose-cn text-tag text-ink-4">{vm.depthNote}</div>
            </header>

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
                        vm.bookFindings.map((f) => <FindingRow key={f.key} f={f} />)
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
                                gridTemplateColumns: '58px 62px minmax(0,1fr) 34px 34px 38px 44px',
                              }}
                            >
                              <Mono className="text-mini">{r.no}</Mono>
                              <span className="text-micro text-ink-4">{r.mode}</span>
                              <Mono className="text-micro text-ink-4">{r.chars}</Mono>
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
                  <div className="flex flex-col gap-14">
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
                            style={{
                              background: vm.draft.mode === 'deep' ? ARM.deep : ARM.shallow,
                            }}
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
                          <LinkButton onClick={vm.toggleEdit}>{vm.editing ? '读' : '改'}</LinkButton>
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

                        {vm.editing ? (
                          <div className="flex flex-col gap-10 px-18 py-14">
                            {vm.editSegments.map((segment) => (
                              <label key={segment.slot} className="flex flex-col gap-5">
                                <Mono className="text-micro text-ink-5">{segment.slot}</Mono>
                                <textarea
                                  value={segment.text}
                                  onChange={(e) => vm.setSegmentText(segment.slot, e.target.value)}
                                  className="min-h-[180px] w-full resize-y rounded-block border border-line bg-card px-14 py-12 text-read leading-[2.05] text-ink"
                                />
                              </label>
                            ))}
                            <div className="flex items-center gap-10">
                              <span className="prose-cn text-tag text-ink-4">{vm.editNote}</span>
                              <ActionButton
                                className="ml-auto"
                                action={{ kind: 'stage5.write', extra: ['--apply-edit'], paid: false }}
                                disabled={
                                  vm.save.isPending ||
                                  vm.editSegments.length === 0 ||
                                  vm.editSegments.some((segment) => !segment.text.trim())
                                }
                                onClick={() =>
                                  askConfirm({
                                    req: {
                                      kind: 'stage5.write',
                                      label: '章节改稿 · 第 ' + vm.ordinal + ' 章（人工稿落成新一轮）',
                                      // 确认框 preview 同一个 (node, flags)；一次性稿的真实路径由服务端执行时生成
                                      extra: ['--apply-edit', '(服务端一次性人工稿)'],
                                      mode: vm.saveMode,
                                      sid: vm.sid,
                                      ordinal: vm.ordinal,
                                    },
                                    run: () =>
                                      vm.save.mutateAsync({
                                        mode: vm.saveMode,
                                        segments: vm.editSegments,
                                      }),
                                  })
                                }
                              >
                                保存，落成新的一轮
                              </ActionButton>
                            </div>
                          </div>
                        ) : vm.sideBySide ? (
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
                        body="计划里有这一章，但盘上没有它的稿。下面的动作里挑一条把它写出来——注意深度 / 浅层是两条不同的臂。"
                      />
                    )}

                    {/* 动作 */}
                    <Panel className="flex flex-col gap-10 px-18 py-14">
                      <div className="flex items-center gap-10">
                        <span className="text-body font-semibold">对这一章 / 这些章跑点什么</span>
                        <Mono className="rounded-btn border border-line bg-card px-8 py-4 text-body">
                          --chapters
                        </Mono>
                        <input
                          value={vm.chaptersArg}
                          onChange={(e) => vm.setChaptersArg(e.target.value)}
                          className="w-[140px] rounded-btn border border-line-btn bg-card px-10 py-4 font-mono text-body"
                        />
                        <span className="text-tag text-ink-4">
                          确认框里会原样摆出将要执行的那条命令，点之前对一眼章号。
                        </span>
                      </div>
                      <div className="flex flex-wrap gap-8">
                        {vm.writeActions.map((a) => (
                          <ActionButton
                            key={a.kind + ':' + (a.mode ?? '') + ':' + (a.extra ?? []).join(',')}
                            action={a}
                            size="sm"
                            disabled={!vm.chaptersArg.trim()}
                            disabledReason="先填章号"
                            onClick={() =>
                              ask({
                                kind: a.kind,
                                label: a.label,
                                extra: a.extra,
                                mode: a.mode ?? null,
                                args: chapterSelectionArgs(a.extra, vm.chaptersArg),
                                required_inputs: a.required_inputs,
                                sid: vm.sid,
                                ordinal: null,
                              })
                            }
                          >
                            {a.label.replace('stage5 ', '')}
                          </ActionButton>
                        ))}
                      </div>
                      <div className="prose-cn text-tag text-ink-4">{vm.gateNote}</div>
                    </Panel>

                    {/* 章索引 */}
                    <Panel>
                      <PaneHeader title="全书成稿" meta={d.index.length + ' 章'} />
                      <div className="flex flex-wrap gap-8 px-18 py-14">
                        {vm.index.map((i) => (
                          <button
                            key={i.no}
                            type="button"
                            onClick={() => nav('/n/' + vm.sid + '/stage5/' + i.ordinal)}
                            className={cx(
                              'flex cursor-pointer items-center gap-7 rounded-btn border px-9 py-5',
                              i.active ? 'border-ink bg-inset' : 'border-line bg-card',
                            )}
                          >
                            {/* freshness=null = 本章还没有稿——按「不在盘上」画 */}
                            <FreshnessBadge f={i.freshness ?? 'missing'} size={8} />
                            <Mono className="text-mini">{i.no}</Mono>
                            <span className="text-tag text-ink-3">{i.modes}</span>
                            <Mono className="text-micro text-ink-5">{i.chars}</Mono>
                            <Mono className="text-micro text-ink-5">重合 {i.overlap}</Mono>
                          </button>
                        ))}
                      </div>
                    </Panel>
                  </div>
                }
              />
            )}
          </section>
        )}
      </QueryStates>
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
