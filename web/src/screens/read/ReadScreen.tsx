import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { errorBody } from '@/api/errors'
import { FRESH, SEV } from '@/constants/vocabulary'
import { useSampleCoverage } from '@/hooks/useSample'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { Stage2NodeReviewPanel } from '@/components/gate/Stage2NodeReviewPanel'
import { ComparePanes, PaneScroll } from '@/components/compare/ComparePanes'
import { QueryStates } from '@/components/state/QueryStates'
import { ErrorHint } from '@/components/state/States'
import { ChapterStrip } from '@/components/ui/ChapterStrip'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { ActionButton, LinkButton } from '@/components/ui/ActionButton'
import { cx } from '@/lib/cx'
import type { BlueprintReviewNarrativeRelation } from '@/types/stages'
import { useReadScreen, type BeatView, type SegView } from './useReadScreen'

/**
 * 原文 ↔ 骨架。
 * 这一章的原文里，哪些内容被抽成了骨架、哪些漏了 —— 只能靠人对着看。
 * 并排结构本身就是需求，不能改成上下堆叠或 tab。
 */
export function ReadScreen() {
  const vm = useReadScreen()
  const nav = useNavigate()
  const { hasSample } = useSampleCoverage()
  const leftPane = useRef<HTMLDivElement>(null)

  // 选中一拍后，左栏原文里对应的那块滚到视野中间（stage1 同款 data 属性定位）
  useEffect(() => {
    if (!vm.selBeat) return
    leftPane.current
      ?.querySelector<HTMLElement>('[data-beat="' + vm.selBeat + '"]')
      ?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  }, [vm.selBeat])

  const strip = vm.chapters.map((n) => ({
    n,
    active: n === vm.ordinal,
    dim: !hasSample(vm.sid, n),
    go: () => nav('/n/' + vm.sid + '/read/' + n),
  }))

  return (
    <ScreenFrame screen="read" onJumpChapter={(n) => nav('/n/' + vm.sid + '/read/' + n)}>
      <QueryStates
        q={vm.query}
        loadingLabel="读这一章的骨架"
        missing={{
          title: '原文 ↔ 骨架',
          chapters: vm.chapters,
          ordinal: vm.ordinal,
          path: 'read',
        }}
      >
        {() => (
          <section className="flex flex-col">
            <header className="sticky top-0 z-10 flex flex-col gap-12 border-b border-line bg-sunken-3 px-30 pt-20 pb-14">
              <div className="flex items-end justify-between gap-24">
                <div className="flex flex-col gap-5">
                  <h1 className="m-0 text-h1 font-semibold">原文 ↔ 骨架 · 第 {vm.ordinal} 章</h1>
                  <div className="text-body text-ink-3">
                    这一章的原文里，哪些内容被抽成了骨架，哪些漏了。
                  </div>
                </div>
                <ChapterStrip items={strip} />
              </div>
              <BlueprintReviewWorkpackSummary
                review={vm.reviewWorkpack}
                ordinal={vm.ordinal}
                onOpenChapter={(chapter) => nav('/n/' + vm.sid + '/read/' + chapter)}
              />
              <BlueprintReviewConfirmation review={vm.blueprintReview} ordinal={vm.ordinal} />
              <Stage2NodeReviewPanel sid={vm.sid} node="stage2.blueprint" token={vm.reviewWorkpack.data?.review_token ?? null} />
              <div className="flex flex-wrap items-center gap-10">
                <span
                  className="rounded-chip px-10 py-3 text-body font-semibold"
                  style={{ color: vm.looseColor, background: vm.looseBg }}
                >
                  {vm.looseLabel}
                </span>
                <span className="rounded-chip bg-inset px-9 py-3 text-body text-ink-3">
                  {vm.counts}
                </span>
                <div className="flex flex-wrap items-center gap-5 rounded-chip bg-inset px-9 py-3 text-body text-ink-3">
                  <span>叙述：</span>
                  {vm.narrations.length ? vm.narrations.map((narration, index) => (
                    <span key={index}>声音 {index + 1} · {narration}</span>
                  )) : <span>未声明</span>}
                </div>
                <span className="text-tag text-ink-4">{vm.minNote}</span>
                <span className="ml-auto text-tag text-ink-4">{vm.selHint}</span>
              </div>
            </header>

            <ComparePanes
              className="px-30 pt-14 pb-8"
              left={
                <Panel>
                  <PaneHeader title="原文" meta={vm.leftMeta} sticky />
                  <div ref={leftPane} className="flex flex-col gap-2 px-20 pt-14 pb-24">
                    {vm.segments.map((s) => (
                      <Segment
                        key={s.key}
                        s={s}
                        onClick={() => s.beatId && vm.toggleBeat(s.beatId)}
                      />
                    ))}
                  </div>
                </Panel>
              }
              right={
                <Panel className="flex h-full min-h-0 flex-col">
                  <PaneHeader
                    title="抽出来的骨架"
                    meta={vm.rightMeta}
                    right={vm.selBeat ? <LinkButton onClick={vm.clearSel}>取消选中</LinkButton> : null}
                  />
                  <PaneScroll className="flex flex-col gap-9 px-16 pt-12 pb-18">
                    {vm.beats.map((b) => (
                      <Beat key={b.id} b={b} onClick={() => vm.toggleBeat(b.id)} />
                    ))}
                    <div
                      className="prose-cn rounded-btn-lg border border-dashed px-12 py-10 text-sm2 text-ink-4"
                      style={{
                        borderColor: vm.problemsEmpty ? SEV.unver.border : undefined,
                        background: vm.problemsEmpty ? SEV.unver.wash : undefined,
                      }}
                    >
                      {vm.problems}
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

function narrativeRelationText(row: BlueprintReviewNarrativeRelation): string {
  const level = row.enforcement === 'hard' ? 'HARD' : '待复核'
  switch (row.kind) {
    case 'story_before':
      return `${level} · 故事先后：${row.before_claim} → ${row.after_claim}`
    case 'response_to':
      return `${level} · 回应：${row.response_claim} ← ${row.stimulus_claim}`
    case 'same_scene':
      return `${level} · 必须同场：${row.left_claim} ↔ ${row.right_claim}`
    case 'observes':
      return `${level} · ${row.observer_char_id} 通过「${row.observation_claim}」观察到「${row.observed_claim}」`
  }
}

function BlueprintReviewWorkpackSummary({
  review,
  ordinal,
  onOpenChapter,
}: {
  review: ReturnType<typeof useReadScreen>['reviewWorkpack']
  ordinal: number
  onOpenChapter: (ordinal: number) => void
}) {
  if (review.query.isPending && review.query.fetchStatus !== 'idle') {
    return (
      <div className="rounded-block border border-line-2 bg-card px-14 py-11 text-sm2 text-ink-3">
        正在生成整本 Blueprint 的异常优先只读工作包…
      </div>
    )
  }
  if (review.query.isError) {
    return (
      <div className="rounded-block border border-line-2 bg-card px-14 py-11 text-sm2 text-ink-3">
        工作包读取失败：{errorBody(review.query.error)}
        <ErrorHint error={review.query.error} />
      </div>
    )
  }
  const pack = review.data
  if (!pack) {
    return (
      <div className="rounded-block border border-line-2 bg-card px-14 py-11 text-sm2 text-ink-4">
        Blueprint 生成后，这里会先列异常章，再保留全章覆盖供抽查；当前没有可读工作包。
      </div>
    )
  }

  const current = review.current
  return (
    <div className="flex flex-col gap-9 rounded-block border border-line-2 bg-card px-14 py-12">
      <div className="flex flex-wrap items-center gap-8">
        <span className="text-body-lg font-semibold text-ink-2">异常优先审阅工作包</span>
        <span className="rounded-chip bg-inset px-8 py-2 text-tag text-ink-3">
          异常章 {review.anomalyChapters.length} / {pack.chapters.length}
        </span>
        <span className="rounded-chip bg-inset px-8 py-2 text-tag text-ink-3">
          非正常判据 {review.attentionCount} · 扫描 {pack.check.rules_scanned}
        </span>
        <Mono className="ml-auto text-micro text-ink-5">
          Blueprint {pack.subject_sha256.slice(0, 12)}…
        </Mono>
      </div>
      <div className="text-sm2 text-ink-3">
        先看服务端标出的异常章；beat/fact/hinge 结构 ID、字符坐标与引用闭合等机械字段仍由完整 CheckReport 守卫，
        这里只隐藏，不从产物或评测面删除。语义是否忠于原文仍需人判断。
      </div>
      {pack.review_delta ? (
        <details className="rounded-block border border-line-2 px-10 py-8 text-sm2 text-ink-3">
          <summary>本次复核范围：{pack.review_delta.mode}</summary>
          <p>{pack.review_delta.reason}</p>
          <p>受影响章：{pack.review_delta.affected_chapter_ordinals.join('、') || '无'}；
            可复用接受依据的章：{pack.review_delta.reusable_accepted_chapter_ordinals.join('、') || '无'}</p>
          {pack.review_delta.changed_global_items.map((change) => (
            <p key={change.item_id + change.subject_path}>{change.subject_path} · {change.change} ·
              {change.changed_fields.join('、')} · {change.affected_references.join('、')}：{change.scope_reason}</p>
          ))}
        </details>
      ) : null}
      <div className="flex flex-wrap items-center gap-7">
        {review.anomalyChapters.length ? (
          review.anomalyChapters.map((chapter) => (
            <LinkButton key={chapter.chapter_ordinal} onClick={() => onOpenChapter(chapter.chapter_ordinal)}>
              第 {chapter.chapter_ordinal} 章 · {chapter.attention_rules.join(' / ')}
            </LinkButton>
          ))
        ) : (
          <span className="text-tag text-ink-4">
            生产判据未标出章级异常；这不等于语义自动通过，仍按完整章节列表抽查。
          </span>
        )}
      </div>
      {current ? (
        <div className="grid grid-cols-3 gap-8 rounded-btn bg-sunken-2 px-12 py-9 text-sm2 text-ink-3">
          <div className="col-span-3 flex flex-wrap items-center gap-8">
            <strong className="text-ink-2">第 {ordinal} 章功能：{current.chapter_function}</strong>
            {current.anomalous ? (
              <span style={{ color: SEV.info.color }}>
                需注意：{current.attention_rules.join(' / ')}
              </span>
            ) : null}
          </div>
          <div>章首状态：{current.entry_state_claims.join('；') || '无声明'}</div>
          <div>章尾落地：{current.ending_state_claims.join('；') || '无声明'}</div>
          <div>
            因果链：{current.causal_links.length
              ? current.causal_links.map((row) => row.before_claim + ' → ' + row.after_claim).join('；')
              : '无声明'}
          </div>
          <div className="col-span-3">
            叙事关系：{current.narrative_relations.length
              ? current.narrative_relations.map(narrativeRelationText).join('；')
              : '无声明'}
          </div>
        </div>
      ) : (
        <div className="text-tag text-ink-4">当前章不在工作包的完整章集合中，请刷新后重试。</div>
      )}
      <div className="text-micro text-ink-5">
        工作包明确隐藏 {pack.mechanical_fields_hidden.length} 类机械字段；绑定
        {' '}{pack.binding_sha256.slice(0, 12)}…，只读且不会自动生成 PASS/FAIL。
      </div>
    </div>
  )
}

function BlueprintReviewConfirmation({
  review,
  ordinal,
}: {
  review: ReturnType<typeof useReadScreen>['blueprintReview']
  ordinal: number
}) {
  let disabledReason: string | undefined
  if (review.pending) disabledReason = '正在保存整本 Blueprint 审阅确认'
  else if (!review.blueprintExists) disabledReason = '当前还没有可审阅的 Blueprint'
  else if (review.workpackLoading) disabledReason = '正在读取整本 Blueprint 异常优先工作包'
  else if (review.workpackError) disabledReason = '工作包读取失败，不能确认整本审阅'
  else if (!review.workpackReady) disabledReason = '尚未取得绑定当前正式 Blueprint 的完整工作包'
  else if (review.approvalLoading) disabledReason = '正在读取整本 Blueprint 的审阅状态'
  else if (review.approvalError) disabledReason = '审阅状态读取失败，需先恢复读取'
  else if (review.seen) disabledReason = '本书 Blueprint 已确认整本审阅'

  let status = '尚未记录整本审阅确认。'
  if (review.workpackLoading) status = '正在读取整本 Blueprint 工作包…'
  else if (review.workpackError) status = '工作包读取失败：' + errorBody(review.workpackError)
  else if (!review.blueprintExists) status = '当前还没有可审阅的 Blueprint，不能确认。'
  else if (!review.workpackReady) status = '尚未取得绑定当前正式 Blueprint 的完整工作包。'
  else if (review.approvalLoading) status = '正在读取整本审阅状态…'
  else if (review.approvalError) status = '审阅状态读取失败：' + errorBody(review.approvalError)
  else if (review.pending) status = '正在保存整本审阅确认…'
  else if (review.submitError) status = '确认失败：' + errorBody(review.submitError)
  else if (review.seen) status = '已记录：本书 Blueprint 已整本审阅。'

  return (
    <div
      role="group"
      aria-label="整本 Blueprint 审阅确认"
      className="flex flex-wrap items-center gap-14 rounded-block border border-line-2 bg-card px-14 py-11"
    >
      <div className="flex min-w-0 flex-1 flex-col gap-3">
        <span className="text-body-lg font-semibold text-ink-2">整本 Blueprint 审阅确认</span>
        <span className="text-sm2 text-ink-3">
          这会确认你已按上方工作包审阅本书全部章节的 Blueprint，不是只确认当前第 {ordinal} 章。
        </span>
        <span aria-live="polite" className="text-tag text-ink-4">
          {status}
          {review.workpackError ? <ErrorHint error={review.workpackError} /> : null}
          {review.approvalError ? <ErrorHint error={review.approvalError} /> : null}
          {review.submitError ? <ErrorHint error={review.submitError} /> : null}
        </span>
      </div>
      <ActionButton
        action={{ kind: 'approval.seen.read', extra: null, paid: false, writes_disk: true }}
        disabled={!review.canConfirm}
        disabledReason={disabledReason}
        onClick={review.confirm}
      >
        {review.pending
          ? '正在确认整本 Blueprint…'
          : review.seen
            ? '已确认审阅本书 Blueprint'
            : '确认已审阅本书 Blueprint'}
      </ActionButton>
    </div>
  )
}

function Segment({ s, onClick }: { s: SegView; onClick: () => void }) {
  if (s.kind === 'hole') {
    // 没被判为「漏」的空隙不渲染正文：它是间隙，不是内容
    return (
      <div
        className={cx(
          'border-l-3',
          s.flagged ? 'rounded-btn px-12 py-8' : 'border-l-transparent px-12 py-1',
        )}
        style={
          s.flagged ? { borderLeftColor: FRESH.stale.color, background: FRESH.stale.wash } : undefined
        }
      >
        <Mono className="block py-2 text-micro">
          <span className={s.flagged ? '' : 'text-ink-7'} style={s.flagged ? { color: FRESH.stale.color } : undefined}>
            {s.label}
          </span>
        </Mono>
        {s.paras.map((p, i) => (
          <p key={i} className="prose-cn m-0 text-section text-ink-4">
            {p}
          </p>
        ))}
      </div>
    )
  }
  return (
    <button
      type="button"
      data-beat={s.beatId ?? undefined}
      onClick={onClick}
      className={cx(
        'block w-full cursor-pointer rounded-btn-lg border-l-3 px-14 py-8 text-left',
        s.selected ? 'border-l-accent bg-accent-bg' : 'border-l-line bg-transparent',
      )}
    >
      <Mono className={cx('block pt-2 pb-4 text-micro', s.selected ? 'text-accent-ink' : 'text-ink-5')}>
        {s.label}
      </Mono>
      <div className="prose-cn flex max-w-[var(--measure-read)] flex-col gap-8 text-read-lg text-ink">
        {s.paras.map((p, i) => (
          <p key={i} className="m-0">
            {p}
          </p>
        ))}
      </div>
    </button>
  )
}

function Beat({ b, onClick }: { b: BeatView; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cx(
        'flex w-full cursor-pointer flex-col gap-8 rounded-block border px-14 py-12 text-left',
        b.selected
          ? 'border-accent-line-2 bg-accent-bg-2 shadow-ring-accent'
          : 'border-line-2 bg-card',
      )}
    >
      <div className="flex flex-wrap items-center gap-8">
        <Mono className="text-mini text-ink-4">第 {b.order} 拍</Mono>
        <span
          className={cx(
            'rounded-tag px-7 py-1 text-mini',
            b.layerPresent ? 'bg-inset text-ink-3' : 'bg-recall-bg text-recall',
          )}
        >
          {b.layer}
        </span>
        <span className="text-tag text-ink-3">{b.place}</span>
        {/* 定位失败是阻断级的事实，不能和「已定位」共用一种视觉 */}
        <span
          className="rounded-tag px-7 py-1 text-mini"
          style={
            b.located
              ? { color: FRESH.fresh.color, background: FRESH.fresh.bg }
              : { color: SEV.hard.color, background: SEV.hard.bg }
          }
        >
          {b.located ? '已定位' : '定位失败'}
        </span>
        <Mono className="ml-auto text-micro text-ink-5">{b.span}</Mono>
      </div>

      <div className="flex flex-wrap gap-6">
        {b.presentCharIds.map((charId) => (
          <span
            key={charId}
            className="rounded-[11px] border border-line-2 bg-inset px-8 py-2 text-tag text-ink-2"
          >
            {charId}
          </span>
        ))}
      </div>

      {b.facts.map((fact) => (
        <div key={fact.id} className="rounded-btn bg-sunken-2 px-12 py-8">
          <div className="flex gap-8 text-micro text-ink-4">
            <span>{fact.id} · {fact.meta}</span>
          </div>
          <div className="prose-cn text-section text-ink-2">{fact.claim}</div>
          <div className="text-tag text-ink-4">
            参与人物：{fact.participantCharIds.join('、') || '（无）'}
          </div>
          {fact.evidence ? <div className="prose-cn text-tag text-ink-4">证据：{fact.evidence}</div> : null}
        </div>
      ))}

      {b.hinges.map((h, i) => (
        <div key={i} className="rounded-r-btn border-l-3 border-l-hinge bg-hinge-bg px-12 py-8">
          <div className="text-micro text-hook-ink">必须保真的点 · {h.kind}</div>
          <div className="prose-cn text-sm2 text-ink-2">{h.text}</div>
        </div>
      ))}

      <Mono className="text-mini text-ink-5">定位状态：{b.span}</Mono>
    </button>
  )
}
