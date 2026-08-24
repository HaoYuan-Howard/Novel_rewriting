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

/** Stage3 v2：免费保存的机械基线，与当前代码重算的机械测量并排。 */
export function StyleScreen() {
  const sid = useSid()
  const q = useQuery({ queryKey: qk.style(sid), queryFn: () => api.getStyle(sid), retry: false })

  return (
    <ScreenFrame screen="style">
      <QueryStates q={q} loadingLabel="读机械基线" missing={{ title: '机械基线 · stage3' }}>
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
                  <h1 className="m-0 text-screen font-semibold">机械基线 · stage3</h1>
                  <div className="text-body text-ink-3">
                    Stage3 v2 不再调用模型抽取文风；它免费保存机械测量，供下游做确定性归一与检查。
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
                    ? '没有两份可比基线'
                    : drift.length
                      ? drift.length + ' 项机械测量漂移'
                      : '存量与当前测量一致'}
                </span>
              </header>

              <ComparePanes
                sticky="none"
                left={
                  <Panel>
                    <PaneHeader title="当前重算" meta="读取 stage1，服务端 0 成本现算" />
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
                                      {same ? '与存量基线一致' : '存量 ' + num(stored)}
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
                        title="存量 Stage3 v2 机械档位"
                        meta={{
                          ready: '来自盘上的 style_grounding.json',
                          missing: '尚未保存；运行免费的 stage3.style 即可建立',
                          invalid: '检测到旧 INVALID；免费重跑会写入当前 v2',
                          unreadable: '盘上产物不符合当前 v2；必须免费重建',
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
                            title={d.exists ? '旧产物不可读' : '还没有机械基线'}
                            body="Stage3 现在是 0 成本节点；重新运行会按当前测量契约生成 v2，不迁移或手补旧 v1。"
                          />
                        </div>
                      )}
                      {d.readable === false ? (
                        <div
                          className="prose-cn border-t border-line-soft px-18 py-10 text-tag"
                          style={{ color: SEV.unver.color }}
                        >
                          盘上的文件存在，但不能按当前 v2 schema 读取；这不是“没有运行过”。
                        </div>
                      ) : null}
                    </Panel>

                    <Panel>
                      <PaneHeader title="原作开篇文案" meta="Stage1 原文；只读参考" />
                      {d.hook_copy_text ? (
                        <div className="bg-hook-bg px-18 py-12">
                          <Prose text={d.hook_copy_text} measure="compare" size="md" />
                        </div>
                      ) : (
                        <div className="px-18 py-12 text-sm2 text-ink-4">Stage1 尚无可展示的文案原文。</div>
                      )}
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
