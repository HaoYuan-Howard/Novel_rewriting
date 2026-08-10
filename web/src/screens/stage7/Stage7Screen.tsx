import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { FRESH, SEV } from '@/constants/vocabulary'
import { useSampleCoverage } from '@/hooks/useSample'
import { useSid } from '@/hooks/useRouteParams'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { CompareRows } from '@/components/compare/CompareRows'
import { QueryStates } from '@/components/state/QueryStates'
import { HatchNote } from '@/components/state/States'
import { ActionButton } from '@/components/ui/ActionButton'
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
  const nav = useNavigate()
  const qc = useQueryClient()
  const { o } = useParams<{ o: string }>()
  const ordinal = o ? Number(o) : null
  const { hasSample, defaultOrdinal } = useSampleCoverage()

  const q = useQuery({ queryKey: qk.stage7(sid), queryFn: () => api.getStage7(sid), retry: false })
  const chQ = useQuery({
    queryKey: qk.stage7ch(sid, ordinal ?? 0),
    queryFn: () => api.getStage7Chapter(sid, ordinal!),
    enabled: ordinal !== null,
    retry: false,
  })

  const approve = useMutation({
    mutationFn: () => api.approveNames(sid),
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.stage7(sid) }),
  })

  return (
    <ScreenFrame screen="stage7">
      <QueryStates q={q} loadingLabel="读人名表" missing={{ title: '人名替换 + 成书 · stage7' }}>
        {(d) => {
          const rows = chQ.data?.rows ?? null
          const changedRows = rows?.filter((r) => r.kind !== 'same').length ?? 0

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
                  <ActionButton
                    action={{ kind: 'n9_rename', extra: ['--approve'], paid: false }}
                    onClick={() => approve.mutate()}
                  >
                    {d.approved ? '重新定稿' : '定稿人物表'}
                  </ActionButton>
                </div>
              </header>

              <div className="prose-cn text-tag text-ink-4">{d.scope_note}</div>

              {/* 漏网叫法候选：唯一能发现人名表漏了人的地方 */}
              <Panel>
                <PaneHeader
                  title="漏网叫法候选"
                  meta={d.residual.length + ' 条'}
                  right={<span className="prose-cn text-tag text-ink-4">{d.residual_note}</span>}
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
                        <HighlightedText parts={splitOnce(r.context, r.unit)} />
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
                    style={{ gridTemplateColumns: '120px 18px 120px minmax(0,1fr) 52px' }}
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
                      style={{ gridTemplateColumns: '120px 18px 120px minmax(0,1fr) 52px' }}
                    >
                      <span className="text-read">{c.old_canonical}</span>
                      <span className="text-ink-6">→</span>
                      <span className="flex flex-col gap-2">
                        <span
                          className={cx('text-read', c.renamed ? 'font-semibold' : 'text-ink-5')}
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
                            const changed = c.changed_aliases.some((x) => x.old === a.old)
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
                    <span className="text-body text-ink-2">
                      {d.title} → <strong>{d.title_new}</strong>
                    </span>
                    <Mono className="text-micro text-ink-5">
                      {d.book_txt
                        ? 'book.txt · ' +
                          d.book_txt.chars +
                          ' 字 · ' +
                          d.book_txt.chapters +
                          ' 章 · sha1 ' +
                          d.book_txt.sha1.slice(0, 12) +
                          (d.book_txt.has_hook ? ' · 含文案' : '')
                        : '还没组装成书'}
                    </Mono>
                  </Panel>
                </div>
              </div>

              <Panel>
                <PaneHeader title="文案替换前后" meta="hook" />
                <div className="grid grid-cols-2 gap-16 px-18 py-14">
                  <Prose text={d.hook.before} measure="compare" size="md" tone="ink-2" />
                  <Prose text={d.hook.after} measure="compare" size="md" />
                </div>
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
                      note: r.kind === 'same' ? undefined : '这一行有替换',
                    }))}
                  />
                ) : (
                  <div className="px-18 py-14">
                    <HatchNote>
                      第 {ordinal} 章的替换对照没有在样本里（GET …/stage7/{ordinal}）。上面的人物表是全书级的，照常有效——只有这一块按章取数。
                      样本包只抓了第 {defaultOrdinal(sid)} 章。
                    </HatchNote>
                  </div>
                )}
              </Panel>
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
