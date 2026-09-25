import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { errorBody, isNoSample, isNotRunYet } from '@/api/errors'
import { qk } from '@/api/queryKeys'
import { lookupAction } from '@/constants/actions'
import { DELIVERY_LABEL, FRESH, SCRIM, SEV } from '@/constants/vocabulary'
import { useSampleCoverage } from '@/hooks/useSample'
import { useAskJob } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import { useSid } from '@/hooks/useRouteParams'
import { invalidateBookReadModels } from '@/hooks/bookReadModels'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { CompareRows } from '@/components/compare/CompareRows'
import { QueryStates } from '@/components/state/QueryStates'
import { ErrorHint, HatchNote, LoadFailed } from '@/components/state/States'
import { ActionButton } from '@/components/ui/ActionButton'
import { ChapterStrip } from '@/components/ui/ChapterStrip'
import { HighlightedText } from '@/components/ui/HighlightedText'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { Prose } from '@/components/ui/Prose'
import { cx } from '@/lib/cx'
import type { Stage7ApprovalWorkpack } from '@/types/approvals'

/**
 * `extra_subs[].origin` 的人读名。**判定上两者同权**（后端 `ExtraSub.origin` 那一格
 * 写着理由），这里分开只是让人看清「这条是谁加的」。
 * ⚠ 取不到就原样显示那个值 —— 前端只呈现判定，不替后端补一个它没给的结论。
 */
const ORIGIN_LABEL: Record<string, string> = {
  human: '人加的',
  adopted: '机械/批量采纳',
}

/**
 * 人名替换 + 成书。
 * 新旧人名表并排 + 逐章替换对照；定稿由 rename 机械 seal（只读）。
 * 「漏网叫法候选」是唯一能发现人名表本身漏了人的地方 —— 放在最上面。
 */
