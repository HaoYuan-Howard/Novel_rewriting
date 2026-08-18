import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { MASKED_SECRET_ID } from '@/api/spoilers'
import { FRESH, MUTED, SEV, SPOILER_ON } from '@/constants/vocabulary'
import { useApprovals } from '@/hooks/useApprovals'
import { useEnums } from '@/hooks/useEnums'
import { useSampleCoverage } from '@/hooks/useSample'
import { useSid } from '@/hooks/useRouteParams'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { QueryStates } from '@/components/state/QueryStates'
import { EmptyIsSuspicious } from '@/components/state/States'
import { LinkButton } from '@/components/ui/ActionButton'
import { Mono } from '@/components/ui/Mono'
import { Panel } from '@/components/ui/Panel'
import { cx } from '@/lib/cx'
import { GlobalReviewPanel } from './GlobalReviewPanel'

type Tab = 'chars' | 'rels' | 'rules' | 'secrets' | 'arc'

/**
 * 全局设定。人物、关系、世界规则、秘密、弧线 —— 抽对了吗、抽全了吗。
 *
 * 两条最要紧的：
 *   · 剧透开关是**部分遮蔽**，服务端下发的那句 spoiler_disclosure.notice 必须常驻显示，
 *     不能收进 tooltip：一个没标注的部分遮蔽，会被当成完整遮蔽用。
 *   · secrets 是空数组 ≠ 这本书没有秘密。
 */
