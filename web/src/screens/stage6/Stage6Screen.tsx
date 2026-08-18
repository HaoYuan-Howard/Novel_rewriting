import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { errorBody } from '@/api/errors'
import { ErrorHint } from '@/components/state/States'
import { FRESH, SEV } from '@/constants/vocabulary'
import { useChrome } from '@/hooks/useChrome'
import { useSid } from '@/hooks/useRouteParams'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { QueryStates } from '@/components/state/QueryStates'
import { LinkButton } from '@/components/ui/ActionButton'
import { ActorReasonFields } from '@/components/ui/ActorReasonFields'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { Prose } from '@/components/ui/Prose'
import { SevChip, SevMark } from '@/components/vocab/Sev'
import type { Finding } from '@/types/common'
import { cx } from '@/lib/cx'

/**
 * 开篇文案。
 * 先从三候选里定稿一条；定稿后左 = 校验、中 = 原作文案、右 = 重构文案（可编辑）。
 * 三候选并排本身就是需求 —— 要的就是横着比。
 */
export function Stage6Screen() {
  const sid = useSid()
  const qc = useQueryClient()
  const { askConfirm, notify } = useChrome()
  const [pick, setPick] = useState<number | null>(null)
  const [reopened, setReopened] = useState(false)
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')

  const q = useQuery({ queryKey: qk.stage6(sid), queryFn: () => api.getStage6(sid), retry: false })
  const s1hook = useQuery({
    queryKey: qk.s1hook(sid),
    queryFn: () => api.getStage1HookCopy(sid),
    retry: false,
  })

  const approve = useMutation({
    mutationFn: (req: {
      pick: number
      actor: string
      reason: string
      subject_sha256: string
    }) => api.approveHook(sid, req),
    onSuccess: (job) => {
      if (job.state !== 'failed') {
        setReopened(false)
        notify('开篇文案已定稿')
      }
      void qc.invalidateQueries({ queryKey: qk.stage6(sid) })
      void qc.invalidateQueries({ queryKey: qk.check(sid, 'stage6') })
      void qc.invalidateQueries({ queryKey: qk.approvals(sid) })
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },
  })

  const askApprove = (candidate: {
    index: number
    subject_sha256: string
  }) =>
    askConfirm({
      req: {
        kind: 'stage6.approve',
        label: '文案定稿 · 候选 ' + candidate.index,
        extra: [
          '--pick', String(candidate.index),
          '--actor', actor.trim(),
          '--reason', reason.trim(),
          '--subject-sha256', candidate.subject_sha256,
        ],
        sid,
        ordinal: null,
      },
      run: () => approve.mutateAsync({
        pick: candidate.index,
        actor: actor.trim(),
        reason: reason.trim(),
        subject_sha256: candidate.subject_sha256,
      }),
    })

  return (
    <ScreenFrame screen="stage6">
      <QueryStates q={q} loadingLabel="读开篇文案候选" missing={{ title: '开篇文案 · stage6' }}>
        {(d) => {
          const approvedIdx = d.candidates.find((c) => c.approved)?.index ?? null
          const recommended = [...d.candidates]
            .filter((c) => c.selectable)
            .sort((a, b) => (a.quality_rank ?? 999) - (b.quality_rank ?? 999))[0]
          const picked = pick ?? approvedIdx ?? recommended?.index ?? d.candidates[0]?.index ?? 1
          const cand = d.candidates.find((c) => c.index === picked) ?? d.candidates[0]
          const finalPhase = !!d.approved && !reopened
          const body = d.approved?.text ?? cand?.text ?? ''
          const origText = s1hook.data?.hook_copy.text ?? ''

          return (
            <section className="flex flex-col gap-16 px-30 py-24">
              <header className="flex items-end justify-between gap-24">
                <div className="flex flex-col gap-5">
                  <h1 className="m-0 text-screen font-semibold">开篇文案 · stage6</h1>
                  <div className="prose-cn max-w-[52em] text-body text-ink-3">{d.rationale}</div>
                </div>
                <div className="flex items-center gap-10">
                  <span
                    className="rounded-btn px-11 py-4 text-body font-semibold"
                    style={
                      d.approved
                        ? { color: FRESH.fresh.color, background: FRESH.fresh.bg }
                        : { color: SEV.unver.color, background: SEV.unver.bg }
                    }
                  >
                    {d.approved
                      ? '已定稿：' +
                        d.approved.approach +
                        '（' +
                        (d.approved.resolution.basis === 'human_verification'
                          ? '人工核验'
                          : '校准双盲 PASS 后人选') +
                        '）'
                      : '还没定稿'}
                  </span>
                  {finalPhase ? (
                    <LinkButton onClick={() => setReopened(true)}>回去重挑候选</LinkButton>
                  ) : null}
                </div>
              </header>

              {approve.isError ? (
                <div className="rounded-card border border-warn-line bg-warn-bg px-18 py-12 text-body text-warn-ink">
                  定稿失败：{errorBody(approve.error)}
                  <ErrorHint error={approve.error} />
                </div>
              ) : null}

              <div className="prose-cn text-tag text-ink-4">
                判据只看下发范围（lead_upto 前 {d.lead_upto} 章）；{d.rationale}
              </div>

              {!finalPhase ? (
                <>
                  <div className="text-section font-semibold">
                    三个候选并排看，选一个定稿（{d.candidates.length} 条）
                  </div>
                  <ActorReasonFields
                    actor={actor}
                    reason={reason}
                    onActor={setActor}
                    onReason={setReason}
                    note="定稿会同时绑定当前候选的 SHA-256。缺 profile 或双盲 UNVER 可由这条人工 resolution 消解；已校准的 FAIL/实质矛盾不可绕过。"
                  />
                  <div
                    className="grid gap-14"
                    style={{ gridTemplateColumns: 'repeat(3, minmax(0,1fr))' }}
                  >
                    {d.candidates.map((c) => {
                      const on = picked === c.index
                      const mechanicallyBlocked = c.findings.some((f) => f.sev === 'hard')
                      const approvable =
                        !mechanicallyBlocked &&
                        (c.selectable || c.human_resolvable) &&
                        !!actor.trim() &&
                        !!reason.trim()
                      return (
                        <div
                          key={c.index}
                          className={cx(
                            'flex flex-col overflow-hidden rounded-card bg-card',
                            on ? 'border-2 border-ink' : 'border border-line',
                          )}
                        >
                          <div className="flex flex-wrap items-center gap-8 border-b border-line-soft bg-sunken-2 px-16 py-11">
                            <Mono className="rounded-tag bg-inset px-7 py-1 text-mini">
                              候选 {c.index}
                            </Mono>
                            <span className="text-body font-semibold">{c.approach}</span>
                            <Mono className="text-micro text-ink-5">{c.chars} 字</Mono>
                            {c.quality_rank != null ? (
                              <Mono className="rounded-tag bg-inset px-7 py-1 text-mini">
                                质量 #{c.quality_rank} · {c.quality_score?.toFixed(2)}
                              </Mono>
                            ) : null}
                            <span
                              className={cx(
                                'ml-auto rounded-tag px-8 py-2 text-tag',
                                !c.findings.some((f) => f.sev === 'hard') && 'bg-inset text-ink-3',
                              )}
                              style={
                                c.findings.some((f) => f.sev === 'hard')
                                  ? { color: SEV.hard.color, background: SEV.hard.bg }
                                  : undefined
                              }
                            >
                              hard {c.findings.filter((f) => f.sev === 'hard').length} · info{' '}
                              {c.findings.filter((f) => f.sev === 'info').length} · 共{' '}
                              {c.findings.length} 条
                            </span>
                          </div>

                          <div className="flex flex-col gap-8 px-16 py-14">
                            {/* 高亮由 findings.evidence 在文案里 indexOf —— 与验收屏同一做法；
                                文案区统一限高内滚，三卡等高、比较视线不跳 */}
                            <p className="prose-cn m-0 max-h-[240px] max-w-[var(--measure-compare)] overflow-y-auto whitespace-pre-wrap text-read leading-[2.05]">
                              {c.text}
                            </p>
                            <div className="prose-cn rounded-block bg-sunken-2 px-10 py-8 text-tag text-ink-3">
                              质量审判：{c.semantic_state} · {c.semantic_reason}
                            </div>
                            {c.contradictions.map((item, i) => (
                              <div
                                key={'semantic-' + i}
                                className="prose-cn rounded-block px-10 py-8 text-tag"
                                style={
                                  c.semantic_state === 'contradiction'
                                    ? { color: SEV.hard.color, background: SEV.hard.bg }
                                    : { color: SEV.unver.color, background: SEV.unver.bg }
                                }
                              >
                                <div className="font-semibold">
                                  {c.semantic_state === 'contradiction'
                                    ? '校准双盲 FAIL（实质矛盾）'
                                    : '未校准线索（UNVER）'}
                                  ：{item.assertion}
                                </div>
                                <div>证据：{item.evidence}</div>
                                <div>理由：{item.reason}</div>
                              </div>
                            ))}
                          </div>

                          <div className="flex flex-col border-t border-line-soft">
                            {c.findings.map((f, i) => (
                              <FindingLine key={i} f={f} />
                            ))}
                          </div>

                          <div className="mt-auto flex items-center gap-8 border-t border-line-soft bg-sunken-2 px-16 py-12">
                            <Mono className="text-micro text-ink-5">
                              标点 {c.mech.punct_fixed} · 引号 {c.mech.quotes_fixed} · 段落{' '}
                              {c.mech.paras_fixed}
                            </Mono>
                            <button
                              type="button"
                              disabled={!approvable}
                              onClick={() => {
                                if (!approvable) return
                                if (on) askApprove(c)
                                else setPick(c.index)
                              }}
                              className={cx(
                                'ml-auto cursor-pointer rounded-btn-lg border px-14 py-9 text-body-lg font-semibold',
                                !approvable && 'cursor-not-allowed opacity-50',
                                on ? 'border-ink bg-ink text-card' : 'border-line-btn bg-card text-ink',
                              )}
                            >
                              {mechanicallyBlocked
                                ? '有机械 HARD · 不可定稿'
                                : c.semantic_state === 'contradiction'
                                  ? '有实质矛盾 · 不可定稿'
                                  : !actor.trim() || !reason.trim()
                                    ? '先填身份与理由'
                                    : !c.selectable && !c.human_resolvable
                                      ? '当前结论不可消解'
                                      : on
                                        ? c.selectable
                                          ? '定稿这一条 · 进入审阅'
                                          : '人工核验后定稿'
                                        : '定这一条'}
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  <div className="prose-cn text-tag text-ink-4">
                    排序来自匿名主编的六维质量审判；剧透不扣分，实质矛盾候选不可定稿。人仍负责最终审美选择。改字不在浏览器：把
                    hook_edit.json 放进书目录 stage6/ 后跑 stage6.hook --apply-edit（0 成本）。
                  </div>
                </>
              ) : (
                <div
                  className="grid items-start gap-14"
                  style={{ gridTemplateColumns: '300px minmax(0,1fr) minmax(0,1.15fr)' }}
                >
                  <Panel className="flex flex-col">
                    <PaneHeader
                      title="定稿那一份的判定"
                      meta="判据全表见本屏底部的 stage6 校验"
                    />
                    {(d.approved?.findings ?? []).map((f, i) => (
                      <FindingLine key={i} f={f} />
                    ))}
                  </Panel>

                  <Panel className="flex flex-col">
                    <PaneHeader
                      title="原作文案"
                      meta={
                        origText
                          ? [...origText].length + ' 字'
                          : '原作文案不在样本里——这一栏是空的，不是原书没有文案'
                      }
                    />
                    <div className="px-18 py-14">
                      {origText ? (
                        <Prose text={origText} measure="compare" size="md" tone="ink-2" />
                      ) : (
                        <div className="hatch rounded-block border border-dashed border-line-dash p-16 text-sm2 text-ink-4">
                          原作文案不在样本里——这一栏是空的，不是原书没有文案。
                        </div>
                      )}
                    </div>
                  </Panel>

                  <Panel className="flex flex-col">
                    <PaneHeader
                      title="重构文案"
                      meta={[...body].length + ' 字'}
                      right={
                        <LinkButton
                          disabled
                          title="当前不可用：把 hook_edit.json 放进书目录 stage6/ 后跑 stage6.hook --apply-edit（0 成本）；语义结论按文本 sha1 自动过期，重跑 stage6.semantic 可清"
                        >
                          改字（走 CLI）
                        </LinkButton>
                      }
                    />
                    <div className="px-18 py-14">
                      <Prose text={body} measure="compare" size="md" />
                    </div>
                    <div className="prose-cn border-t border-line-soft px-18 py-9 text-tag text-ink-4">
                      浏览器只做定稿（人选那一下是唯一闸门）。改字是另一个 0 成本节点
                      （stage6.hook --apply-edit），编辑文件的形状与位置是契约——放进书目录
                      stage6/ 之后跑 CLI。
                    </div>
                  </Panel>
                </div>
              )}
            </section>
          )
        }}
      </QueryStates>
    </ScreenFrame>
  )
}

function FindingLine({ f }: { f: Finding }) {
  return (
    <div className="flex flex-col gap-4 border-b border-line-row px-16 py-10">
      <div className="flex flex-wrap items-center gap-7">
        <SevMark sev={f.sev} />
        <span className="text-body font-semibold">{f.item}</span>
        <SevChip sev={f.sev} />
      </div>
      <Mono className="text-micro text-ink-5">{f.rule}</Mono>
      <div className="prose-cn text-sm2 text-ink-2">{f.detail}</div>
    </div>
  )
}
