import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { BADGE, FRESH, GATE } from '@/constants/vocabulary'
import { useSampleCoverage } from '@/hooks/useSample'
import { useSid } from '@/hooks/useRouteParams'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { ComparePanes } from '@/components/compare/ComparePanes'
import { QueryStates } from '@/components/state/QueryStates'
import { HatchNote } from '@/components/state/States'
import { LinkButton } from '@/components/ui/ActionButton'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { Prose } from '@/components/ui/Prose'
import { cx } from '@/lib/cx'

/**
 * 分章报告。左边是切分结果，右边是原文未切分全文，下滑对照。
 *
 * 「查过什么」要逐项列出来 —— 六项检查各带一个点。
 * 没有 warning 时也不能只显示一片绿：要写清楚「这不是没查」。
 */
export function Stage1Screen() {
  const sid = useSid()
  const nav = useNavigate()
  const { hasSample, defaultOrdinal } = useSampleCoverage()
  const [sel, setSel] = useState<number | null>(null)
  const rawPane = useRef<HTMLDivElement>(null)

  const report = useQuery({
    queryKey: qk.s1report(sid),
    queryFn: () => api.getStage1Report(sid),
    retry: false,
  })
  const chapters = useQuery({
    queryKey: qk.s1chapters(sid),
    queryFn: () => api.getStage1Chapters(sid),
    retry: false,
  })
  const hook = useQuery({
    queryKey: qk.s1hook(sid),
    queryFn: () => api.getStage1HookCopy(sid),
    retry: false,
  })
  const reading = useQuery({
    queryKey: qk.reading(sid, defaultOrdinal(sid)),
    queryFn: () => api.getReading(sid, defaultOrdinal(sid)),
    retry: false,
  })

  const scrollTo = (n: number) => {
    setSel(n)
    const el = rawPane.current?.querySelector<HTMLElement>('[data-ch="ch' + n + '"]')
    if (el) window.scrollTo({ top: el.getBoundingClientRect().top + window.scrollY - 120 })
  }

  return (
    <ScreenFrame screen="stage1">
      <QueryStates q={report} loadingLabel="读分章报告" missing={{ title: '分章报告 · stage1' }}>
        {(r) => {
          const warns = r.warnings
          const ok = warns.length === 0
          const checks = [
            { label: '章号严格递增', ok: r.chapter_nos_strictly_increasing },
            { label: '没有断号', ok: r.chapter_no_gaps.length === 0 },
            { label: '没有重号', ok: r.chapter_no_duplicates.length === 0 },
            { label: '有结尾标记', ok: r.has_end_marker },
            { label: '文案段已单独摘出', ok: r.hook_copy_present },
            { label: '剔除内容 ' + r.dropped_code_points + ' 字', ok: r.dropped_code_points === 0 },
          ]
          const stats = [
            { label: '总字数（码点）', value: String(r.total_code_points), note: '含文案段与被剔除内容' },
            { label: '正文字数', value: String(r.body_code_points), note: 'body_ratio ' + r.body_ratio },
            { label: '切出的段落数', value: String(r.num_sections), note: 'sections' },
            { label: '章数', value: String(r.num_chapters), note: '章号 ' + r.chapter_nos.join(',') },
            { label: '被剔除', value: r.dropped_code_points + ' 字', note: r.dropped.length + ' 段' },
            {
              label: '文案段',
              value: r.hook_copy_code_points + ' 字',
              note: r.hook_copy_present ? '已摘出，stage2 不消费它' : '没有',
            },
          ]
          const chs = chapters.data?.chapters ?? []
          const hookText = hook.data?.hook_copy.text ?? ''

          return (
            <section className="flex flex-col gap-18 px-30 py-26">
              <header className="flex items-end justify-between gap-24">
                <div className="flex flex-col gap-5">
                  <h1 className="m-0 text-screen font-semibold">分章报告 · stage1</h1>
                  <div className="text-body text-ink-3">
                    把整本 txt 分成文案段与正文各章，番外 / 后记 / 作者有话说一律剔除。
                  </div>
                </div>
                <div className="flex flex-col items-end gap-2 text-right">
                  <Mono className="text-tag text-ink-4">{r.original_filename}</Mono>
                  <Mono className="text-tag text-ink-4">
                    sha1 {r.source_sha1.slice(0, 10)} · {r.source_encoding} · {r.text_norm}
                  </Mono>
                </div>
              </header>

              {/* 设计稿里这个框绑了 s1HasWarn 但没人算，于是永远不出现。这里按 warnings 长度补上。 */}
              <div
                className="flex flex-col gap-11 rounded-card border border-l-5 px-20 py-16"
                style={{
                  borderColor: ok ? GATE.okLine : 'var(--color-warn-line)',
                  borderLeftColor: ok ? FRESH.fresh.color : FRESH.stale.color,
                  background: ok ? FRESH.fresh.wash : 'var(--color-warn-bg)',
                }}
              >
                <div className="flex flex-wrap items-center gap-10">
                  <span
                    className="rounded-chip px-9 py-3 text-tag font-semibold"
                    style={
                      ok
                        ? { color: BADGE.pass.color, background: BADGE.pass.bg }
                        : { color: BADGE.amber.color, background: BADGE.amber.bg }
                    }
                  >
                    {ok ? '没有可疑项' : warns.length + ' 条可疑'}
                  </span>
                  <span className="text-section font-semibold">
                    {ok ? '切法没有报出可疑之处' : '这几处切得可疑，要你看一眼'}
                  </span>
                </div>
                <div className="prose-cn max-w-[52em] text-body text-ink-2">
                  {ok
                    ? '这不是「没查」——下面六项是这一屏真正查过的东西，逐项列出来了。查不了的项会单独标成「查不了」，这里一项都没有。'
                    : 'warnings 是「切得可疑但不算错」，需要人看一眼再决定要不要重切。'}
                </div>
                {warns.map((w, i) => (
                  <div
                    key={i}
                    className={'rounded-btn border border-warn-line-2 bg-card px-12 py-8 ' +
                      'text-body text-ink-2'}
                  >
                    {w}
                  </div>
                ))}
                <div className="flex flex-wrap gap-8">
                  {checks.map((c) => (
                    <span
                      key={c.label}
                      className="flex items-center gap-7 rounded-btn border bg-card px-11 py-5 text-body text-ink-2"
                      style={{ borderColor: c.ok ? GATE.okLine : GATE.warnLine }}
                    >
                      <span
                        aria-hidden
                        className="inline-block h-8 w-8 rounded-full"
                        style={{ background: c.ok ? FRESH.fresh.color : FRESH.stale.color }}
                      />
                      {c.label}
                    </span>
                  ))}
                </div>
                {r.notes.length ? (
                  <ul className="m-0 flex list-none flex-col gap-3">
                    {r.notes.map((n, i) => (
                      <li key={i} className="prose-cn text-tag text-ink-4">
                        · {n}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>

              <div
                className="grid gap-1 overflow-hidden rounded-block-lg border border-line bg-line-2"
                style={{ gridTemplateColumns: 'repeat(6,1fr)' }}
              >
                {stats.map((s) => (
                  <div key={s.label} className="flex flex-col gap-3 bg-card px-14 py-12">
                    <span className="text-tag text-ink-4">{s.label}</span>
                    <Mono className="text-h2">{s.value}</Mono>
                    <span className="text-mini text-ink-5">{s.note}</span>
                  </div>
                ))}
              </div>

              <ComparePanes
                sticky="none"
                left={
                  <Panel>
                    <PaneHeader
                      title="切分后的原文"
                      meta={'1 段文案 + ' + chs.length + ' 章正文'}
                      right={
                        <span className="flex items-center gap-10 text-micro text-ink-4">
                          <span className="flex items-center gap-5">
                            <span className="inline-block h-8 w-8 rounded-nub bg-hook" />
                            文案段
                          </span>
                          <span className="flex items-center gap-5">
                            <span className="inline-block h-8 w-8 rounded-nub bg-accent-solid" />
                            正文各章
                          </span>
                        </span>
                      }
                    />
                    {hookText ? (
                      <div className="border-l-5 border-l-hook bg-hook-bg px-18 py-14">
                        <div className="mb-8 flex items-center gap-8">
                          <span className="rounded-tag bg-hook px-7 py-1 text-micro text-card">
                            文案段
                          </span>
                          <span className="text-body font-semibold">开篇文案段（钩子）</span>
                          <Mono className="text-micro text-ink-5">
                            {[...hookText].length} 字
                          </Mono>
                          <span className="text-tag text-ink-4">{hook.data?.note}</span>
                        </div>
                        <Prose text={hookText} measure="compare" size="md" />
                      </div>
                    ) : null}

                    {chs.map((c) => {
                      const on = sel === c.chapter_ordinal
                      return (
                        <div
                          key={c.chapter_ordinal}
                          onClick={() => scrollTo(c.chapter_ordinal)}
                          className={cx(
                            'cursor-pointer border-l-5',
                            on ? 'border-l-accent-solid bg-accent-bg' : 'border-l-accent-line bg-card',
                          )}
                        >
                          <div className="flex flex-wrap items-center gap-8 border-b border-dashed border-line-soft px-18 py-10">
                            <Mono className="rounded-tag bg-accent-solid px-7 py-1 text-mini text-card">
                              ch{String(c.chapter_ordinal).padStart(2, '0')}
                            </Mono>
                            <span className="text-tag text-ink-3">
                              原文标记「{c.source_chapter_label}」
                            </span>
                            <Mono className="text-micro text-ink-5">{c.chars} 字</Mono>
                            <LinkButton
                              className="ml-auto"
                              onClick={() => nav('/n/' + sid + '/read/' + c.chapter_ordinal)}
                            >
                              看骨架 →
                            </LinkButton>
                          </div>
                          <div className="px-18 py-12">
                            <Prose text={c.preview} measure="compare" size="sm" tone="ink-2" />
                          </div>
                        </div>
                      )
                    })}
                  </Panel>
                }
                right={
                  <Panel>
                    <PaneHeader
                      title="原文（未切分）"
                      meta="全书顺序铺开 · 章边界标在原文里 · 左右一起下滑对照"
                      sticky
                    />
                    <div ref={rawPane}>
                      {hookText ? (
                        <div className="border-b border-line-row border-l-5 border-l-hook">
                          <div className="bg-hook-head px-18 py-8 text-tag font-semibold text-hook-ink">
                            文案段（正文之前）
                          </div>
                          <div className="px-18 py-12">
                            <Prose text={hookText} measure="compare" size="md" />
                          </div>
                        </div>
                      ) : null}
                      {chs.map((c) => {
                        const has =
                          hasSample(sid, c.chapter_ordinal) &&
                          reading.data?.chapter_ordinal === c.chapter_ordinal
                        return (
                          <div
                            key={c.chapter_ordinal}
                            data-ch={'ch' + c.chapter_ordinal}
                            className="border-b border-line-row border-l-5 border-l-accent-line"
                          >
                            <div className="sticky top-[45px] z-1 bg-accent-head px-18 py-8 text-tag font-semibold text-accent-ink">
                              第 {c.chapter_ordinal} 章
                            </div>
                            <div className="px-18 py-12">
                              {has ? (
                                <Prose
                                  text={reading.data!.segments.map((s) => s.text).join('\n')}
                                  measure="compare"
                                  size="md"
                                />
                              ) : (
                                <HatchNote>
                                  这一章的逐字原文没有在样本里：原文按章取数（GET …/reading/
                                  {c.chapter_ordinal}），样本包只抓了第 {defaultOrdinal(sid)}{' '}
                                  章。左边的切分结果是全书的，所以这里留出这一章的位置——空的原因是「没抓样本」，不是「这一章没有原文」。
                                </HatchNote>
                              )}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </Panel>
                }
              />
            </section>
          )
        }}
      </QueryStates>
    </ScreenFrame>
  )
}
