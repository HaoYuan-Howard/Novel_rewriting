import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { errorBody } from '@/api/errors'
import { FRESH, MUTED, SEV } from '@/constants/vocabulary'
import type { Finding } from '@/types/common'
import { useAskJob, useChrome } from '@/hooks/useChrome'
import { useSampleCoverage } from '@/hooks/useSample'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { ComparePanes, PaneScroll } from '@/components/compare/ComparePanes'
import { CompareRows } from '@/components/compare/CompareRows'
import { QueryStates } from '@/components/state/QueryStates'
import { ErrorHint, LoadFailed, NotRunYet } from '@/components/state/States'
import { ActionButton, LinkButton } from '@/components/ui/ActionButton'
import { ChapterGrid } from '@/components/ui/ChapterGrid'
import { ChapterStrip } from '@/components/ui/ChapterStrip'
import { ActorReasonFields } from '@/components/ui/ActorReasonFields'
import { HighlightedText } from '@/components/ui/HighlightedText'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { FreshnessBadge } from '@/components/vocab/Freshness'
import { SevChip, SevCounts, SevMark } from '@/components/vocab/Sev'
import { cx } from '@/lib/cx'
import {
  retainPretrimArgs,
  stage5WontfixSpec,
  useStage5Screen,
  type FindingView,
} from './useStage5Screen'
import { Stage5ClosurePanel } from './Stage5ClosurePanel'

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
  const draftPane = useRef<HTMLDivElement>(null)

  // 点中一条判据后，右栏成稿里第一处证据高亮滚到视野中间
  useEffect(() => {
    draftPane.current
      ?.querySelector('mark')
      ?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  }, [vm.selFinding])

  const askRetainPretrim = () => {
    if (!vm.trimAudit || !vm.trimActor.trim() || !vm.trimReason.trim()) return
    const args = retainPretrimArgs(vm.trimAudit.output_ordinal, vm.trimActor, vm.trimReason)
    askConfirm({
      req: {
        kind: 'stage5.review',
        label: '保留未裁剪正文 · 第 ' + vm.trimAudit.output_ordinal + ' 章',
        extra: ['--retain-pretrim'],
        args,
        sid: vm.sid,
        ordinal: vm.trimAudit.output_ordinal,
      },
      run: () => vm.retainPretrim.mutateAsync({
        actor: vm.trimActor.trim(),
        reason: vm.trimReason.trim(),
      }),
    })
  }

  const askWontfix = () => {
    const target = vm.wontfixTarget
    if (!target || target.blockedReason) return
    const spec = stage5WontfixSpec(
      target.scope,
      target.rule,
      target.evidenceKey,
      vm.wontfixReason,
    )
    if (!spec) return
    ask(
      {
        kind: 'stage5.review',
        label: 'wontfix · ' + target.item,
        extra: ['--wontfix'],
        args: [spec],
        sid: vm.sid,
        ordinal: null,
      },
      (job) => {
        if (job) vm.afterWontfix(target.key, job.id)
      },
    )
  }

  const askApplyPreview = () => {
    const preview = vm.editPreview.data
    if (
      !preview || !vm.editPreviewCurrent || !vm.editConfirmed
      || !vm.editActor.trim() || !vm.editReason.trim()
    ) return
    askConfirm({
      req: {
        kind: 'stage5.write',
        label: '章节改稿 · 第 ' + vm.ordinal + ' 章 · ' + preview.required_confirmation,
        extra: ['--apply-edit'],
        args: [
          '(服务端一次性人工稿)',
          '--actor', vm.editActor.trim(),
          '--reason', vm.editReason.trim(),
          '--current-chapter-sha256', preview.current_chapter_sha256,
          '--preview-subject-sha256', preview.preview_subject_sha256,
          '--edit-confirmation', preview.required_confirmation,
        ],
        required_inputs: vm.editAction?.required_inputs,
        sid: vm.sid,
        ordinal: vm.ordinal,
      },
      run: vm.applyPreview,
    })
  }

  return (
    <ScreenFrame screen="stage5" onJumpChapter={(n) => nav('/n/' + vm.sid + '/stage5/' + n)}>
      <>
        <Stage5ClosurePanel
          sid={vm.sid}
          onJumpChapter={(n) => nav('/n/' + vm.sid + '/stage5/' + n)}
        />
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
                    title="篇幅裁剪核验待销账"
                    meta={'第 ' + vm.trimAudit.output_ordinal + ' 章 · 只允许保留未裁剪正文'}
                  />
                  <div className="flex flex-col gap-10 px-18 py-14">
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
                    {vm.retainPretrim.isError ? (
                      <div className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-body text-warn-ink">
                        销账失败：{errorBody(vm.retainPretrim.error)}
                        <ErrorHint error={vm.retainPretrim.error} />
                      </div>
                    ) : null}
                    {vm.retainPretrim.data?.state === 'failed' ? (
                      <div className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-body text-warn-ink">
                        retain-pretrim 作业没跑成；审计仍未销账，完整输出在作业抽屉。
                      </div>
                    ) : null}
                    <ActorReasonFields
                      actor={vm.trimActor}
                      reason={vm.trimReason}
                      onActor={vm.setTrimActor}
                      onReason={vm.setTrimReason}
                      note="该决定由服务端绑定当前 TrimAudit 与当前正文摘要；这里只能确认保留 pretrim，不能采纳任何裁剪候选。"
                      actions={
                        <ActionButton
                          action={vm.retainPretrimAction}
                          disabled={
                            !vm.retainPretrimAction ||
                            vm.retainPretrim.isPending ||
                            !vm.trimActor.trim() ||
                            !vm.trimReason.trim()
                          }
                          disabledReason={
                            !vm.retainPretrimAction
                              ? '服务端未提供 retain-pretrim 动作'
                              : '请先填写自报身份与保留理由'
                          }
                          onClick={askRetainPretrim}
                        >
                          确认保留未裁剪正文
                        </ActionButton>
                      }
                    />
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
                      {/* 本屏唯一能产出这些判据的入口。常驻而不是只在空态出现：
                          改完稿要重跑一轮，报告才跟得上正文。 */}
                      <ActionButton
                        className="ml-auto"
                        size="sm"
                        action={vm.reviewAction}
                        onClick={() =>
                          ask({
                            kind: 'stage5.review',
                            label: 'stage5 全书验收',
                            extra: null,
                            required_inputs: vm.reviewAction?.required_inputs,
                            sid: vm.sid,
                            ordinal: null,
                          })
                        }
                      >
                        跑全书验收
                      </ActionButton>
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
                      {vm.wontfixTarget && vm.tab !== 'index' ? (
                        <div className="flex flex-col gap-8 border-t border-line-soft bg-sunken-2 px-16 py-12">
                          <div className="flex flex-wrap items-center gap-7">
                            <span className="text-body font-semibold">人工 wontfix</span>
                            <Mono className="text-micro text-ink-5">
                              {vm.wontfixTarget.scope}|{vm.wontfixTarget.rule}|
                              {vm.wontfixTarget.evidenceKey ?? '缺 evidence_key'}
                            </Mono>
                          </div>
                          {vm.wontfixTarget.wontfix ? (
                            <div className="prose-cn rounded-block border border-line bg-card px-11 py-8 text-sm2 text-ink-3">
                              已有审计：{vm.wontfixTarget.wontfix.why}（{vm.wontfixTarget.wontfix.at}）
                            </div>
                          ) : null}
                          {vm.wontfixTarget.blockedReason ? (
                            <div className="prose-cn text-sm2 text-warn-ink">
                              {vm.wontfixTarget.blockedReason}
                            </div>
                          ) : null}
                          <label className="flex flex-col gap-4 text-tag text-ink-3">
                            wontfix 理由
                            <input
                              value={vm.wontfixReason}
                              disabled={!!vm.wontfixTarget.blockedReason}
                              onChange={(event) => vm.setWontfixReason(event.target.value)}
                              placeholder="说明核对了什么，以及为什么决定不修这一条"
                              className="rounded-btn border border-line-btn bg-card px-10 py-8 text-body font-normal text-ink disabled:opacity-45"
                            />
                          </label>
                          <ActionButton
                            action={vm.wontfixAction}
                            size="sm"
                            disabled={
                              !vm.wontfixAction ||
                              !!vm.wontfixTarget.blockedReason ||
                              !vm.wontfixReason.trim()
                            }
                            disabledReason={
                              !vm.wontfixAction
                                ? '服务端未提供 stage5.review --wontfix 动作'
                                : vm.wontfixTarget.blockedReason ?? '请先填写 wontfix 理由'
                            }
                            onClick={askWontfix}
                          >
                            记为 wontfix
                          </ActionButton>
                        </div>
                      ) : null}
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
                            {vm.editPreview.isError ? (
                              <div className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-body text-warn-ink">
                                只读预检失败：{errorBody(vm.editPreview.error)}
                                <ErrorHint error={vm.editPreview.error} />
                              </div>
                            ) : null}
                            {vm.editPreview.data && !vm.editPreviewCurrent ? (
                              <div className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-body text-warn-ink">
                                正文在 preview 后又改过；旧双 SHA 与归一稿已失效，必须重新只读预检。
                              </div>
                            ) : null}
                            {vm.editPreview.data ? (
                              <div className="flex flex-col gap-10 rounded-card border border-line bg-sunken-2 p-14">
                                <div className="flex flex-wrap items-baseline gap-8">
                                  <span className="text-body font-semibold">服务端只读预检</span>
                                  <Mono className="rounded-tag bg-inset px-7 py-2 text-mini">
                                    confirmation={vm.editPreview.data.required_confirmation}
                                  </Mono>
                                  <span className="text-tag text-ink-4">
                                    finding {vm.editPreview.data.findings.length} 条
                                    {' · '}当前 HARD {vm.editPreview.data.current_hard_findings.length}
                                    {' · '}新增 HARD {vm.editPreview.data.introduced_hard_findings.length}
                                    {' · '}消除 HARD {vm.editPreview.data.resolved_hard_findings.length}
                                  </span>
                                </div>
                                <span className="prose-cn text-sm2 text-ink-4">
                                  这些结论、归一稿和确认语义都来自 preview；浏览器不按 rule 重判能否落盘。
                                  apply 时只原样回传当前预览的归一稿与两枚 SHA。
                                </span>
                                {vm.editPreview.data.required_confirmation === 'working_draft' ? (
                                  <div className="rounded-block border border-warn-line bg-warn-bg px-12 py-9 text-sm2 text-warn-ink">
                                    本次预检要求 working_draft：候选新增了 HARD。明示确认后仍由后端决定是否接受；
                                    Stage5 闭合和下游会继续阻断，直到最终 Review 与 Commander 回执收口。
                                  </div>
                                ) : null}
                                <EditPreviewFindingSet
                                  title="本次预检全部 finding"
                                  findings={vm.editPreview.data.findings}
                                />
                                <div className="grid gap-7 md:grid-cols-3">
                                  <EditPreviewFindingSet
                                    title="当前已有 HARD"
                                    findings={vm.editPreview.data.current_hard_findings}
                                  />
                                  <EditPreviewFindingSet
                                    title="本次新增 HARD"
                                    findings={vm.editPreview.data.introduced_hard_findings}
                                  />
                                  <EditPreviewFindingSet
                                    title="本次消除 HARD"
                                    findings={vm.editPreview.data.resolved_hard_findings}
                                  />
                                </div>
                                <details className="text-micro text-ink-4">
                                  <summary className="cursor-pointer">查看 apply 双 SHA 与服务端归一槽位</summary>
                                  <Mono className="mt-5 block break-all">
                                    chapter {vm.editPreview.data.current_chapter_sha256}
                                  </Mono>
                                  <Mono className="mt-3 block break-all">
                                    preview {vm.editPreview.data.preview_subject_sha256}
                                  </Mono>
                                  <div className="mt-5 flex flex-col gap-3">
                                    {vm.editPreview.data.normalized_segments.map((segment) => (
                                      <Mono key={segment.slot} className="break-all">
                                        {segment.slot} · {segment.text.length} 字
                                      </Mono>
                                    ))}
                                  </div>
                                </details>
                                <ActorReasonFields
                                  actor={vm.editActor}
                                  reason={vm.editReason}
                                  onActor={vm.setEditActor}
                                  onReason={vm.setEditReason}
                                  reasonPlaceholder="说明本次改了什么，以及为何接受预检结论"
                                  note={
                                    vm.editPreviewCurrent
                                      ? 'actor/reason 与本次双 SHA 一起进入人工动作审计；apply 不会自动跑 Review。'
                                      : '当前 preview 已失效，重新预检后才能确认。'
                                  }
                                  actions={
                                    <label className="flex items-start gap-7 text-sm2 font-semibold text-ink-2">
                                      <input
                                        type="checkbox"
                                        aria-label={'确认以 ' + vm.editPreview.data.required_confirmation + ' apply 当前预览'}
                                        checked={vm.editConfirmed}
                                        disabled={!vm.editPreviewCurrent || vm.save.isPending}
                                        onChange={(event) => vm.setEditConfirmed(event.target.checked)}
                                      />
                                      我已阅读服务端 finding，并确认以
                                      <Mono>{vm.editPreview.data.required_confirmation}</Mono>
                                      apply 当前归一稿与双 SHA
                                    </label>
                                  }
                                />
                              </div>
                            ) : null}
                            {vm.save.isError ? (
                              <div className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-body text-warn-ink">
                                apply 失败：{errorBody(vm.save.error)}
                                <ErrorHint error={vm.save.error} />
                              </div>
                            ) : null}
                            {vm.save.data?.state === 'failed' || vm.save.data?.state === 'unknown' ? (
                              <div className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-body text-warn-ink">
                                改稿作业终态为 {vm.save.data.state}；不能据此认定落成新一轮，
                                请在下方作业抽屉核对日志。
                              </div>
                            ) : null}
                            <div className="flex flex-wrap items-center gap-10">
                              <span className="prose-cn text-tag text-ink-4">{vm.editNote}</span>
                              <LinkButton
                                className="ml-auto"
                                disabled={
                                  vm.editPreview.isPending ||
                                  vm.editSegments.length === 0 ||
                                  vm.editSegments.some((segment) => !segment.text.trim())
                                }
                                title="typed 槽位须完整且非空；preview 只读、不落盘"
                                onClick={vm.previewEdit}
                              >
                                {vm.editPreview.isPending ? '只读预检中…' : '只读预检改稿'}
                              </LinkButton>
                              <ActionButton
                                action={vm.editAction}
                                disabled={
                                  !vm.editAction ||
                                  vm.save.isPending ||
                                  !vm.editPreview.data ||
                                  !vm.editPreviewCurrent ||
                                  !vm.editConfirmed ||
                                  !vm.editActor.trim() ||
                                  !vm.editReason.trim()
                                }
                                disabledReason={
                                  !vm.editAction
                                    ? '服务端未提供 stage5.write --apply-edit 动作'
                                    : !vm.editPreview.data || !vm.editPreviewCurrent
                                      ? '请先对当前正文完成只读预检'
                                      : '请填写 actor/reason，并明示确认服务端要求的 confirmation'
                                }
                                onClick={askApplyPreview}
                              >
                                {vm.save.isPending
                                  ? 'apply 中…'
                                  : 'apply 当前预览'}
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
                        body={
                          d.written === 0
                            ? 'Stage5 还没有 canonical 成稿。授权后将按唯一闭卷契约写完整本书，并在确认框明确显示这是付费作业。'
                            : '计划里有这一章，但盘上没有它的 canonical 成稿。可在下方用 --chapters 定向补写。'
                        }
                        action={
                          d.written === 0 ? (
                            <ActionButton
                              action={vm.fullWriteAction}
                              disabled={!vm.fullWriteAction || vm.approvals.startWriting.isPending}
                              disabledReason={!vm.fullWriteAction ? '服务端未提供全书写作动作' : ''}
                              onClick={() =>
                                ask(
                                  {
                                    kind: 'stage5.write',
                                    label: 'Stage5 全书闭卷开写',
                                    extra: null,
                                    required_inputs: vm.fullWriteAction?.required_inputs,
                                    sid: vm.sid,
                                    ordinal: null,
                                  },
                                  undefined,
                                  vm.writingBefore(vm.fullWriteAction),
                                )
                              }
                            >
                              全书闭卷开写
                            </ActionButton>
                          ) : undefined
                        }
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
                            key={a.kind + ':' + (a.extra ?? []).join(',')}
                            action={a}
                            size="sm"
                            disabled={!vm.chaptersArg.trim()}
                            disabledReason="先填章号"
                            onClick={() =>
                              ask(
                                {
                                  kind: a.kind,
                                  label: a.label,
                                  extra: a.extra,
                                  args: chapterSelectionArgs(a.extra, vm.chaptersArg),
                                  required_inputs: a.required_inputs,
                                  sid: vm.sid,
                                  ordinal: null,
                                },
                                undefined,
                                vm.writingBefore(a),
                              )
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

function EditPreviewFindingSet({
  title,
  findings,
}: {
  title: string
  findings: Finding[]
}) {
  return (
    <details className="rounded-block border border-line-row bg-card px-11 py-9">
      <summary className="cursor-pointer text-tag font-semibold text-ink-3">
        {title} · {findings.length} 条
      </summary>
      {findings.length ? (
        <div className="mt-7 flex flex-col divide-y divide-line-row">
          {findings.map((finding, index) => (
            <div
              key={finding.rule + '|' + (finding.evidence_key ?? '') + '|' + index}
              className="flex flex-col gap-4 py-7 first:pt-0 last:pb-0"
            >
              <div className="flex items-center gap-6">
                <SevMark sev={finding.sev} />
                <span className="text-sm2 font-semibold text-ink-2">{finding.item}</span>
                <Mono className="ml-auto text-micro text-ink-5">{finding.rule}</Mono>
              </div>
              <span className="prose-cn text-tag text-ink-4">{finding.detail}</span>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-6 text-tag text-ink-4">服务端返回空集合。</div>
      )}
    </details>
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
