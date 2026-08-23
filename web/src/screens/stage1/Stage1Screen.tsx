import { useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { useSid } from '@/hooks/useRouteParams'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { QueryStates } from '@/components/state/QueryStates'
import { Mono } from '@/components/ui/Mono'
import { ChapterGrid } from '@/components/ui/ChapterGrid'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { CheckPanel } from '@/components/check/CheckPanel'
import { GateBar } from '@/components/gate/GateBar'
import { cx } from '@/lib/cx'
import type { ChapterLabelAnomaly } from '@/types/stages'

/** 语义来自服务端 anomaly；这里只把闭合取值面翻成界面文案。 */
const ANOMALY_LABEL: Record<ChapterLabelAnomaly, string> = {
  reset: '章号倒退',
  gap: '章号缺号',
  repeat: '章号重复',
}

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
  const scrollTo = (ordinal: number) => {
    setSelected(ordinal)
    const target = documentPane.current?.querySelector<HTMLElement>(
      '[data-chapter=' + String.fromCharCode(34) + ordinal + String.fromCharCode(34) + ']',
    )
    target?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <ScreenFrame screen={'stage1'} showCheck={false} showGate={false}>
      <section className={'flex flex-col gap-16 px-30 py-24'}>
        <header className={'flex items-start justify-between gap-24'}>
          <h1 className={'m-0 text-screen font-semibold'}>正文切分 · stage1</h1>
          {chapters.data ? (
            <Mono className={'text-tag text-ink-4'}>{chapters.data.chapters.length} 章</Mono>
          ) : null}
        </header>

        {/* 恢复面独立于正文/章表查询：正文接口坏了，复检与确认仍必须可达。 */}
        <div
          role={'group'}
          aria-label={'Stage1 校验与切分确认'}
          className={'overflow-hidden rounded-card border border-line bg-card'}
        >
          <CheckPanel
            screen={'stage1'}
            onJumpChapter={scrollTo}
            allowRerun={false}
            embedded
            headerAction={<GateBar screen={'stage1'} actionOnly />}
          />
        </div>

        <QueryStates q={segments} loadingLabel={'读 Stage 1 连续切分'}>
          {(document) => (
            <QueryStates q={chapters} loadingLabel={'读章节索引'}>
              {(index) => (
                <>
                  <Panel className={'flex flex-wrap items-center gap-12 px-18 py-13'}>
                    <span className={'rounded-chip bg-gate-ok-bg px-9 py-3 text-tag font-semibold text-ink-2'}>
                      只读切分结果
                    </span>
                    <span className={'text-body font-semibold'}>
                      1 段开篇文案 · {index.chapters.length} 章正文
                    </span>
                    {index.anomaly_count ? (
                      <span className={'text-body font-semibold text-warn'}>
                        章号异常 {index.anomaly_count} 处，请逐章核对
                      </span>
                    ) : null}
                    {document.segments.some((item) => item.kind === 'hole') ? (
                      <span className={'text-body font-semibold text-warn'}>
                        检出未覆盖空洞，请先检查分章器
                      </span>
                    ) : null}
                  </Panel>

                  <div
                    className={'grid items-start gap-16'}
                    style={{ gridTemplateColumns: '320px minmax(0,1fr)' }}
                  >
                    <div className={'sticky top-[60px] flex flex-col gap-14'}>
                      <Panel>
                        <PaneHeader
                          title={'章节索引'}
                          meta={
                            index.anomaly_count
                              ? index.chapters.length + ' 章 · 异常 ' + index.anomaly_count
                              : index.chapters.length + ' 章'
                          }
                        />
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
                                  原标号 {chapter.source_chapter_no}
                                </span>
                                <Mono className={'ml-auto text-micro text-ink-5'}>
                                  {chapter.chars} 字
                                </Mono>
                              </span>
                              {chapter.anomaly ? (
                                <span className={'text-tag font-semibold text-warn'}>
                                  {ANOMALY_LABEL[chapter.anomaly]}
                                  {chapter.label_delta === null ? null : `（差值 ${chapter.label_delta}）`}
                                </span>
                              ) : null}
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
                        title={'原文切分结果'}
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
                </>
              )}
            </QueryStates>
          )}
        </QueryStates>
      </section>
    </ScreenFrame>
  )
}
