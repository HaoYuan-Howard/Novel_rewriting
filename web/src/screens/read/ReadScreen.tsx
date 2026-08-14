import { useNavigate } from 'react-router-dom'
import { FRESH, SEV } from '@/constants/vocabulary'
import { useSampleCoverage } from '@/hooks/useSample'
import { ScreenFrame } from '@/components/app/ScreenFrame'
import { ComparePanes, PaneScroll } from '@/components/compare/ComparePanes'
import { QueryStates } from '@/components/state/QueryStates'
import { ChapterStrip } from '@/components/ui/ChapterStrip'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { LinkButton } from '@/components/ui/ActionButton'
import { cx } from '@/lib/cx'
import { useReadScreen, type BeatView, type SegView } from './useReadScreen'

/**
 * 原文 ↔ 骨架。
 * 这一章的原文里，哪些内容被抽成了骨架、哪些漏了 —— 只能靠人对着看。
 * 并排结构本身就是需求，不能改成上下堆叠或 tab。
 */
export function ReadScreen() {
  const vm = useReadScreen()
  const nav = useNavigate()
  const { hasSample } = useSampleCoverage()

  const strip = Array.from({ length: 8 }, (_, i) => i + 1).map((n) => ({
    n,
    active: n === vm.ordinal,
    dim: !hasSample(vm.sid, n),
    go: () => nav('/n/' + vm.sid + '/read/' + n),
  }))

  return (
    <ScreenFrame screen="read" onJumpChapter={(n) => nav('/n/' + vm.sid + '/read/' + n)}>
      <QueryStates
        q={vm.query}
        loadingLabel="读这一章的骨架"
        missing={{
          title: '原文 ↔ 骨架',
          chapters: [1, 2, 3, 4, 5, 6, 7, 8],
          ordinal: vm.ordinal,
          path: 'read',
        }}
      >
        {() => (
          <section className="flex flex-col">
            <header className="sticky top-0 z-10 flex flex-col gap-12 border-b border-line bg-sunken-3 px-30 pt-20 pb-14">
              <div className="flex items-end justify-between gap-24">
                <div className="flex flex-col gap-5">
                  <h1 className="m-0 text-h1 font-semibold">原文 ↔ 骨架 · 第 {vm.ordinal} 章</h1>
                  <div className="text-body text-ink-3">
                    这一章的原文里，哪些内容被抽成了骨架，哪些漏了。
                  </div>
                </div>
                <ChapterStrip items={strip} />
              </div>
              <div className="flex flex-wrap items-center gap-10">
                <span
                  className="rounded-chip px-10 py-3 text-body font-semibold"
                  style={{ color: vm.looseColor, background: vm.looseBg }}
                >
                  {vm.looseLabel}
                </span>
                <span className="rounded-chip bg-inset px-9 py-3 text-body text-ink-3">
                  {vm.counts}
                </span>
                <span className="rounded-chip bg-inset px-9 py-3 text-body text-ink-3">
                  叙述：{vm.narration}
                </span>
                <span className="text-tag text-ink-4">{vm.minNote}</span>
                <span className="ml-auto text-tag text-ink-4">{vm.selHint}</span>
              </div>
            </header>

            <div className="px-30 pt-14">
              <Panel className="flex items-start gap-16 px-18 py-13">
                <span className="flex-none text-sm2 text-ink-4">章摘要</span>
                <span className="prose-cn text-section text-ink-2">{vm.summary}</span>
              </Panel>
            </div>

            <ComparePanes
              className="px-30 pt-14 pb-8"
              left={
                <Panel>
                  <PaneHeader title="原文" meta={vm.leftMeta} sticky />
                  <div className="flex flex-col gap-2 px-20 pt-14 pb-24">
                    {vm.segments.map((s) => (
                      <Segment
                        key={s.key}
                        s={s}
                        onClick={() => s.beatId && vm.toggleBeat(s.beatId)}
                      />
                    ))}
                  </div>
                </Panel>
              }
              right={
                <Panel className="flex h-full min-h-0 flex-col">
                  <PaneHeader
                    title="抽出来的骨架"
                    meta={vm.rightMeta}
                    right={vm.selBeat ? <LinkButton onClick={vm.clearSel}>取消选中</LinkButton> : null}
                  />
                  <PaneScroll className="flex flex-col gap-9 px-16 pt-12 pb-18">
                    {vm.beats.map((b) => (
                      <Beat key={b.id} b={b} onClick={() => vm.toggleBeat(b.id)} />
                    ))}
                    <div
                      className="prose-cn rounded-btn-lg border border-dashed px-12 py-10 text-sm2 text-ink-4"
                      style={{
                        borderColor: vm.problemsEmpty ? SEV.unver.border : undefined,
                        background: vm.problemsEmpty ? SEV.unver.wash : undefined,
                      }}
                    >
                      {vm.problems}
                    </div>
                  </PaneScroll>
                </Panel>
              }
            />
          </section>
        )}
      </QueryStates>
    </ScreenFrame>
  )
}

