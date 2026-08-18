import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { errorBody, isNoSample, isNotRunYet } from '@/api/errors'
import { qk } from '@/api/queryKeys'
import { lookupAction } from '@/constants/actions'
import { FRESH, SCRIM, SEV } from '@/constants/vocabulary'
import { useSampleCoverage } from '@/hooks/useSample'
import { useChrome, useAskJob } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import { useSid } from '@/hooks/useRouteParams'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { CompareRows } from '@/components/compare/CompareRows'
import { QueryStates } from '@/components/state/QueryStates'
import { ErrorHint, HatchNote, LoadFailed } from '@/components/state/States'
import { ActionButton } from '@/components/ui/ActionButton'
import { ActorReasonFields } from '@/components/ui/ActorReasonFields'
import { ChapterStrip } from '@/components/ui/ChapterStrip'
import { HighlightedText } from '@/components/ui/HighlightedText'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { Prose } from '@/components/ui/Prose'
import { cx } from '@/lib/cx'

/**
 * 人名替换 + 成书。
 * 新旧人名表并排 + 逐章替换前后对照，人定稿。
 * 「漏网叫法候选」是唯一能发现人名表本身漏了人的地方 —— 放在最上面。
 */
export function Stage7Screen() {
  const sid = useSid()
  const { askConfirm, notify } = useChrome()
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
  const [actor, setActor] = useState('')
  const [reason, setReason] = useState('')

  const q = useQuery({ queryKey: qk.stage7(sid), queryFn: () => api.getStage7(sid), retry: false })
  const chQ = useQuery({
    queryKey: qk.stage7ch(sid, ordinal ?? 0),
    queryFn: () => api.getStage7Chapter(sid, ordinal!),
    enabled: ordinal !== null,
    retry: false,
  })
  const bookQ = useQuery({
    queryKey: qk.stage7book(sid),
    queryFn: () => api.getStage7Book(sid),
    enabled: bookOpen,
    retry: false,
  })
  const download = useMutation({
    mutationFn: () => api.downloadStage7Book(sid),
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

  const approve = useMutation({
    mutationFn: (req: { actor: string; reason: string; subject_sha256: string }) =>
      api.approveNames(sid, req),
    onSuccess: (job) => {
      if (job.state !== 'failed') notify('人名表已定稿')
      void qc.invalidateQueries({ queryKey: qk.stage7(sid) })
      void qc.invalidateQueries({ queryKey: qk.check(sid, 'stage7') })
      void qc.invalidateQueries({ queryKey: qk.approvals(sid) })
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },
  })

  const adoptResidual = useMutation({
    mutationFn: () => api.adoptResidual(sid, { confidence: 'high' }),
    onSuccess: (job) => {
      if (job.state !== 'failed') notify('已采纳可安全应用的 high 候选')
      void qc.invalidateQueries({ queryKey: qk.stage7(sid) })
      void qc.invalidateQueries({ queryKey: qk.check(sid, 'stage7') })
      void qc.invalidateQueries({ queryKey: qk.approvals(sid) })
      void qc.invalidateQueries({ queryKey: qk.jobs(sid) })
    },
  })

  const askApprove = (subjectSha256: string) =>
    askConfirm({
      req: {
        kind: 'stage7.approve',
        label: '人名表定稿（绑定当前 decision surface）',
        extra: [
          '--actor', actor.trim(),
          '--reason', reason.trim(),
          '--subject-sha256', subjectSha256,
        ],
        sid,
        ordinal: null,
      },
      run: () => approve.mutateAsync({
        actor: actor.trim(),
        reason: reason.trim(),
        subject_sha256: subjectSha256,
      }),
    })

  const askAdoptResidual = () =>
    askConfirm({
      req: {
        kind: 'stage7.rename',
        label: '采纳可安全应用的 high 漏网叫法候选',
        extra: ['--adopt-residual', 'high'],
        sid,
        ordinal: null,
      },
      run: () => adoptResidual.mutateAsync(),
    })

  return (
    <ScreenFrame screen="stage7">
      {/*
        ⚠⚠ **这一层的产出入口必须在这一屏上，否则浏览器里走不到成书。**
        `stage6.approve` 会自己盖上 s6 的章 ⇒ 闸门那颗按钮当场变「已确认」并禁用，
        而 `stage7.rename` 正是挂在它 spawn 上的 ⇒ 那条发起路再也不会被走到。
        与此同时本屏 404 的原话是「先跑产它的那一层」，却没给任何地方能跑它。
        两件事叠起来就是：文案定稿之后，整条链在浏览器里断在这里。
      */}
      {isNotRunYet(q.error) ? (
        <Panel className="mx-30 mt-20 flex flex-wrap items-center gap-12 px-18 py-14">
          <div className="flex min-w-0 flex-col gap-3">
            <span className="text-body font-semibold">还没跑人名替换</span>
            <span className="text-tag text-ink-4">
              stage6 文案已定稿。这一层要先产出 stage7/name_map.json，才能审人名、再拼装成书。
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
                mode: null,
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

          return (
            <section className="flex flex-col gap-16 px-30 py-24">
              <header className="flex items-end justify-between gap-24">
                <div className="flex flex-col gap-5">
                  <h1 className="m-0 text-screen font-semibold">人名替换 + 成书 · stage7</h1>
                  <div className="text-body text-ink-3">
                    新旧人名表并排看，确认没漏人；再看逐章替换前后对照，最后拼成书。
                  </div>
                </div>
                <div className="flex items-center gap-10">
                  <span
                    className="rounded-chip px-10 py-3 text-body font-semibold"
                    style={
                      d.approved
                        ? { color: FRESH.fresh.color, background: FRESH.fresh.bg }
                        : { color: SEV.unver.color, background: SEV.unver.bg }
                    }
                  >
                    {d.approved ? '人物表已定稿' : '人物表还没定稿'}
                  </span>
                </div>
              </header>

              {approve.isError ? (
                <div className="rounded-card border border-warn-line bg-warn-bg px-18 py-12 text-body text-warn-ink">
                  定稿失败：{errorBody(approve.error)}
                  <ErrorHint error={approve.error} />
                </div>
              ) : null}

              <ActorReasonFields
                actor={actor}
                reason={reason}
                onActor={setActor}
                onReason={setReason}
                note={
                  <>
                    <div>
                      定稿绑定当前姓氏映射、角色决定与人工补充替换的 SHA-256；表面变化后旧审批自动失效。
                    </div>
                    {d.approved ? (
                      <div className="text-ink-3">
                        当前审批：{d.approved.actor} · {d.approved.reason} · subject{' '}
                        <Mono className="text-micro">
                          {d.approved.subject_sha256.slice(0, 16)}…
                        </Mono>
                      </div>
                    ) : null}
                  </>
                }
                actions={
                  <ActionButton
                    action={{ kind: 'stage7.approve', extra: null, paid: false }}
                    onClick={() => askApprove(d.approval_subject_sha256)}
                    disabled={!actor.trim() || !reason.trim()}
                    disabledReason="请先填写自报身份与定稿理由"
                  >
                    {d.approved ? '重新定稿' : '定稿人物表'}
                  </ActionButton>
                }
              />

              <div className="prose-cn text-tag text-ink-4">
                正文只读——改正文回成稿屏。人物表编辑走 CLI（stage7.rename --apply-edit）。
              </div>

              {/* 漏网叫法候选：唯一能发现人名表漏了人的地方 */}
              <Panel>
                <PaneHeader
                  title="漏网叫法候选"
                  meta={d.residual.length + ' 条'}
                  right={
                    <div className="flex items-center gap-10">
                      <span className="prose-cn text-tag text-ink-4">
                        这是全链唯一能发现「人物表本身漏了人」的通道——按置信度从上往下看
                      </span>
                      <ActionButton
                        action={{
                          kind: 'stage7.rename',
                          extra: ['--adopt-residual', 'high'],
                          paid: false,
                        }}
                        size="sm"
                        onClick={askAdoptResidual}
                        disabled={!d.residual.some((item) =>
                          item.confidence === 'high' && !!item.unit && !!item.suggest)}
                        disabledReason="当前没有可提交给后端复核的 high 建议"
                      >
                        采纳安全 high 候选
                      </ActionButton>
                    </div>
                  }
                />
                {adoptResidual.isError ? (
                  <div className="border-b border-warn-line bg-warn-bg px-18 py-10 text-body text-warn-ink">
                    采纳失败：{errorBody(adoptResidual.error)}
                    <ErrorHint error={adoptResidual.error} />
                  </div>
                ) : null}
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
                    <span className="text-section font-semibold">人工补的替换</span>
                    {d.extra_subs.length ? (
                      d.extra_subs.map((s, i) => (
                        <Mono key={i} className="text-body">
                          {s.old} → {s.new}
                          <span className="ml-6 text-micro text-ink-5">
                            {s.origin === 'human' ? '人加的' : s.origin}
                          </span>
                        </Mono>
                      ))
                    ) : (
                      <span className="text-sm2 text-ink-4">没有人工补的替换。</span>
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
                    {d.book_txt_on_disk ? (
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
                        ⚠⚠ **最后一步的发起入口，和上面 rename 那条是同一个病。**
                        `stage7.approve` 一落盘就自己盖上 s7 的章 ⇒ 闸门那颗
                        「确认并成书」当场变成「已经确认过了」并禁用，
                        而 `stage7.assemble` 恰恰挂在它的 spawn 上 ⇒ 拼装再也发不起来。
                        人物表已定稿、书却永远组装不出来，是这条链在浏览器里的终点。
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
                              mode: null,
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
                        disabled={download.isPending}
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
                    <pre className="m-0 min-h-0 flex-1 overflow-y-auto px-28 py-22 font-sans text-read leading-[2] whitespace-pre-wrap text-ink">
                      {bookQ.isPending
                        ? '读取成书中…'
                        : bookQ.isError
                          ? '取不到成书；请确认文件仍在盘上。'
                          : bookQ.data}
                    </pre>
                    {download.isError ? (
                      <div className="border-t border-warn-line bg-warn-bg px-20 py-10 text-body text-warn-ink">
                        下载失败；请确认成书仍在盘上。
                      </div>
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
