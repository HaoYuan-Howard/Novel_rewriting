import { useNavigate } from 'react-router-dom'
import { useQueries, useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { GATE, SEV, chNo, type InboxKind } from '@/constants/vocabulary'
import type { CheckKind, CheckReport } from '@/types/stages'
import { useSid } from '@/hooks/useRouteParams'
import { useSampleCoverage } from '@/hooks/useSample'
import { QueryStates } from '@/components/state/QueryStates'
import { EmptyIsGood } from '@/components/state/States'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { CheckBadge, CheckMark } from '@/components/vocab/Badges'
import { isNoSample } from '@/api/errors'

/** 七层校验器分别落在哪一屏 —— 待办只路由，不提供编辑 */
const LAYERS: { kind: CheckKind; where: string; screen: string; chaptered?: boolean }[] = [
  { kind: 'stage0', where: '剔除审阅', screen: 'stage0' },
  { kind: 'stage1', where: '分章报告', screen: 'stage1' },
  { kind: 'stage2', where: '全局设定', screen: 'global' },
  { kind: 'stage4', where: '计划屏', screen: 'stage4', chaptered: true },
  { kind: 'stage5', where: '成稿屏', screen: 'stage5', chaptered: true },
  { kind: 'stage6', where: '文案屏', screen: 'stage6' },
  { kind: 'stage7', where: '人名屏', screen: 'stage7' },
]

const KIND_META: Record<
  InboxKind,
  { title: string; meaning: string; color: string; bg: string; border: string }
> = {
  invalid: {
    title: '被落盘闸门拦下的稿',
    meaning: '没有覆盖现役产物，在等人改',
    ...SEV.hard,
  },
  rejected: { title: '自救被回归护栏驳回', meaning: '钱花了，稿丢了', ...SEV.hard },
  stalled: {
    title: '连续多轮未消',
    meaning: '判「根因不在写手」，修复已停手',
    ...SEV.unver,
  },
  repairable: {
    title: '机器可执行的修复',
    meaning: '走确定性或本地修复路由，不冒充人工项',
    ...SEV.info,
  },
  human_only: { title: '真正需要人工修复', meaning: '没有机器修复路由，或人裁决不改', ...SEV.info },
  verification: {
    title: '人工复核未闭合',
    meaning: 'Stage2 / Stage5 / Stage6 的 pending、FAIL 或 UNVER 回到所属阶段补决定与证据',
    ...SEV.unver,
  },
  upstream: { title: '阻断级，重写修不掉', meaning: '根因在上游', ...SEV.hard },
  approval: {
    title: '「人定稿」闸门',
    meaning: '按设计就该人做的一步，不是故障',
    color: GATE.ok.chipColor,
    bg: GATE.ok.chipBg,
    border: GATE.okLine,
  },
}

const KINDS = Object.keys(KIND_META) as InboxKind[]

/**
 * 待办。
 *
 * 判据与校验结论**下沉到各自的阶段屏**，改也只在阶段屏发生。
 * 这一屏回答阶段屏答不上的问题：这本书现在总共卡着几件事、坏在哪一层。
 * **它是入口，不是编辑面** —— 这里不放任何编辑控件，否则同一条问题会有两个能改的地方。
 */
export function InboxScreen() {
  const sid = useSid()
  const nav = useNavigate()
  const { defaultOrdinal } = useSampleCoverage()

  const q = useQuery({ queryKey: qk.inbox(sid), queryFn: () => api.getInbox(sid), retry: false })
  const overview = useQuery({
    queryKey: qk.novel(sid),
    queryFn: () => api.getNovel(sid),
    retry: false,
  })

  const checks = useQueries({
    queries: LAYERS.map((l) => ({
      queryKey: qk.check(sid, l.kind),
      queryFn: () => api.getCheck(sid, l.kind),
      enabled: overview.data?.stage_reviewable[l.kind] === true,
      retry: false,
    })),
  })

  return (
    <section className="flex flex-col gap-16 px-30 py-24">
      <header className="flex items-end justify-between gap-24">
        <div className="flex flex-col gap-5">
          <h1 className="m-0 text-screen font-semibold">待办</h1>
          <div className="prose-cn max-w-[52em] text-body text-ink-3">
            这本书现在总共卡着几件事、坏在哪一层。改在各自的阶段屏里发生——这一屏只带你过去。
          </div>
        </div>
      </header>

      {/* 七层校验器状态 */}
      <Panel>
        <PaneHeader title="七层校验器" meta="点一层跳到它的阶段屏" />
        <div className="grid gap-1 bg-line-2" style={{ gridTemplateColumns: 'repeat(7,1fr)' }}>
          {LAYERS.map((l, i) => {
            const r = checks[i]
            const reviewable = overview.data?.stage_reviewable[l.kind]
            const rep = (
              reviewable === true && !r?.isError ? r?.data ?? null : null
            ) as CheckReport | null
            const noSample = reviewable === true && !!r?.isError && isNoSample(r.error)
            return (
              <button
                key={l.kind}
                type="button"
                onClick={() =>
                  nav(
                    '/n/' +
                      sid +
                      '/' +
                      l.screen +
                      (l.chaptered ? '/' + defaultOrdinal(sid) : ''),
                  )
                }
                className="flex cursor-pointer flex-col items-start gap-5 border-none bg-card px-12 py-11 text-left"
              >
                <div className="flex items-center gap-7">
                  <CheckMark badge={rep ? rep.badge : null} />
                  <Mono className="text-mini">{l.kind}</Mono>
                </div>
                <CheckBadge badge={rep ? rep.badge : null} />
                <Mono className="text-micro text-ink-5">
                  {reviewable === undefined
                    ? overview.isPending ? '读取阶段状态…' : '阶段状态未知'
                    : reviewable === false
                      ? '尚不可检查'
                      : r?.isPending
                        ? '读取中…'
                        : rep
                          ? 'hard ' +
                            rep.rules.filter(
                              (x) => x.sev === 'hard' && x.status === 'hit',
                            ).length +
                            ' · 查不了 ' +
                            (rep.counts.unver + rep.counts.skipped)
                          : noSample
                            ? '没抓样本'
                            : '还没跑'}
                </Mono>
                <span className="text-micro text-ink-4">→ {l.where}</span>
              </button>
            )
          })}
        </div>
      </Panel>

      <QueryStates q={q} loadingLabel="读待人工清单" missing={{ title: '待办' }}>
        {(d) =>
          d.items.length === 0 ? (
            <EmptyIsGood
              title="待人工是空的 —— 好了"
              body={
                '这本书现在没有需要人处理的条目。' +
                (d.wontfix ? '另有 ' + d.wontfix + ' 条被裁决为「不改」，它们不在这里。' : '')
              }
            />
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-10">
                <span className="text-section font-semibold">共 {d.total} 条待人工</span>
                <span className="text-tag text-ink-4">
                  尚待处理 {d.attention_counts.pending} · 已裁决保留 {d.attention_counts.reviewed_retained}
                </span>
                <span className="text-tag text-ink-4">
                  阻塞交付 {d.delivery_counts.blocking} · 仅披露 {d.delivery_counts.disclosure}
                </span>
                {d.wontfix ? (
                  <span className="text-tag text-ink-4">另有 {d.wontfix} 条裁决为「不改」</span>
                ) : null}
                <span className="ml-auto flex flex-wrap gap-6">
                  {KINDS.map((k) => (
                    <Mono
                      key={k}
                      className="rounded-tag border px-8 py-2 text-mini"
                      title={KIND_META[k].meaning}
                    >
                      <span
                        style={{
                          color: KIND_META[k].color,
                          background: KIND_META[k].bg,
                          borderColor: KIND_META[k].border,
                        }}
                      >
                        {k} {d.counts[k] ?? 0}
                      </span>
                    </Mono>
                  ))}
                </span>
              </div>

              {KINDS.filter((k) => d.items.some((i) => i.kind === k)).map((k) => {
                const mine = d.items.filter((i) => i.kind === k)
                const m = KIND_META[k]
                return (
                  <Panel key={k}>
                    <div
                      className="flex items-center gap-10 border-b px-18 py-11"
                      style={{ background: m.bg, borderColor: m.border }}
                    >
                      <Mono
                        className="rounded-tag border bg-card px-8 py-2 text-mini font-medium"
                        title={k}
                      >
                        <span style={{ color: m.color, borderColor: m.border }}>{k}</span>
                      </Mono>
                      <span className="text-body font-semibold">{m.title}</span>
                      <span className="text-tag text-ink-4">{m.meaning}</span>
                      <Mono className="ml-auto text-mini text-ink-4">{mine.length} 条</Mono>
                    </div>
                    {mine.map((i, n) => {
                      // 按条目自己的 stage 跳它的阶段屏；chapter=null（书级）不编假章号
                      const layer = LAYERS.find((l) => l.kind === i.stage)
                      return (
                      <button
                        key={n}
                        type="button"
                        onClick={() => {
                          const screen = layer?.screen ?? 'stage5'
                          const suffix = layer?.chaptered
                            ? '/' + (i.chapter ?? defaultOrdinal(sid))
                            : ''
                          nav('/n/' + sid + '/' + screen + suffix)
                        }}
                        className="flex w-full cursor-pointer flex-col gap-5 border-b border-line-row px-18 py-12 text-left"
                      >
                        <div className="flex flex-wrap items-center gap-8">
                          <Mono className="rounded-tag bg-inset px-7 py-1 text-mini">
                            {i.chapter ? chNo(i.chapter) : '全书'}
                          </Mono>
                          <Mono className="text-micro text-ink-5">{i.stage}</Mono>
                          <Mono className="rounded-tag bg-inset px-7 py-1 text-micro text-ink-4">
                            {i.attention === 'pending' ? '尚待处理' : '已裁决·记录保留'}
                          </Mono>
                          <Mono className="rounded-tag bg-inset px-7 py-1 text-micro text-ink-4">
                            {i.delivery_effect === 'blocking' ? '阻塞交付' : '仅披露'}
                          </Mono>
                          <span className="text-body font-semibold">{i.what}</span>
                          <Mono className="ml-auto text-micro text-ink-5">{i.rule}</Mono>
                          <span className="text-micro text-ink-4">
                            → {layer?.where ?? '成稿屏'}
                          </span>
                        </div>
                        <div className="prose-cn text-sm2 text-ink-2">{i.how}</div>
                      </button>
                      )
                    })}
                  </Panel>
                )
              })}
            </>
          )
        }
      </QueryStates>
    </section>
  )
}
