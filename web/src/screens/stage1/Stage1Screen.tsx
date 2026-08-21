import { useRef, useState } from 'react'
import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import type { Stage1HookCopy } from '@/types/stages'
import { useSid } from '@/hooks/useRouteParams'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { QueryStates } from '@/components/state/QueryStates'
import { LoadFailed, LoadingPane } from '@/components/state/States'
import { Mono } from '@/components/ui/Mono'
import { ChapterGrid } from '@/components/ui/ChapterGrid'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { Prose } from '@/components/ui/Prose'
import { cx } from '@/lib/cx'

export function Stage1Screen() {
  const sid = useSid()
  const documentPane = useRef<HTMLDivElement>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const segments = useQuery({
    queryKey: qk.s1segments(sid),
    queryFn: () => api.getStage1Segments(sid),
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

  const scrollTo = (ordinal: number) => {
    setSelected(ordinal)
    const target = documentPane.current?.querySelector<HTMLElement>(
      '[data-chapter=' + String.fromCharCode(34) + ordinal + String.fromCharCode(34) + ']',
    )
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <ScreenFrame screen={'stage1'} allowCheckRerun={false}>
      <QueryStates q={segments} loadingLabel={'读 Stage 1 连续切分'}>
        {(document) => (
          <QueryStates q={chapters} loadingLabel={'读章节索引'}>
            {(index) => (
              <section className={'flex flex-col gap-16 px-30 py-24'}>
                    <header className={'flex items-start justify-between gap-24'}>
                      <div className={'flex flex-col gap-5'}>
                        <h1 className={'m-0 text-screen font-semibold'}>正文切分 · stage1</h1>
                        <p className={'prose-cn m-0 max-w-[58em] text-body text-ink-3'}>
                          这里是 Stage 0 批准后的完整 clean_text。文案段、章节、结尾标记与任何空洞都由后端按同一坐标系切好，本页只读。
                        </p>
                      </div>
                      <div className={'flex flex-col items-end gap-2 text-right'}>
                        <Mono className={'text-tag text-ink-4'}>{index.chapters.length} 章</Mono>
                        <Mono className={'text-tag text-ink-4'}>
                          input sha1 {document.input_sha1.slice(0, 12)}
                        </Mono>
                      </div>
                    </header>

                    <Panel className={'flex flex-wrap items-center gap-12 px-18 py-13'}>
                      <span className={'rounded-chip bg-gate-ok-bg px-9 py-3 text-tag font-semibold text-ink-2'}>
                        只读切分结果
                      </span>
                      <span className={'text-body font-semibold'}>
                        1 段开篇文案 · {index.chapters.length} 章正文
                      </span>
                      <span className={'text-tag text-ink-4'}>
                        连续分区 {document.segments.length} 段 · 没有前端二次切字
                      </span>
                      {document.segments.some((item) => item.kind === 'hole') ? (
                        <span className={'ml-auto text-body font-semibold text-warn'}>
                          检出未覆盖空洞，请先检查分章器
                        </span>
                      ) : null}
                    </Panel>

                    <div
                      className={'grid items-start gap-16'}
                      style={{ gridTemplateColumns: '320px minmax(0,1fr)' }}
                    >
                      <div className={'sticky top-[60px] flex flex-col gap-14'}>
                        <HookPanel q={hook} />

                        <Panel>
                          <PaneHeader title={'章节索引'} meta={index.chapters.length + ' 章'} />
                          <ChapterGrid
                            layout={'list'}
                            className={'max-h-[52vh] overflow-y-auto'}
                            items={index.chapters.map((chapter) => ({
                              ...chapter,
                              ordinal: chapter.chapter_ordinal,
                              active: selected === chapter.chapter_ordinal,
                            }))}
                            onPick={scrollTo}
                          >
                            {(chapter) => (
                              <>
                                <span className={'flex items-center gap-8'}>
                                  <Mono
                                    className={'rounded-tag bg-accent-solid px-7 py-1 text-mini text-card'}
                                  >
                                    ch{String(chapter.chapter_ordinal).padStart(2, '0')}
                                  </Mono>
                                  <span className={'truncate text-body font-semibold'}>
                                    {chapter.source_chapter_no}
                                  </span>
                                  <Mono className={'ml-auto text-micro text-ink-5'}>
                                    {chapter.chars} 字
                                  </Mono>
                                </span>
                                <span className={'line-clamp-2 text-tag text-ink-4'}>
                                  {chapter.preview}
                                </span>
                              </>
                            )}
                          </ChapterGrid>
                        </Panel>
                      </div>

                      <Panel>
                        <PaneHeader
                          title={'完整 clean_text'}
                          meta={'服务端连续分区 · 按原顺序逐字展示'}
                          right={
                            <span className={'text-tag text-ink-4'}>
                              紫 = 文案 · 蓝 = 章节 · 虚线 = 结尾
                            </span>
                          }
                        />
                        <div ref={documentPane}>
                          {document.segments.map((segment) => (
                            <article
                              key={segment.id}
                              data-chapter={segment.chapter_ordinal ?? undefined}
                              className={cx(
                                'scroll-mt-[110px] border-b border-line-row border-l-5',
                                segment.kind === 'hook' && 'border-l-hook bg-hook-bg',
                                segment.kind === 'chapter' && (
                                  selected === segment.chapter_ordinal
                                    ? 'border-l-accent-solid bg-accent-bg'
                                    : 'border-l-accent-line bg-card'
                                ),
                                segment.kind === 'end_marker' &&
                                  'border-l-line-btn border-dashed bg-inset',
                                segment.kind === 'hole' && 'border-l-warn bg-warn-bg',
                              )}
                            >
                              <div
                                className={cx(
                                  'sticky top-[45px] z-1 flex items-center gap-8 border-b ' +
                                    'border-line-soft px-18 py-8 text-tag font-semibold',
                                  segment.kind === 'hook' && 'bg-hook-head text-hook-ink',
                                  segment.kind === 'chapter' && 'bg-accent-head text-accent-ink',
                                  segment.kind === 'end_marker' && 'bg-inset text-ink-4',
                                  segment.kind === 'hole' && 'bg-warn-bg text-warn',
                                )}
                              >
                                <span>
                                  {segment.kind === 'hook'
                                    ? '开篇文案'
                                    : segment.kind === 'chapter'
                                      ? '第 ' + segment.chapter_ordinal + ' 章'
                                      : segment.kind === 'end_marker'
                                        ? '结尾标记'
                                        : '未覆盖空洞'}
                                </span>
                                {segment.source_chapter_no !== null ? (
                                  <span className={'font-normal text-ink-4'}>
                                    原文标记「{segment.source_chapter_no}」
                                  </span>
                                ) : null}
                                <Mono className={'ml-auto text-micro text-ink-5'}>
                                  [{segment.start}, {segment.end}) · {segment.chars} 字
                                </Mono>
                              </div>
                              <div className={'px-18 py-14'}>
                                <div
                                  className={
                                    'prose-cn max-w-[var(--measure-solo)] text-read leading-[2.05] ' +
                                    'whitespace-pre-wrap text-ink'
                                  }
                                >
                                  {segment.text}
                                </div>
                              </div>
                            </article>
                          ))}
                        </div>
                      </Panel>
                    </div>
                  </section>
            )}
          </QueryStates>
        )}
      </QueryStates>
    </ScreenFrame>
  )
}

/**
 * 开篇文案面板。hook_copy 查询的四态在这一个面板里就地消化——
 * 它取数失败只换这一块，不再把已加载的切分与章节索引整屏替掉。
 */
function HookPanel({ q }: { q: UseQueryResult<Stage1HookCopy> }) {
  if (q.isPending) {
    return (
      <Panel>
        <LoadingPane label={'读文案段'} rows={2} />
      </Panel>
    )
  }
  if (q.isError) {
    return (
      <Panel>
        <div className={'p-14'}>
          <LoadFailed error={q.error} onRetry={() => void q.refetch()} compact />
        </div>
      </Panel>
    )
  }
  return (
    <Panel>
      <PaneHeader title={'开篇文案'} meta={q.data.hook_copy.text.length + ' 字'} />
      <div className={'border-l-5 border-l-hook bg-hook-bg px-16 py-14'}>
        <Prose text={q.data.hook_copy.text} measure={'full'} size={'sm'} tone={'ink-2'} />
      </div>
      <div className={'border-t border-line-soft px-16 py-10 text-tag text-ink-4'}>{q.data.note}</div>
    </Panel>
  )
}
