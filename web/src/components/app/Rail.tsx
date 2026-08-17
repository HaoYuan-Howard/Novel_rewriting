import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { RAIL, type ScreenName } from '@/constants/vocabulary'
import { RAIL_GATE, RAIL_LOCK, gateNeedText } from '@/constants/gates'
import { useApprovals } from '@/hooks/useApprovals'
import { useSampleCoverage } from '@/hooks/useSample'
import { cx } from '@/lib/cx'

const SCREEN_STAGE: Partial<Record<ScreenName, string>> = {
  stage0: 'stage0',
  stage1: 'stage1',
  read: 'stage2',
  global: 'stage2',
  style: 'stage3',
  stage4: 'stage4',
  depth: 'stage4',
  stage5: 'stage5',
  stage6: 'stage6',
  stage7: 'stage7',
}

/**
 * 左侧常驻阶段导航。
 *
 * 已确认的屏带 ✓，上游没过的屏带「锁」并**真的点不动**，还要说清缺什么。
 * 「导航上写着锁、屏上却能点」是评审揪出来的真实缺陷。
 */
export function Rail({
  sid,
  screen,
  ordinal,
}: {
  sid: string | null
  screen: ScreenName | null
  ordinal: number | null
}) {
  const nav = useNavigate()
  const { doc } = useApprovals(sid ?? '')
  const { defaultOrdinal } = useSampleCoverage()
  const overview = useQuery({
    queryKey: qk.novel(sid ?? ''),
    queryFn: () => api.getNovel(sid!),
    enabled: !!sid,
    retry: false,
  })
  // 与待办屏同一条 queryKey：走 Query 缓存，不新开请求路径
  const inbox = useQuery({
    queryKey: qk.inbox(sid ?? ''),
    queryFn: () => api.getInbox(sid!),
    enabled: !!sid,
    retry: false,
  })
  const inboxTotal = inbox.data?.total ?? 0

  const inBook = !!sid

  return (
    <nav className="sticky top-0 flex h-screen flex-col overflow-y-auto border-r border-line bg-inset">
      <div className="flex flex-col gap-4 px-16 pt-18 pb-14">
        <div className="text-read font-semibold tracking-[0.02em]">小说重构 · 审校台</div>
        {inBook && overview.data?.title ? (
          <div className="truncate text-body text-ink-3" title={overview.data.title}>
            {overview.data.title}
          </div>
        ) : null}
        <div className="font-mono text-mini text-ink-4">127.0.0.1:8765 · 单人 · 本机</div>
      </div>

      <div className="flex flex-col gap-2 px-10">
        <button
          type="button"
          onClick={() => nav('/')}
          className={cx(
            'flex cursor-pointer items-center gap-8 rounded-btn border-none px-12 py-9 text-left text-body-lg font-semibold',
            screen === 'corpus' ? 'bg-card shadow-current' : 'bg-transparent',
          )}
        >
          <span
            aria-hidden
            className={cx(
              'h-16 w-5 rounded-nub',
              screen === 'corpus' ? 'bg-accent' : 'bg-transparent',
            )}
          />
          语料库
        </button>
      </div>

      {inBook ? (
        <div className="mt-14 flex flex-col gap-2 px-10 pb-24">
          <div className="px-12 pt-6 pb-8 text-micro tracking-[0.08em] text-ink-5">流水线</div>
          {/* 进度是观察屏：不进 RAIL，不吃 RAIL_GATE / RAIL_LOCK，跑到哪一步都能看 */}
          <button
            type="button"
            aria-current={screen === 'run' ? 'page' : undefined}
            onClick={() => nav('/n/' + sid + '/run')}
            className={cx(
              'flex cursor-pointer items-center gap-7 rounded-btn border-none px-10 py-7 text-left text-body',
              screen === 'run'
                ? 'bg-card font-semibold text-ink shadow-current'
                : 'bg-transparent text-ink-3',
            )}
          >
            <span
              aria-hidden
              className={cx(
                'h-14 w-4 flex-none rounded-nub',
                screen === 'run' ? 'bg-accent' : 'bg-transparent',
              )}
            />
            <span className="w-12 flex-none font-mono text-mini text-ink-5">»</span>
            <span className="min-w-0 flex-1 truncate">进度</span>
          </button>
          {RAIL.map((r) => {
            const gk = RAIL_GATE[r.screen]
            const ok = !!(doc && gk && doc.gate[gk])
            const lock = RAIL_LOCK[r.screen]
            const stage = SCREEN_STAGE[r.screen]
            const stageDone = stage ? overview.data?.stage_done?.[stage] : undefined
            const hasArtifact = !!stage && overview.data?.stage_reviewable[stage] === true
            // 已有真实产物时允许只读审阅；继续运行和定稿仍由 GateBar 按审批记录拦住。
            const locked = !!(doc && lock && !doc.gate[lock.need] && !hasArtifact)
            const reviewable = hasArtifact && !ok
            const active = screen === r.screen
            const to =
              '/n/' +
              sid +
              '/' +
              r.screen +
              (r.chaptered ? '/' + (ordinal ?? defaultOrdinal(sid!)) : '')
            const btn = (
              <button
                key={r.screen}
                type="button"
                disabled={locked}
                title={
                  !locked && reviewable
                    ? '已有盘上产物，可只读审阅；继续运行仍需补齐人工确认'
                    : undefined
                }
                aria-current={active ? 'page' : undefined}
                onClick={() => nav(to)}
                className={cx(
                  'flex w-full items-center gap-7 rounded-btn border-none px-10 py-7 text-left text-body',
                  active ? 'bg-card font-semibold text-ink shadow-current' : 'bg-transparent text-ink-3',
                  locked ? 'cursor-not-allowed opacity-60' : 'cursor-pointer',
                )}
              >
                <span
                  aria-hidden
                  className={cx('h-14 w-4 flex-none rounded-nub', active ? 'bg-accent' : 'bg-transparent')}
                />
                <span className="w-12 flex-none font-mono text-mini text-ink-5">{r.n}</span>
                <span className="min-w-0 flex-1 truncate">{r.label}</span>
                {r.screen === 'inbox' && inboxTotal > 0 ? (
                  <span className="flex-none rounded-tag bg-accent-bg px-6 py-1 text-micro font-semibold text-accent-ink">
                    {inboxTotal}
                  </span>
                ) : null}
                <span
                  className={cx(
                    'flex-none whitespace-nowrap',
                    ok ? 'text-mini text-gate-mark' : 'text-micro text-ink-6',
                  )}
                >
                  {ok
                    ? '✓'
                    : locked
                      ? gateNeedText(lock!.need)
                      : reviewable
                        ? stageDone === false
                          ? '需处理'
                          : '待审'
                        : ''}
                </span>
              </button>
            )
            // Chrome 对 disabled 按钮不触发鼠标事件：锁定时 title 由外层 span 承载
            return locked ? (
              <span key={r.screen} title={lock!.why} className="block">
                {btn}
              </span>
            ) : (
              btn
            )
          })}
        </div>
      ) : (
        <div className="prose-cn mt-14 px-16 text-micro text-ink-5">
          还没打开哪一本书。流水线导航要进到一本书里才有。
        </div>
      )}

      <div className="mt-auto px-16 pt-10 pb-14 text-micro text-ink-5">←/→ 翻章 · Esc 关闭</div>
    </nav>
  )
}