function Segment({ s, onClick }: { s: SegView; onClick: () => void }) {
  if (s.kind === 'hole') {
    // 没被判为「漏」的空隙不渲染正文：它是间隙，不是内容
    return (
      <div
        className={cx(
          'border-l-3',
          s.flagged ? 'rounded-btn px-12 py-8' : 'border-l-transparent px-12 py-1',
        )}
        style={
          s.flagged ? { borderLeftColor: FRESH.stale.color, background: FRESH.stale.wash } : undefined
        }
      >
        <Mono className="block py-2 text-micro">
          <span className={s.flagged ? '' : 'text-ink-7'} style={s.flagged ? { color: FRESH.stale.color } : undefined}>
            {s.label}
          </span>
        </Mono>
        {s.paras.map((p, i) => (
          <p key={i} className="prose-cn m-0 text-section text-ink-4">
            {p}
          </p>
        ))}
      </div>
    )
  }
  return (
    <div
      onClick={onClick}
      className={cx(
        'cursor-pointer rounded-btn-lg border-l-3 px-14 py-8',
        s.selected ? 'border-l-accent bg-accent-bg' : 'border-l-line bg-transparent',
      )}
    >
      <Mono className={cx('block pt-2 pb-4 text-micro', s.selected ? 'text-accent-ink' : 'text-ink-5')}>
        {s.label}
      </Mono>
      <div className="prose-cn flex max-w-[var(--measure-read)] flex-col gap-8 text-read-lg text-ink">
        {s.paras.map((p, i) => (
          <p key={i} className="m-0">
            {p}
          </p>
        ))}
      </div>
    </div>
  )
}

function Beat({ b, onClick }: { b: BeatView; onClick: () => void }) {
  return (
    <div
      onClick={onClick}
      className={cx(
        'flex cursor-pointer flex-col gap-8 rounded-block border px-14 py-12',
        b.selected
          ? 'border-accent-line-2 bg-accent-bg-2 shadow-ring-accent'
          : 'border-line-2 bg-card',
      )}
    >
      <div className="flex flex-wrap items-center gap-8">
        <Mono className="text-mini text-ink-4">{b.id}</Mono>
        <span
          className={cx(
            'rounded-tag px-7 py-1 text-mini',
            b.layerPresent ? 'bg-inset text-ink-3' : 'bg-recall-bg text-recall',
          )}
        >
          {b.layer}
        </span>
        <span className="text-tag text-ink-3">{b.place}</span>
        {/* 定位失败是阻断级的事实，不能和「已定位」共用一种视觉 */}
        <span
          className="rounded-tag px-7 py-1 text-mini"
          style={
            b.located
              ? { color: FRESH.fresh.color, background: FRESH.fresh.bg }
              : { color: SEV.hard.color, background: SEV.hard.bg }
          }
        >
          {b.located ? '已定位' : '定位失败'}
        </span>
        <Mono className="ml-auto text-micro text-ink-5">{b.span}</Mono>
      </div>

      <div className="flex flex-wrap gap-6">
        {b.present.map((p, i) => (
          <span
            key={i}
            className={cx(
              'rounded-[11px] border px-8 py-2 text-tag',
              p.resolved && 'border-line-2 bg-inset text-ink-2',
            )}
            style={
              p.resolved
                ? undefined
                : {
                    background: SEV.unver.bg,
                    color: SEV.unver.color,
                    borderColor: SEV.unver.border,
                    borderStyle: 'dashed',
                  }
            }
            title={p.resolved ? undefined : '这个称呼没解析到人物 —— 查不了，不是「没问题」'}
          >
            {p.name}
          </span>
        ))}
      </div>

      <div className="prose-cn text-section text-ink-2">{b.event}</div>

      {b.hinges.map((h, i) => (
        <div key={i} className="rounded-r-btn border-l-3 border-l-hinge bg-hinge-bg px-12 py-8">
          <div className="text-micro text-hook-ink">必须保真的点 · {h.kind}</div>
          <div className="prose-cn text-sm2 text-ink-2">{h.text}</div>
        </div>
      ))}

      <Mono className="text-mini text-ink-5">原文区间 {b.span}</Mono>
    </div>
  )
}
