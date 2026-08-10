import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { FRESH, SEV } from '@/constants/vocabulary'
import { useSid } from '@/hooks/useRouteParams'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { QueryStates } from '@/components/state/QueryStates'
import { ActionButton, LinkButton } from '@/components/ui/ActionButton'
import { HighlightedText } from '@/components/ui/HighlightedText'
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
  const [pick, setPick] = useState<number | null>(null)
  const [reopened, setReopened] = useState(false)
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState<string | null>(null)

  const q = useQuery({ queryKey: qk.stage6(sid), queryFn: () => api.getStage6(sid), retry: false })
  const s1hook = useQuery({
    queryKey: qk.s1hook(sid),
    queryFn: () => api.getStage1HookCopy(sid),
    retry: false,
  })

  const approve = useMutation({
    mutationFn: (v: { index: number; text: string }) => api.approveHook(sid, v.index, v.text),
    onSuccess: () => {
      setReopened(false)
      setEditing(false)
      void qc.invalidateQueries({ queryKey: qk.stage6(sid) })
    },
  })

  return (
    <ScreenFrame screen="stage6">
      <QueryStates q={q} loadingLabel="读开篇文案候选" missing={{ title: '开篇文案 · stage6' }}>
        {(d) => {
          const approvedIdx = d.candidates.find((c) => c.approved)?.index ?? null
          const picked = pick ?? approvedIdx ?? d.candidates[0]?.index ?? 1
          const cand = d.candidates.find((c) => c.index === picked) ?? d.candidates[0]
          const finalPhase = !!d.approved && !reopened
          const body = text ?? d.approved?.text ?? cand?.text ?? ''
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
                        (d.approved.origin === 'human' ? '人选的' : d.approved.origin) +
                        '）'
                      : '还没定稿'}
                  </span>
                  {finalPhase ? (
                    <LinkButton onClick={() => setReopened(true)}>回去重挑候选</LinkButton>
                  ) : null}
                </div>
              </header>

              <div className="prose-cn text-tag text-ink-4">{d.scope_note}</div>

              {!finalPhase ? (
                <>
                  <div className="text-section font-semibold">
                    三个候选并排看，选一个定稿（{d.candidates.length} 条）
                  </div>
                  <div
                    className="grid items-start gap-14"
                    style={{ gridTemplateColumns: 'repeat(3, minmax(0,1fr))' }}
                  >
                    {d.candidates.map((c) => {
                      const on = picked === c.index
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
                                !c.hard && 'bg-inset text-ink-3',
                              )}
                              style={
                                c.hard
                                  ? { color: SEV.hard.color, background: SEV.hard.bg }
                                  : undefined
                              }
                            >
                              hard {c.hard} · info {c.info} · 共 {c.findings.length} 条
                            </span>
                          </div>

                          <div className="flex flex-col gap-8 px-16 py-14">
                            {/* segments 里带 rule/sev 的片段按 sev 上色 —— 服务端切好的，前端不按下标切 */}
                            <p className="prose-cn m-0 max-w-[var(--measure-compare)] text-read leading-[2.05]">
                              <HighlightedText
                                parts={c.segments.map((s) => ({
                                  text: s.text,
                                  sev: s.sev,
                                  rule: s.rule,
                                }))}
                              />
                            </p>
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
                              onClick={() => {
                                if (on) {
                                  setText(c.text)
                                  approve.mutate({ index: c.index, text: c.text })
                                } else {
                                  setPick(c.index)
                                  setText(null)
                                }
                              }}
                              className={cx(
                                'ml-auto cursor-pointer rounded-btn-lg border px-14 py-9 text-body-lg font-semibold',
                                on ? 'border-ink bg-ink text-card' : 'border-line-btn bg-card text-ink',
                              )}
                            >
                              {on ? '定稿这一条 · 进入审阅' : '定这一条'}
                            </button>
                          </div>
                        </div>
                      )
                    })}
                  </div>
                  <div className="prose-cn text-tag text-ink-4">{d.approve_note}</div>
                </>
              ) : (
                <div
                  className="grid items-start gap-14"
                  style={{ gridTemplateColumns: '300px minmax(0,1fr) minmax(0,1.15fr)' }}
                >
                  <Panel className="flex flex-col">
                    <PaneHeader title="校验" meta={d.rules.length + ' 条判据'} />
                    <div className="flex flex-wrap gap-8 border-b border-line-soft px-16 py-10">
                      {(['hard', 'info', 'metric', 'unver'] as const).map((s) => (
                        <span key={s} className="inline-flex items-center gap-5 text-mini">
                          <SevMark sev={s} />
                          <span style={{ color: SEV[s].color }}>
                            {SEV[s].label} {d.counts[s] ?? 0}
                          </span>
                        </span>
                      ))}
                    </div>
                    {(d.approved?.findings ?? []).map((f, i) => (
                      <FindingLine key={i} f={f} />
                    ))}
                    <div className="border-t border-line-soft bg-sunken-2 px-16 py-9 text-tag font-semibold text-ink-3">
                      全书级 {d.book.length} 条
                    </div>
                    {d.book.map((f, i) => (
                      <FindingLine key={'b' + i} f={f} />
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
                        <>
                          <LinkButton
                            onClick={() => {
                              setText(body)
                              setEditing((v) => !v)
                            }}
                          >
                            {editing ? '读' : '改'}
                          </LinkButton>
                          {editing ? (
                            <ActionButton
                              action={{ kind: 'n8_hook', extra: ['--approve'], paid: false }}
                              size="sm"
                              onClick={() => approve.mutate({ index: picked, text: body })}
                            >
                              保存定稿
                            </ActionButton>
                          ) : null}
                        </>
                      }
                    />
                    <div className="px-18 py-14">
                      {editing ? (
                        <textarea
                          value={body}
                          onChange={(e) => setText(e.target.value)}
                          className="min-h-[320px] w-full resize-y rounded-block border border-line bg-card px-14 py-12 text-read leading-[2.05] text-ink"
                        />
                      ) : (
                        <Prose text={body} measure="compare" size="md" />
                      )}
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