export function Stage7Screen() {
  const sid = useSid()
  const ask = useAskJob()
  const enums = useEnums()
  const renameAction = lookupAction(enums.data, { kind: 'stage7.rename', extra: null })
  const assembleAction = lookupAction(enums.data, { kind: 'stage7.assemble', extra: null })
  const nav = useNavigate()
  const qc = useQueryClient()
  const { o } = useParams<{ o: string }>()
  const ordinal = o ? Number(o) : null
  const { hasSample, defaultOrdinal } = useSampleCoverage()
  const [bookOpen, setBookOpen] = useState(false)

  const q = useQuery({ queryKey: qk.stage7(sid), queryFn: () => api.getStage7(sid), retry: false })
  const workpackQ = useQuery({
    queryKey: qk.finalApprovalWorkpack(sid, 'stage7'),
    queryFn: () => api.getStage7ApprovalWorkpack(sid),
    retry: false,
  })
  const workpackSettled =
    !workpackQ.isLoading && !workpackQ.isFetching && !workpackQ.isError
  const workpackCurrent = Boolean(
    workpackSettled && workpackQ.data?.disposition === 'already_current',
  )
  const chQ = useQuery({
    queryKey: qk.stage7ch(sid, ordinal ?? 0),
    queryFn: () => api.getStage7Chapter(sid, ordinal!),
    enabled: ordinal !== null,
    retry: false,
  })
  const deliveryReady = !q.isFetching && !q.isError &&
    (q.data?.delivery?.state === 'ready' || q.data?.delivery?.state === 'ready_with_disclosures')
  const bookQ = useQuery({
    queryKey: qk.stage7book(sid),
    queryFn: () => api.getStage7Book(sid),
    enabled: bookOpen && deliveryReady,
    retry: false,
  })
  const download = useMutation({
    mutationFn: () => api.downloadStage7Book(sid),
    onError: () => { void invalidateBookReadModels(qc, sid) },
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      // 另存名由服务端下发（`book_filename`，来自 `novel/paths.py::book_txt_rel`）。
      // ⚠ 不在这里拿 title 再拼一次：那是同一条命名规则的第二个落点（`R-11.4`）。
      // 拿不到就**不设**，让浏览器用默认名——编一个看起来对的名字更坏。
      const filename = q.data?.book_filename
      if (filename) anchor.download = filename
      anchor.click()
      URL.revokeObjectURL(url)
    },
  })

  return (
    <ScreenFrame screen="stage7">
      {/*
        ⚠⚠ **这一层的产出入口必须在这一屏上，否则浏览器里走不到成书。**
        定稿已由 rename 机械 seal；本屏负责发起 rename / assemble。
      */}
      {isNotRunYet(q.error) ? (
        <Panel className="mx-30 mt-20 flex flex-wrap items-center gap-12 px-18 py-14">
          <div className="flex min-w-0 flex-col gap-3">
            <span className="text-body font-semibold">还没跑人名替换</span>
            <span className="text-tag text-ink-4">
              这一层要先产出 stage7/name_map.json；机械通过后自动定稿再拼装成书。
            </span>
          </div>
          <ActionButton
            className="ml-auto"
            action={renameAction}
            onClick={() =>
              ask({
                kind: 'stage7.rename',
                label: '执行人物改名',
                extra: null,
                required_inputs: renameAction?.required_inputs,
                sid,
                ordinal: null,
              })
            }
          >
            跑人名替换
          </ActionButton>
        </Panel>
      ) : null}
      <QueryStates q={q} loadingLabel="读人名表" missing={{ title: '人名替换 + 成书 · stage7' }}>
        {(d) => {
          const rows = chQ.data?.rows ?? null
          const changedRows = rows?.filter((r) => r.changed).length ?? 0
          const approvalCurrent = workpackCurrent
          const currentApproval = approvalCurrent ? workpackQ.data?.current_approval : null

          return (
            <section className="flex flex-col gap-16 px-30 py-24">
              <header className="flex items-end justify-between gap-24">
                <div className="flex flex-col gap-5">
                  <h1 className="m-0 text-screen font-semibold">人名替换 + 成书 · stage7</h1>
                  <div className="text-body text-ink-3">
                    新旧人名表并排看；机械校验通过后由系统自动定稿，再拼成书。
                  </div>
                </div>
                <div className="flex items-center gap-10">
                  <span
                    className="rounded-chip px-10 py-3 text-body font-semibold"
                    style={
                      approvalCurrent
                        ? { color: FRESH.fresh.color, background: FRESH.fresh.bg }
                        : { color: SEV.unver.color, background: SEV.unver.bg }
                    }
                  >
                    {approvalCurrent
                      ? '人物表已自动定稿'
                      : d.approved
                        ? '旧定稿未绑定当前工作包 · 需 --recheck'
                        : '人物表还没定稿'}
                  </span>
                </div>
              </header>

              {workpackQ.isError ? (
                <div className="rounded-card border border-warn-line bg-warn-bg px-18 py-12 text-body text-warn-ink">
                  终审工作包读取失败：{errorBody(workpackQ.error)}
                  <ErrorHint error={workpackQ.error} />
                </div>
              ) : null}

              <Panel className="px-18 py-14">
                <div className="prose-cn text-body text-ink-3">
                  定稿由 rename producer 在机械校验通过后自动写入（system:stage7-auto）。
                  本页只读；安全 high 残留由流水线自动采纳，不提供人工写按钮。
                </div>
                {currentApproval ? (
                  <div className="mt-8 text-tag text-ink-3">
                    当前定稿：{currentApproval.actor} · {currentApproval.reason} · subject{' '}
                    <Mono className="text-micro">
                      {currentApproval.subject_sha256.slice(0, 16)}…
                    </Mono>
                  </div>
                ) : d.approved ? (
                  <div className="mt-8 text-tag text-ink-3">
                    盘上仍可追溯旧审批，但它没有通过当前 workpack 证明，不能充当交付闸。
                    请运行 stage7.rename --recheck。
                  </div>
                ) : null}
              </Panel>

              {workpackQ.data ? <Stage7WorkpackEvidence workpack={workpackQ.data} /> : null}

              <div className="prose-cn text-tag text-ink-4">
                正文只读——改正文回成稿屏。人物表编辑走 CLI（stage7.rename --apply-edit）。
              </div>

              {/* 漏网叫法候选：唯一能发现人名表漏了人的地方 */}
              <Panel>
                <PaneHeader
                  title="漏网叫法候选"
                  meta={d.residual.length + ' 条'}
                  right={
                    <span className="prose-cn text-tag text-ink-4">
                      漏网候选只读展示；安全 high 候选由流水线自动采纳。
                    </span>
                  }
                />
                {d.residual.length ? (
                  d.residual.map((r, i) => (
                    <div key={i} className="flex flex-col gap-6 border-b border-line-row px-18 py-12">
                      <div className="flex flex-wrap items-center gap-8">
                        <Mono className="rounded-tag bg-inset px-7 py-1 text-mini">{r.stem}</Mono>
                        <Mono className="text-micro text-ink-5">{r.where}</Mono>
                        <span
                          className={cx(
                            'rounded-tag px-7 py-1 text-mini',
                            r.confidence === 'low' && 'bg-inset text-ink-3',
                          )}
                          style={
                            r.confidence === 'low'
                              ? undefined
                              : { color: SEV.hard.color, background: SEV.hard.bg }
                          }
                        >
                          {r.confidence}
                        </span>
                        {d.baselines[r.stem] ? (
                          <Mono className="text-micro text-ink-5">
                            该姓字 {d.baselines[r.stem].covered}/{d.baselines[r.stem].total}{' '}
                            已覆盖，残留 {d.baselines[r.stem].residual}
                          </Mono>
                        ) : null}
                      </div>
                      <div className="prose-cn text-section text-ink-2">
                        <HighlightedText parts={splitOnce(r.context, r.unit ?? r.hit)} />
                      </div>
                      <span
                        className="text-tag"
                        style={r.suggest ? undefined : { color: SEV.unver.color }}
                      >
                        {r.suggest ? '建议：' + r.suggest : '模型没给建议——要你判断它到底是不是人名'}
                      </span>
                    </div>
                  ))
                ) : (
                  <div className="prose-cn px-18 py-14 text-sm2 text-ink-4">
                    没有漏网叫法候选。空不代表「一定没漏」——这一项查的是姓字残留，改了名却换了称呼的漏法它查不出来。
                  </div>
                )}
              </Panel>

              <div
                className="grid items-start gap-14"
                style={{ gridTemplateColumns: 'minmax(0,1.4fr) minmax(0,0.6fr)' }}
              >
                <Panel>
                  <PaneHeader
                    title="旧人名表 ↔ 新人名表"
                    meta={
                      d.characters.length +
                      ' 人 · ' +
                      d.characters.filter((c) => c.renamed).length +
                      ' 人改名'
                    }
                  />
                  <div
                    className="grid gap-10 border-b border-line-soft px-18 py-9 text-tag text-ink-4"
                    style={{ gridTemplateColumns: 'minmax(120px,1fr) 18px minmax(120px,1fr) minmax(0,2fr) 52px' }}
                  >
                    <span>旧</span>
                    <span />
                    <span>新</span>
                    <span>别称 / 理由</span>
                    <span className="text-right">命中</span>
                  </div>
                  {d.characters.map((c) => (
                    <div
                      key={c.char_id}
                      className={cx(
                        'grid items-start gap-10 border-b border-line-row px-18 py-11',
                        c.renamed ? 'bg-transparent' : 'bg-sunken-2',
                      )}
                      style={{ gridTemplateColumns: 'minmax(120px,1fr) 18px minmax(120px,1fr) minmax(0,2fr) 52px' }}
                    >
                      <span className="truncate text-read" title={c.old_canonical}>
                        {c.old_canonical}
                      </span>
                      <span className="text-ink-6">→</span>
                      <span className="flex min-w-0 flex-col gap-2">
                        <span
                          className={cx('truncate text-read', c.renamed ? 'font-semibold' : 'text-ink-5')}
                          title={c.renamed ? c.new_canonical : c.old_canonical}
                        >
                          {c.renamed ? c.new_canonical : c.old_canonical}
                        </span>
                        {!c.renamed ? (
                          <span className="text-micro" style={{ color: SEV.unver.color }}>
                            没改：{c.not_renamed_reason || '未给理由'}
                          </span>
                        ) : null}
                      </span>
                      <span className="flex min-w-0 flex-col gap-5">
                        <span className="flex flex-wrap gap-4">
                          {c.aliases.map((a, i) => {
                            const changed = a.old !== a.new
                            return (
                              <span
                                key={i}
                                className={cx(
                                  'rounded-[11px] px-8 py-2 text-mini',
                                  changed ? 'bg-accent-bg text-accent-ink' : 'bg-inset text-ink-4',
                                )}
                              >
                                {a.old}
                                {changed ? ' → ' + a.new : ' 未变'}
                              </span>
                            )
                          })}
                        </span>
                        <span className="prose-cn text-micro text-ink-5">
                          {c.renamed ? c.reason : c.not_renamed_reason}
                        </span>
                      </span>
                      <Mono className="text-right text-micro text-ink-5">{c.hits}</Mono>
                    </div>
                  ))}
                </Panel>

                <div className="flex flex-col gap-14">
                  <Panel className="flex flex-col gap-8 px-18 py-14">
                    <span className="text-section font-semibold">姓氏映射</span>
                    {Object.entries(d.surname_map).map(([k, v]) => (
                      <Mono key={k} className="text-body">
                        {k} → {v}
                      </Mono>
                    ))}
                    <span className="mt-4 text-tag text-ink-4">
                      替换面 {d.subs_count} 条 · 保留不换 {d.kept_count} 条
                    </span>
                  </Panel>

                  <Panel className="flex flex-col gap-8 px-18 py-14">
                    <span className="text-section font-semibold">补充的替换</span>
                    {d.extra_subs.length ? (
                      d.extra_subs.map((s, i) => (
                        <Mono key={i} className="text-body">
                          {s.old} → {s.new}
                          <span className="ml-6 text-micro text-ink-5">
                            {ORIGIN_LABEL[s.origin] ?? s.origin}
                          </span>
                        </Mono>
                      ))
                    ) : (
                      <span className="text-sm2 text-ink-4">没有补充的替换。</span>
                    )}
                  </Panel>

                  <Panel className="flex flex-col gap-8 px-18 py-14">
                    <span className="text-section font-semibold">书名 / 成书</span>
                    <span className="text-body text-ink-2">{d.title}</span>
                    <Mono className="text-micro text-ink-5">
                      {d.book_txt
                        ? d.book_filename + ' · ' +
                          d.book_txt.chars +
                          ' 字 · ' +
                          d.book_txt.chapters +
                          ' 章 · sha1 ' +
                          d.book_txt.sha1.slice(0, 12) +
                          (d.book_txt_on_disk ? ' · 在盘上' : ' · ⚠ 账在而盘上没有——重跑 stage7.assemble')
                        : '还没组装成书'}
                    </Mono>
                    <p className="m-0 text-body text-ink-2">{q.isFetching ? '正在重验交付状态' : DELIVERY_LABEL[d.delivery.state] + ' · ' + d.delivery.reason}</p>
                    {!bookOpen && download.isError ? <LoadFailed error={download.error} compact /> : null}
                    {deliveryReady ? (
                      <div className="mt-3 flex flex-wrap gap-8">
                        <button
                          type="button"
                          onClick={() => setBookOpen(true)}
                          className="cursor-pointer rounded-btn border border-line-btn bg-card px-12 py-6 text-body font-semibold"
                        >
                          预览成书
                        </button>
                        <button
                          type="button"
                          disabled={download.isPending}
                          onClick={() => download.mutate()}
                          className="cursor-pointer rounded-btn border border-line-btn bg-card px-12 py-6 text-body font-semibold text-ink disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          {download.isPending ? '下载中…' : '下载 ' + d.book_filename}
                        </button>
                      </div>
                    ) : (
                      /*
                        ⚠⚠ **最后一步的发起入口。** rename 机械 seal 后 s7 闸打开，
                        本屏提供 assemble 入口；人物表已定稿却永不拼装是旧 spawn 病。
                      */
                      <div className="mt-3 flex flex-wrap items-center gap-10">
                        <ActionButton
                          size="sm"
                          action={assembleAction}
                          onClick={() =>
                            ask({
                              kind: 'stage7.assemble',
                              label: '拼装最终成书',
                              extra: null,
                              required_inputs: assembleAction?.required_inputs,
                              sid,
                              ordinal: null,
                            })
                          }
                        >
                          拼装成书
                        </ActionButton>
                        <span className="text-tag text-ink-4">
                          按原书结构拼装（文案 + 正文各章），落到 stage7/ 下、以书名命名。
                        </span>
                      </div>
                    )}
                  </Panel>
                </div>
              </div>

              <Panel>
                <PaneHeader title="文案替换前后" meta="hook" />
                {d.hook ? (
                  <div className="grid grid-cols-2 gap-16 px-18 py-14">
                    <Prose text={d.hook.before} measure="compare" size="md" tone="ink-2" />
                    <Prose text={d.hook.after} measure="compare" size="md" />
                  </div>
                ) : (
                  <div className="prose-cn px-18 py-14 text-sm2 text-ink-4">
                    文案还没定稿——对照的左边没有来源。去文案屏定稿后这里才有内容。
                  </div>
                )}
              </Panel>

              <Panel>
                <PaneHeader
                  title="逐章替换前后对照"
                  meta={
                    ordinal === null
                      ? '选一章看'
                      : rows
                        ? '第 ' + ordinal + ' 章 · ' + rows.length + ' 行 · 其中 ' + changedRows + ' 行有替换'
                        : '第 ' + ordinal + ' 章'
                  }
                  right={
                    <ChapterStrip
                      items={d.chapters.map((n) => ({
                        n,
                        active: n === ordinal,
                        dim: !hasSample(sid, n),
                        go: () => nav('/n/' + sid + '/stage7/' + n),
                      }))}
                    />
                  }
                />
                {ordinal === null ? (
                  <div className="prose-cn px-18 py-14 text-sm2 text-ink-4">
                    上面的人物表是全书级的。逐章对照按章取数，点右边的章号看某一章。
                  </div>
                ) : chQ.isPending ? (
                  <div className="px-18 py-14 text-sm2 text-ink-4">取这一章的对照 …</div>
                ) : rows ? (
                  <CompareRows
                    captions={['替换前', '替换后']}
                    rows={rows.map((r, i) => ({
                      key: 'r' + i,
                      left: r.l,
                      right: r.r,
                      anchored: true,
                      note: r.changed ? '这一行有替换' : undefined,
                    }))}
                  />
                ) : chQ.isError && !isNoSample(chQ.error) ? (
                  /* 真实 500 不能吞成「没有在样本里」——取数失败就是取数失败 */
                  <div className="px-18 py-14">
                    <LoadFailed error={chQ.error} onRetry={() => void chQ.refetch()} compact />
                  </div>
                ) : (
                  <div className="px-18 py-14">
                    <HatchNote>
                      第 {ordinal} 章的替换对照没有在样本里（GET …/stage7/{ordinal}）。上面的人物表是全书级的，照常有效——只有这一块按章取数。
                      样本包只抓了第 {defaultOrdinal(sid)} 章。
                    </HatchNote>
                  </div>
                )}
              </Panel>

              {bookOpen ? (
                <div
                  className="fixed inset-0 z-50 flex justify-end"
                  style={{ backgroundColor: SCRIM }}
                  onClick={() => setBookOpen(false)}
                >
                  <aside
                    className="flex h-full w-[min(760px,92vw)] flex-col border-l border-line-btn bg-card shadow-modal"
                    onClick={(event) => event.stopPropagation()}
                  >
                    <div className="flex items-center gap-10 border-b border-line-soft px-20 py-14">
                      <span className="text-h3 font-semibold">成书预览</span>
                      <Mono className="text-mini text-ink-5">
                        {d.book_txt ? d.book_txt.chars + ' 字 · ' + d.book_txt.chapters + ' 章' : ''}
                      </Mono>
                      <button
                        type="button"
                        disabled={!deliveryReady || download.isPending}
                        onClick={() => download.mutate()}
                        className="ml-auto cursor-pointer rounded-btn border border-line-btn bg-card px-12 py-6 text-body font-semibold text-ink disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {download.isPending ? '下载中…' : '下载'}
                      </button>
                      <button
                        type="button"
                        onClick={() => setBookOpen(false)}
                        className="cursor-pointer rounded-btn border border-line-btn bg-card px-12 py-6 text-body"
                      >
                        关闭
                      </button>
                    </div>
                    {deliveryReady && bookQ.isError ? <LoadFailed error={bookQ.error} onRetry={() => void bookQ.refetch()} compact /> : <pre className="m-0 min-h-0 flex-1 overflow-y-auto px-28 py-22 font-sans text-read leading-[2] whitespace-pre-wrap text-ink">
                      {!deliveryReady
                        ? (q.isFetching ? '正在重验交付状态' : d.delivery.reason)
                        : bookQ.isPending || bookQ.isFetching
                          ? '读取成书中…'
                          : bookQ.data}
                    </pre>}
                    {download.isError ? (
                      <LoadFailed error={download.error} compact />
                    ) : null}
                  </aside>
                </div>
              ) : null}
            </section>
          )
        }}
      </QueryStates>
    </ScreenFrame>
  )
}

