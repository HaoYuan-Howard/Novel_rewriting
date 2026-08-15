import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { FRESH, MUTED, SEV } from '@/constants/vocabulary'
import { useSid } from '@/hooks/useRouteParams'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { ComparePanes } from '@/components/compare/ComparePanes'
import { QueryStates } from '@/components/state/QueryStates'
import { NotRunYet } from '@/components/state/States'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { Prose } from '@/components/ui/Prose'
import type { StyleBodyMetrics } from '@/types/stages'
import { cx } from '@/lib/cx'

const FIELDS: [string, keyof StyleBodyMetrics][] = [
  ['字数', 'chars'],
  ['段落数', 'paragraphs'],
  ['句数', 'sentences'],
  ['平均段长', 'avg_paragraph_chars'],
  ['段间空行', 'paragraph_sep_chars'],
  ['平均句长', 'avg_sentence_chars'],
  ['对白比', 'dialogue_ratio'],
]

function num(v: unknown): string {
  if (typeof v === 'number') return String(Math.round(v * 1000) / 1000)
  if (Array.isArray(v)) return v.join('')
  return v === undefined || v === null ? '—' : String(v)
}

/**
 * 文风画像。
 * 左边是服务端 0 成本现算出来的实测数字，右边是真正发给写手的档位词。
 * **档位词有没有变，比数字有没有变更要紧** —— 写手只看得到档位词。
 * exists 为 false（stage3 还没买）时左半边照样有内容。
 */