export function GlobalScreen() {
  const sid = useSid()
  const nav = useNavigate()
  const { doc, markSeen } = useApprovals()
  const { defaultOrdinal } = useSampleCoverage()
  const [spoilers, setSpoilers] = useState(false)
  const [tab, setTab] = useState<Tab>('chars')

  const q = useQuery({
    queryKey: qk.global(sid, spoilers),
    queryFn: () => api.getGlobal(sid, spoilers),
    retry: false,
  })
  const enums = useEnums()
  const maskNote =
    enums.data?.spoiler_disclosure?.notice ??
    '仅在屏幕展示层隐藏 fate / secrets / climax / ending；服务端仍全量下发，浏览器网络与缓存仍可能看到。'

  useEffect(() => {
    if (doc && !doc.seen.global && !markSeen.isPending) markSeen.mutate('global')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [doc?.seen.global])

  return (
    <ScreenFrame screen="global" onJumpChapter={(n) => nav('/n/' + sid + '/read/' + n)}>
      <section className="flex flex-col gap-16 px-30 py-24">
        <header className="flex items-start justify-between gap-24">
          <div className="flex flex-col gap-5">
            <h1 className="m-0 text-screen font-semibold">全局设定 · stage2</h1>
            <div className="text-body text-ink-3">
              人物、关系、世界规则、秘密、弧线——抽对了吗、抽全了吗。
            </div>
          </div>
          <div className="flex max-w-[620px] flex-col items-end gap-7">
            <button
              type="button"
              onClick={() => setSpoilers((value) => !value)}
              className={cx(
                'flex cursor-pointer items-center gap-8 rounded-btn border px-12 py-6 text-body',
                !spoilers && 'border-line-btn bg-card',
              )}
              style={
                spoilers
                  ? { borderColor: SPOILER_ON.border, background: SPOILER_ON.bg }
                  : undefined
              }
            >
              <span
                aria-hidden
                className="inline-block h-9 w-9 rounded-full"
                style={{ background: spoilers ? SPOILER_ON.dot : MUTED }}
              />
              隐藏核心剧透字段（非完整遮蔽）：{spoilers ? '已关闭' : '已开启'}
            </button>
            <div className="flex items-start gap-7 text-right" role="note">
              <span
                aria-hidden
                className="mt-4 inline-block h-10 w-10 flex-none bg-warn"
                style={{ clipPath: 'polygon(50% 0,100% 100%,0 100%)' }}
              />
              <span className="prose-cn text-tag text-warn-ink">{maskNote}</span>
            </div>
          </div>
        </header>
        <GlobalReviewPanel sid={sid} />
        <QueryStates q={q} loadingLabel="读全局设定" missing={{ title: '全局设定 · stage2' }}>
        {(d) => {
          const tabs: { key: Tab; label: string; n: number }[] = [
            { key: 'chars', label: '人物', n: d.characters.length },
            { key: 'rels', label: '关系', n: d.relations.length },
            { key: 'rules', label: '世界规则', n: d.world_rules.length },
            { key: 'secrets', label: '秘密', n: spoilers ? d.secrets.length : 0 },
            { key: 'arc', label: '弧线', n: d.arc.arc_tracks.length },
          ]
          const chapters = Math.max(
            8,
            ...d.relations.map((r) => r.valid_to_chapter ?? r.valid_from_chapter),
          )

          return (
            <div className="flex flex-col gap-16">
              <div className="grid gap-12" style={{ gridTemplateColumns: 'repeat(2,1fr)' }}>
                {Object.entries(d.setting).map(([k, v]) => (
                  <Panel key={k} className="flex flex-col gap-4 px-16 py-13">
                    <span className="text-tag text-ink-4">{k}</span>
                    <span className="prose-cn text-section text-ink-2">{v}</span>
                  </Panel>
                ))}
              </div>

              <div className="flex flex-wrap gap-8">
                {tabs.map((t) => (
                  <button
                    key={t.key}
                    type="button"
                    onClick={() => setTab(t.key)}
                    className={cx(
                      'cursor-pointer rounded-btn-lg border px-14 py-7 text-body-lg',
                      tab === t.key
                        ? 'border-ink bg-ink text-card'
                        : 'border-line-btn bg-card text-ink-2',
                    )}
                  >
                    {t.label}
                    <Mono
                      className="ml-7 rounded-tag px-6 py-1 text-mini"
                      // 计数为 0 的用「过期」橙 —— 空要读起来可疑，不是干净
                      title={t.n === 0 ? '这一类一条都没抽到，可能是漏抽了' : undefined}
                    >
                      <span
                        className={tab === t.key ? 'text-card' : 'text-ink-4'}
                        style={t.n === 0 ? { color: FRESH.stale.color } : undefined}
                      >
                        {t.n}
                      </span>
                    </Mono>
                  </button>
                ))}
              </div>

              {tab === 'chars' ? (
                <div
                  className="grid gap-12"
                  style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(400px,1fr))' }}
                >
                  {d.characters.map((c) => (
                    <Panel key={c.char_id} className="flex flex-col gap-8 px-16 py-13">
                      <div className="flex flex-wrap items-center gap-8">
                        <span className="text-h3 font-semibold">{c.canonical}</span>
                        <Mono className="text-micro text-ink-5">{c.char_id}</Mono>
                        <span className="ml-auto rounded-tag bg-inset px-7 py-1 text-micro text-ink-4">
                          辨识度 {c.name_distinctiveness}
                        </span>
                      </div>
                      <div className="prose-cn text-body-lg text-ink-2">{c.role}</div>
                      <div className="flex flex-wrap gap-5">
                        {c.address.map((a, i) => (
                          <span
                            key={i}
                            className="rounded-[11px] bg-inset px-8 py-2 text-mini text-ink-3"
                          >
                            {/* by 是对象，取 display —— 设计稿这里拼出了 [object Object] */}
                            {a.surface}（{a.by.display}·{a.context}，第{a.from_chapter}章起）
                          </span>
                        ))}
                      </div>
                      {/* 遮住 / 没有这个字段 / 可见，是三件事 */}
                      <div
                        className={cx(
                          'rounded-btn px-12 py-8 text-sm2',
                          spoilers ? 'border border-line-2 bg-sunken-2 text-ink-2' : 'hatch-tight border border-dashed border-line-dash text-ink-5',
                        )}
                      >
                        {c.fate ?? '（这个人没有 fate 字段）'}
                      </div>
                    </Panel>
                  ))}
                </div>
              ) : null}

              {tab === 'rels' ? (
                <Panel>
                  <div
                    className="grid gap-12 border-b border-line-soft px-18 py-10 text-tag text-ink-4"
                    style={{ gridTemplateColumns: '64px minmax(0,1fr) minmax(90px,150px) minmax(0,1fr) 210px' }}
                  >
                    <span>id</span>
                    <span>主</span>
                    <span>谓</span>
                    <span>宾</span>
                    <span>成立区间（章）</span>
                  </div>
                  {d.relations.map((r) => {
                    const from = r.valid_from_chapter
                    const to = r.valid_to_chapter ?? chapters
                    return (
                      <div
                        key={r.relation_id}
                        className="grid items-center gap-12 border-b border-line-row px-18 py-10"
                        style={{ gridTemplateColumns: '64px minmax(0,1fr) minmax(90px,150px) minmax(0,1fr) 210px' }}
                      >
                        <Mono className="text-micro text-ink-5">{r.relation_id}</Mono>
                        <span className="text-body">{r.subject.display}</span>
                        <span className="truncate text-body text-ink-3" title={r.predicate}>
                          {r.predicate}
                        </span>
                        <span className="text-body">{r.object.display}</span>
                        <span className="flex items-center gap-8">
                          <span className="relative h-7 min-w-0 flex-1 overflow-hidden rounded-tag bg-inset">
                            <span
                              className="absolute top-0 bottom-0 bg-accent-bar"
                              style={{
                                left: ((from - 1) / chapters) * 100 + '%',
                                width: ((to - from + 1) / chapters) * 100 + '%',
                              }}
                            />
                          </span>
                          <Mono className="w-52 flex-none text-micro text-ink-5">
                            {from}–{to} 章
                          </Mono>
                        </span>
                      </div>
                    )
                  })}
                </Panel>
              ) : null}

              {tab === 'rules' ? (
                <div className="flex flex-col gap-12">
                  {d.world_rules.map((w) => (
                    <Panel key={w.rule_id} className="flex gap-16 px-18 py-14">
                      <div className="flex w-150 flex-none flex-col gap-3">
                        <Mono className="text-mini text-ink-4">{w.rule_id}</Mono>
                        <span className="text-tag text-ink-3">{w.kind}</span>
                      </div>
                      <div className="flex min-w-0 flex-1 flex-col gap-8">
                        {w.versions.map((v, i) => (
                          <div key={i} className="flex gap-10">
                            <Mono className="flex-none text-mini text-ink-5">
                              第 {v.effective_from_chapter} 章起
                            </Mono>
                            <span className="prose-cn text-section text-ink-2">{v.desc}</span>
                          </div>
                        ))}
                      </div>
                    </Panel>
                  ))}
                </div>
              ) : null}

              {tab === 'secrets' ? (
                d.secrets.length ? (
                  <div className="flex flex-col gap-10">
                    {d.secrets.map((s) => (
                      <Panel key={s.secret_id} className="flex flex-col gap-5 px-18 py-14">
                        {s.secret_id === MASKED_SECRET_ID ? null : (
                          <Mono className="text-mini text-ink-4">{s.secret_id}</Mono>
                        )}
                        <span className="prose-cn text-section text-ink-2">{s.fact}</span>
                        {s.secret_id === MASKED_SECRET_ID ? null : (
                          <span className="text-tag text-ink-4">
                            知情：{s.known_by.map((k) => k.display).join('、') || '—'} · 第{' '}
                            {s.reader_reveal_chapters.join('、')} 章揭示
                            {s.setup_chapters.length
                              ? ' · 埋点 ch' + s.setup_chapters.join('/')
                              : ''}
                          </span>
                        )}
                      </Panel>
                    ))}
                  </div>
                ) : (
                  <EmptyIsSuspicious
                    title="secrets 是空数组 —— 一条秘密都没抽到"
                    body="空数组的意思是「这本书没抽到秘密」，不等于「这本书没有秘密」。这一屏不替你下结论：要判断是真没有还是漏抽了，去对着原文看一遍。"
                    extra={
                      !spoilers && d.secrets.length ? (
                        <div
                          className="rounded-btn border border-dashed px-12 py-8 text-sm2"
                          style={{ borderColor: SEV.unver.border, color: SEV.unver.color }}
                        >
                          剧透开关关着时另有 {d.secrets.length} 条被遮住的秘密字段——遮蔽和空是两回事。
                        </div>
                      ) : null
                    }
                    action={
                      <LinkButton onClick={() => nav('/n/' + sid + '/read/' + defaultOrdinal(sid))}>
                        对着原文看一遍
                      </LinkButton>
                    }
                  />
                )
              ) : null}

              {tab === 'arc' ? (
                <div
                  className="grid items-start gap-16"
                  style={{ gridTemplateColumns: 'minmax(420px,0.9fr) minmax(520px,1.1fr)' }}
                >
                  <Panel className="flex flex-col gap-10 px-18 py-14">
                    <span className="text-section font-semibold">冲突阶段</span>
                    {d.arc.conflict_stages.map((s, i) => (
                      <div key={i} className="flex gap-14">
                        <Mono className="w-70 flex-none text-mini text-ink-5">
                          {s.chapters.join(',')} 章
                        </Mono>
                        <span className="prose-cn text-section text-ink-2">{s.stage}</span>
                      </div>
                    ))}
                  </Panel>
                  <Panel className="flex flex-col gap-10 px-18 py-14">
                    <span className="text-section font-semibold">人物弧线</span>
                    {d.arc.arc_tracks.map((t, i) => (
                      <div
                        key={i}
                        className="grid items-baseline gap-12"
                        style={{ gridTemplateColumns: '86px 76px 1fr' }}
                      >
                        <span className="text-body font-semibold">{t.subject.display}</span>
                        <span className="text-tag text-ink-4">{t.dimension}</span>
                        <span className="prose-cn text-section text-ink-2">
                          {t.change_type} · {t.trajectory}
                        </span>
                      </div>
                    ))}
                  </Panel>
                  <Panel className="flex flex-col gap-10 px-18 py-14">
                    <span className="text-section font-semibold">高潮 / 结局</span>
                    <div className="flex flex-col gap-3">
                      <span className="text-tag text-ink-4">climax</span>
                      <span className="prose-cn text-section text-ink-2">{d.arc.climax}</span>
                    </div>
                    <div className="flex flex-col gap-3">
                      <span className="text-tag text-ink-4">ending</span>
                      <span className="prose-cn text-section text-ink-2">{d.arc.ending}</span>
                    </div>
                  </Panel>
                </div>
              ) : null}
            </div>
          )
        }}
        </QueryStates>
      </section>
    </ScreenFrame>
  )
}