function Stage7WorkpackEvidence({ workpack }: { workpack: Stage7ApprovalWorkpack }) {
  return (
    <details open className="overflow-hidden rounded-card border border-line-row bg-sunken-2">
      <summary className="cursor-pointer px-16 py-11 text-body font-semibold text-ink-2">
        hash-bound 人名终审包 · {workpack.disposition} · {workpack.characters.length} 人
      </summary>
      <div className="flex flex-col gap-12 border-t border-line-row px-16 py-12">
        {workpack.check_basis === 'name_map_without_approval_or_delivery' ? (
          <p className="prose-cn text-tag text-ink-3">
            机械核验以未附审批和交付记录的人物表为基准，其中未审批、未组书的提示不表示当前状态；当前批准看审批状态，下载看成书状态。
          </p>
        ) : null}
        <Mono className="break-all text-micro text-ink-4">
          workpack {workpack.workpack_sha256}
        </Mono>
        <div className="grid gap-4 text-micro text-ink-4 md:grid-cols-2">
          <Mono className="break-all">artifact {workpack.artifact_subject_sha256}</Mono>
          <Mono className="break-all">decision {workpack.decision_subject_sha256}</Mono>
        </div>
        <div className="grid gap-8 md:grid-cols-2">
          <div className="rounded-btn border border-line-row bg-card px-10 py-8 text-tag">
            <div className="font-semibold text-ink-2">姓氏映射</div>
            <div className="text-ink-3">
              {Object.entries(workpack.surname_map)
                .map(([old, next]) => `${old}→${next}`).join(' · ') || '（空）'}
            </div>
          </div>
          <div className="rounded-btn border border-line-row bg-card px-10 py-8 text-tag">
            <div className="font-semibold text-ink-2">
              人工补充替换（{workpack.extra_subs.length}）
            </div>
            <div className="text-ink-3">
              {workpack.extra_subs
                .map((item) => `${item.old}→${item.new}（${item.origin}）`).join(' · ') || '（空）'}
            </div>
          </div>
        </div>
        <div className="grid gap-8 md:grid-cols-2">
          {workpack.characters.map((character) => (
            <div key={character.char_id} className="rounded-btn border border-line-row bg-card px-10 py-8 text-tag">
              <div className="font-semibold text-ink-2">
                {character.old_canonical} → {character.new_canonical} · 实际命中 {character.hits}
              </div>
              <div className="text-ink-4">
                别名：{character.aliases.map((alias) => `${alias.old}→${alias.new}`).join('、') || '（无）'}
              </div>
              <div className="prose-cn text-ink-3">
                {character.renamed
                  ? character.reason
                  : `明确不改：${character.not_renamed_reason ?? '未说明理由'}`}
              </div>
            </div>
          ))}
        </div>
        <div className="flex flex-col gap-4 text-tag text-ink-3">
          <span className="font-semibold">
            最终替换表 {workpack.subs_count} 项 · 明确保留 {workpack.kept_count} 项
          </span>
          <span className="font-semibold">逐章 + hook 单次扫描真实替换命中</span>
          <span>
            {Object.entries(workpack.replacement_hits)
              .map(([old, count]) => `${old} ${count}`).join(' · ') || '0 次'}
          </span>
          <span className="font-semibold">明确保留（{workpack.kept_count}）</span>
          <span>{workpack.kept.join('、') || '（空）'}</span>
        </div>
        <div className="grid gap-8 md:grid-cols-2">
          <div className="rounded-btn border border-line-row bg-card px-10 py-8 text-tag">
            <div className="font-semibold text-ink-2">姓字覆盖基线</div>
            {Object.entries(workpack.baselines).map(([stem, baseline]) => (
              <div key={stem}>
                {stem}：{baseline.covered}/{baseline.total} · residual {baseline.residual} · ratio{' '}
                {baseline.ratio === null ? 'unknown' : baseline.ratio.toFixed(3)}
              </div>
            ))}
          </div>
          <div className="rounded-btn border border-line-row bg-card px-10 py-8 text-tag">
            <div className="font-semibold text-ink-2">恒等往返基线</div>
            {workpack.identity ? (
              <>
                <div>
                  {workpack.identity.roundtrip_ok ? '守恒' : '不守恒'} · 扫描{' '}
                  {workpack.identity.chapters} 章 · mismatch{' '}
                  {workpack.identity.mismatches.join('、') || '（无）'}
                </div>
                <div>盲区：{workpack.identity.blind.join('；')}</div>
              </>
            ) : '未运行（不是通过）'}
          </div>
        </div>
        <div className="flex flex-col gap-5 text-tag text-ink-3">
          <span className="font-semibold">人物表结论（{workpack.book.length}）</span>
          {workpack.book.map((finding, index) => (
            <div
              key={`${finding.rule}:${finding.evidence_key || finding.detail}:${index}`}
              className="rounded-btn border border-line-row bg-card px-10 py-8"
            >
              <Mono>{finding.rule} · {finding.sev}</Mono>
              <div className="prose-cn text-ink-2">{finding.detail}</div>
            </div>
          ))}
        </div>
        <div className="flex flex-col gap-7">
          <span className="text-tag font-semibold text-ink-2">
            残留上下文（{workpack.residual.length}）
          </span>
          {workpack.residual.map((item, index) => (
            <div key={index} className="rounded-btn border border-line-row bg-card px-10 py-8 text-tag">
              <Mono>{item.where} · {item.stem} · {item.confidence}</Mono>
              <div className="prose-cn text-ink-2">{item.context}</div>
              <div className="text-ink-4">
                {item.unit ?? item.hit} → {item.suggest ?? '（无建议，需人工判断）'}
              </div>
            </div>
          ))}
        </div>
        <div className="text-tag text-ink-4">
          机械规则 {workpack.check.rules_scanned} 条：ok {workpack.check.counts.ok} · hit{' '}
          {workpack.check.counts.hit} · unver {workpack.check.counts.unver} · skipped{' '}
          {workpack.check.counts.skipped}。来源 {workpack.check.provenance.freshness}/
          {workpack.check.provenance.reason}；异常 {workpack.check.attention.length} 条，
          metric {workpack.check.metrics.length} 条。
        </div>
        {[...workpack.check.attention, ...workpack.check.metrics].map((row) => (
          <div key={row.rule} className="rounded-btn border border-line-row bg-card px-10 py-8 text-tag">
            <div className="flex flex-wrap gap-6">
              <Mono>{row.rule}</Mono>
              <span>{row.sev}/{row.status}</span>
              {row.scanned ? <span>扫描：{row.scanned}</span> : null}
            </div>
            {row.why ? <div className="prose-cn">{row.why}</div> : null}
            {row.findings.map((finding, index) => (
              <div key={`${finding.rule}:${finding.evidence_key || finding.detail}:${index}`} className="prose-cn text-ink-2">
                {finding.detail}
              </div>
            ))}
          </div>
        ))}
      </div>
    </details>
  )
}

/** 在上下文里把那个词标出来。只做 indexOf，不解析。 */
function splitOnce(text: string, needle: string) {
  const i = needle ? text.indexOf(needle) : -1
  if (i < 0) return [{ text }]
  return [
    { text: text.slice(0, i) },
    { text: needle, sev: 'hard' as const },
    { text: text.slice(i + needle.length) },
  ]
}