export function StyleScreen() {
  const sid = useSid()
  const q = useQuery({ queryKey: qk.style(sid), queryFn: () => api.getStyle(sid), retry: false })

  return (
    <ScreenFrame screen="style">
      <QueryStates q={q} loadingLabel="量文风" missing={{ title: '文风画像 · stage3' }}>
        {(d) => {
          const dims = Object.keys(d.bands_now ?? {})
          // drift=null 是「无从比」（有一边算不出来），[] 才是「比过了没漂移」
          const drift = d.drift ?? []
          const driftUnknown = d.drift === null
          const groups = [
            { label: '正文 body', now: d.measured_now?.body, stored: d.measurable_stored?.body },
            { label: '文案段 hook', now: d.measured_now?.hook, stored: d.measurable_stored?.hook },
          ]

          return (
            <section className="flex flex-col gap-16 px-30 py-24">
              <header className="flex items-end justify-between gap-24">
                <div className="flex flex-col gap-5">
                  <h1 className="m-0 text-screen font-semibold">文风画像 · stage3</h1>
                  <div className="text-body text-ink-3">
                    左边是量出来的数字，右边是真正发给写手的档位词。档位词有没有变，比数字有没有变更要紧。
                  </div>
                </div>
                <span
                  className="rounded-btn px-11 py-4 text-body font-semibold"
                  style={
                    driftUnknown
                      ? { color: FRESH.unknown.color, background: FRESH.unknown.bg }
                      : drift.length
                        ? { color: FRESH.stale.color, background: FRESH.stale.bg }
                        : { color: FRESH.fresh.color, background: FRESH.fresh.bg }
                  }
                >
                  {driftUnknown
                    ? '有一边量不出来——无从比'
                    : drift.length
                      ? drift.length + ' 项实测漂了'
                      : '两份实测逐项相同'}
                </span>
              </header>

              <ComparePanes
                sticky="none"
                left={
                  <Panel>
                    <PaneHeader title="左 · 实测" meta="服务端 0 成本现算，不用跑 stage3" />
                    {d.measure_error ? (
                      <div className="p-16">
                        <NotRunYet title="量不出来" body={d.measure_error} />
                      </div>
                    ) : (
                      groups.map((g) => (
                        <div key={g.label} className="border-b border-line-row">
                          <div className="bg-sunken-2 px-18 py-8 text-tag font-semibold text-ink-3">
                            {g.label}
                          </div>
                          {g.now ? (
                            <div className="flex flex-col">
                              {FIELDS.map(([label, key]) => {
                                const now = g.now![key]
                                const stored = g.stored?.[key]
                                const same = JSON.stringify(now) === JSON.stringify(stored)
                                return (
                                  <div
                                    key={label}
                                    className="flex items-baseline gap-12 border-b border-line-row px-18 py-8"
                                  >
                                    <span className="w-110 flex-none text-body text-ink-3">
                                      {label}
                                    </span>
                                    <Mono className="text-body-lg">{num(now)}</Mono>
                                    <span
                                      className="ml-auto text-micro"
                                      style={
                                        same ? { color: MUTED } : { color: FRESH.stale.color }
                                      }
                                    >
                                      {same ? '与上次实测一致' : '上次 ' + num(stored)}
                                    </span>
                                  </div>
                                )
                              })}
                              <div className="flex items-baseline gap-12 px-18 py-8">
                                <span className="w-110 flex-none text-body text-ink-3">
                                  {g.now.chapter_chars ? '章篇幅 min/avg/max' : '引号体'}
                                </span>
                                <Mono className="text-body-lg">
                                  {g.now.chapter_chars
                                    ? g.now.chapter_chars.min +
                                      '/' +
                                      g.now.chapter_chars.avg +
                                      '/' +
                                      g.now.chapter_chars.max
                                    : g.now.quote_styles.join('')}
                                </Mono>
                                {g.now.chapter_chars ? (
                                  <span className="ml-auto text-micro text-ink-6">
                                    引号体 {g.now.quote_styles.join('')}
                                  </span>
                                ) : null}
                              </div>
                            </div>
                          ) : (
                            <div className="px-18 py-12 text-sm2 text-ink-4">
                              这一组没有实测值 —— 不是 0，是没量到。
                            </div>
                          )}
                        </div>
                      ))
                    )}
                  </Panel>
                }
                right={
                  <div className="flex flex-col gap-14">
                    <Panel>
                      <PaneHeader
                        title="右 · 真正发给写手的档位词"
                        meta={{
                          ready: '来自盘上的 style_grounding.json',
                          missing: '还没买——右半边是空的',
                          invalid: '最近一轮无效；右侧若有内容，是较早的正式稿',
                          unreadable: '盘上产物无法按当前契约读取',
                        }[d.condition]}
                      />
                      {d.bands_stored ? (
                        dims.map((k) => {
                          const changed = (d.bands_now ?? {})[k] !== d.bands_stored?.[k]
                          return (
                            <div
                              key={k}
                              className="flex flex-wrap items-center gap-12 border-b border-line-row px-18 py-11"
                            >
                              <span className="w-130 flex-none text-body text-ink-3">{k}</span>
                              <span
                                className={cx(
                                  'rounded-btn px-11 py-3 text-section',
                                  changed ? 'font-semibold' : 'bg-inset text-ink',
                                )}
                                style={
                                  changed
                                    ? { background: FRESH.stale.bg, color: FRESH.stale.color }
                                    : undefined
                                }
                              >
                                {(d.bands_now ?? {})[k]}
                              </span>
                              {changed ? (
                                <Mono className="text-micro text-ink-5">
                                  盘上那份是「{d.bands_stored?.[k]}」
                                </Mono>
                              ) : null}
                            </div>
                          )
                        })
                      ) : (
                        <div className="p-16">
                          <NotRunYet
                            title="stage3 还没跑"
                            body="档位词是 stage3 花钱产出的东西，盘上还没有。左半边的实测数字是 0 成本现算的，照常可看——但写手手里现在什么档位词都没有。"
                          />
                        </div>
                      )}
                    </Panel>

                    <Panel>
                      <PaneHeader title="要模仿的那支笔" meta={d.voice.length + ' 条 voice'} />
                      {d.voice.map((v) => (
                        <div key={v.key} className="border-b border-line-row px-18 py-11">
                          <Mono className="text-micro text-ink-5">{v.key}</Mono>
                          <div className="prose-cn mt-4 text-section text-ink-2">{v.text}</div>
                        </div>
                      ))}
                      {d.readable === false ? (
                        <div
                          className="prose-cn border-t border-line-soft px-18 py-10 text-tag"
                          style={{ color: SEV.unver.color }}
                        >
                          这份产物在盘上但读不回来（不合当前契约）——它跑过，不是「还没跑」。
                        </div>
                      ) : null}
                    </Panel>

                    <Panel>
                      <PaneHeader title="写作习惯" meta={d.surface_habits.length + ' 条'} />
                      <ul className="m-0 flex list-none flex-col gap-6 px-18 py-12">
                        {d.surface_habits.map((h, i) => (
                          <li key={i} className="prose-cn text-section text-ink-2">
                            · {h}
                          </li>
                        ))}
                      </ul>
                    </Panel>

                    <Panel>
                      <PaneHeader title="钩子写法" meta={d.hook_style.length + ' 项'} />
                      {d.hook_style.map((h) => (
                        <div key={h.key} className="border-b border-line-row px-18 py-10">
                          <Mono className="text-micro text-ink-5">{h.key}</Mono>
                          <div className="prose-cn mt-3 text-section text-ink-2">{h.text}</div>
                        </div>
                      ))}
                      {d.hook_copy_text ? (
                        <div className="border-t border-line-soft bg-hook-bg px-18 py-12">
                          <div className="mb-6 text-tag font-semibold text-hook-ink">
                            原作的开篇文案
                          </div>
                          <Prose text={d.hook_copy_text} measure="compare" size="md" />
                        </div>
                      ) : null}
                    </Panel>
                  </div>
                }
              />
            </section>
          )
        }}
      </QueryStates>
    </ScreenFrame>
  )
}
