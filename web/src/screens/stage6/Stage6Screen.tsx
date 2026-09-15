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
import type { CompactApprovalCheck } from '@/types/approvals'
import { cx } from '@/lib/cx'
import { PreparePanel } from './PreparePanel'

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
  const workpackQ = useQuery({
    queryKey: qk.finalApprovalWorkpack(sid, 'stage6'),
    queryFn: () => api.getStage6ApprovalWorkpack(sid),
    retry: false,
  })
  const workpackSettled =
    !workpackQ.isLoading && !workpackQ.isFetching && !workpackQ.isError
  const workpackReady = Boolean(
    workpackSettled && workpackQ.data?.disposition === 'review_required',
  )
  const workpackCurrent = Boolean(
    workpackSettled && workpackQ.data?.disposition === 'already_current',
  )
  const approve = useMutation({
    mutationFn: (req: {
      pick: number
      actor: string
      reason: string
      review_token: string
    }) => api.approveHook(sid, req),
    onSuccess: (job) => {
      if (job.state !== 'failed') {
        setReopened(false)
        notify('开篇文案已定稿')
      }
      void qc.invalidateQueries({ queryKey: qk.stage6(sid) })
      void qc.invalidateQueries({ queryKey: qk.check(sid, 'stage6') })
      void qc.invalidateQueries({ queryKey: qk.approvals(sid) })
      void qc.invalidateQueries({ queryKey: qk.finalApprovalWorkpack(sid, 'stage6') })
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },
  })

  const askApprove = (candidate: {
    index: number
    subject_sha256: string
  }) => {
    const workpack = workpackQ.data
    if (!workpackReady || !workpack) return
    askConfirm({
      req: {
        kind: 'stage6.approve',
        label: '文案定稿 · 候选 ' + candidate.index,
        extra: [
          '--pick', String(candidate.index),
          '--actor', actor.trim(),
          '--reason', reason.trim(),
          '--review-token', workpack.review_token,
        ],
        sid,
        ordinal: null,
      },
      run: () => approve.mutateAsync({
        pick: candidate.index,
        actor: actor.trim(),
        reason: reason.trim(),
        review_token: workpack.review_token,
      }),
    })
  }

  return (
    <ScreenFrame screen="stage6">
      <PreparePanel key={sid} sid={sid} />
      <QueryStates q={q} loadingLabel="读开篇文案候选" missing={{ title: '开篇文案 · stage6' }}>
        {(d) => {
          const approvedIdx = d.candidates.find((c) => c.approved)?.index ?? null
          const approvalCandidates = workpackQ.data?.candidates ?? []
          // 判官退役（2026-09-15）：不再有质量排序读数。UI 默认落点与服务端
          // --auto 同规则——固定槽位序第一条（无 HARD 优先由服务端判定，这里只取次序）。
          const picked = pick ?? approvedIdx ?? approvalCandidates[0]?.index ?? 1
          const cand = approvalCandidates.find((c) => c.index === picked) ?? approvalCandidates[0]
          const currentCandidate = approvalCandidates.find(
            (c) => c.slot === workpackQ.data?.current_slot,
          )
          const approvalCurrent = Boolean(
            workpackCurrent && workpackQ.data?.current_approval && currentCandidate,
          )
          const finalPhase = approvalCurrent && !reopened
          const body = approvalCurrent ? currentCandidate?.text ?? '' : cand?.text ?? ''
          const origText = d.source_hook_text
          const approvedLabel = approvalCurrent && currentCandidate
            ? `候选 ${currentCandidate.index} · ${currentCandidate.approach}`
              + (workpackQ.data?.current_approval?.basis === 'default_policy'
                ? '（自动定稿）' : '（人工选择）')
            : null

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
                      approvalCurrent
                        ? { color: FRESH.fresh.color, background: FRESH.fresh.bg }
                        : { color: SEV.unver.color, background: SEV.unver.bg }
                    }
                  >
                    {approvedLabel
                      ? `已定稿：${approvedLabel}`
                      : d.approved
                        ? '旧定稿未绑定当前工作包 · 需重审'
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
              {workpackQ.isError ? (
                <div className="rounded-card border border-warn-line bg-warn-bg px-18 py-12 text-body text-warn-ink">
                  终审工作包读取失败：{errorBody(workpackQ.error)}
                  <ErrorHint error={workpackQ.error} />
                </div>
              ) : null}

              <div className="prose-cn text-tag text-ink-4">
                A/B 审判只看开篇前 {d.lead_upto} 章；最终定稿工作包提供完整重写正文。{d.rationale}
              </div>
              {workpackQ.data?.check_basis === 'candidate_without_approval' ? (
                <p className="prose-cn text-tag text-ink-3">
                  机械核验以未附审批的候选为基准，其中“未定稿”提示不表示当前审批状态；是否已定稿以上方审批状态为准。
                </p>
              ) : null}

              {!finalPhase ? (
                <>
                  <div className="text-section font-semibold">
                    三个候选并排看，选一个定稿（{d.candidates.length} 条）
                  </div>
                  {workpackQ.data ? (
                    <div className="prose-cn rounded-block border border-line-row bg-sunken-2 px-14 py-11 text-tag text-ink-3">
                      <div className="font-semibold">
                        hash-bound 终审包 · {workpackQ.data.disposition}
                      </div>
                      <Mono className="break-all text-micro">
                        workpack {workpackQ.data.workpack_sha256}
                      </Mono>
                      <Mono className="break-all text-micro">
                        artifact {workpackQ.data.artifact_subject_sha256}
                      </Mono>
                      <CompactCheckEvidence check={workpackQ.data.check} />
                      <details className="mt-10">
                        <summary className="cursor-pointer font-semibold">
                          核验材料 · {workpackQ.data.materials.rewritten_chapters.length} 章正文
                          {' · '}{workpackQ.data.materials.source_blurb.facts.length} 条钩子主张
                        </summary>
                        <p>钩子独立支持的主张无需在正文重复；正文有反证时仍须处理。</p>
                        <div className="whitespace-pre-wrap">{workpackQ.data.materials.source_hook}</div>
                        {workpackQ.data.materials.source_blurb.facts.map((fact) => (
                          <div key={fact.claim_id} className="mt-10">
                            <strong>{fact.claim_id} · {fact.kind === 'hook_supported'
                              ? '钩子独立支持' : fact.kind === 'supported' ? '正文支持' : '待裁决'}</strong>
                            <div>{fact.claim}</div>
                            <div>{fact.kind === 'hook_supported'
                              ? fact.hook_evidence.source?.text ?? fact.hook_evidence.unresolved_locator
                              : fact.kind === 'supported' ? fact.evidence.source?.text : fact.reason}</div>
                          </div>
                        ))}
                        {workpackQ.data.materials.rewritten_chapters.map((chapter) => (
                          <details key={chapter.chapter_ordinal} className="mt-10">
                            <summary className="cursor-pointer">第 {chapter.chapter_ordinal} 章正文</summary>
                            <div className="whitespace-pre-wrap">{chapter.text}</div>
                          </details>
                        ))}
                      </details>
                    </div>
                  ) : null}
                  <ActorReasonFields
                    actor={actor}
                    reason={reason}
                    onActor={setActor}
                    onReason={setReason}
                    note="定稿会同时绑定当前候选的 SHA-256。服务端不替人否决任何一条候选：矛盾线索只呈现不阻断，核验的责任在你这一侧。"
                  />
                  <div
                    className="grid gap-14"
                    style={{ gridTemplateColumns: 'repeat(3, minmax(0,1fr))' }}
                  >
                    {approvalCandidates.map((c) => {
                      const on = picked === c.index
                      const mechanicallyBlocked = c.findings.some((f) => f.sev === 'hard')
                      const approvable =
                        !mechanicallyBlocked && !!actor.trim() && !!reason.trim() &&
                        workpackReady
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
                            {/* A/B 判官随 stage6.semantic 退役（2026-09-15 全自动裁决）：
                                本层只呈现机械判据；选择记录见卡头 origin 标签。 */}
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
                                : workpackCurrent
                                  ? '当前工作包已批准 · 跳过'
                                  : workpackQ.isError
                                    ? '终审工作包读取失败'
                                  : workpackQ.isFetching
                                    ? '终审工作包刷新中'
                                  : !workpackQ.data
                                    ? '终审工作包读取中'
                                    : !actor.trim() || !reason.trim()
                                      ? '先填身份与理由'
                                      : on
                                        ? '人工核验后定稿'
                                        : '定这一条'}
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  <div className="prose-cn text-tag text-ink-4">
                    排序来自匿名主编的六维质量审判；剧透不扣分。⚠
                    语义结论恒是带证据的 UNVER——服务端不替人否决任何一条候选，矛盾线索只呈现不阻断，
                    定稿前请读两名判官各自的理由与逐字证据。改字不在浏览器：把 hook_edit.json
                    放进书目录 stage6/ 后跑 stage6.hook --apply-edit（0 成本）。
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
                          title="当前不可用：把 hook_edit.json 放进书目录 stage6/ 后跑 stage6.hook --apply-edit（0 成本）"
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

function CompactCheckEvidence({ check }: { check: CompactApprovalCheck }) {
  const rows = [...check.attention, ...check.metrics]
  return (
    <div role="region" aria-label="文案机械核验材料" className="flex flex-col gap-5 border-t border-line-soft pt-7">
      <span>
        机械规则 {check.rules_scanned} 条：ok {check.counts.ok} · hit {check.counts.hit} ·
        {' '}unver {check.counts.unver} · skipped {check.counts.skipped}；来源{' '}
        {check.provenance.freshness}/{check.provenance.reason}
      </span>
      {rows.map((row) => (
        <div key={row.rule} className="rounded-btn border border-line-row bg-card px-9 py-7">
          <div className="flex flex-wrap gap-6">
            <Mono>{row.rule}</Mono>
            <span>{row.sev}/{row.status}</span>
            {row.scanned ? <span>扫描：{row.scanned}</span> : null}
          </div>
          {row.why ? <div className="prose-cn">{row.why}</div> : null}
          {row.findings.map((finding, index) => (
            // 不同候选可以共享相同证据键（如引号计数 0/0），每条发现仍须独立显示。
            <div key={`${row.rule}:${index}:${finding.evidence_key ?? ''}`} className="prose-cn text-ink-2">
              {finding.detail}
            </div>
          ))}
        </div>
      ))}
    </div>
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
