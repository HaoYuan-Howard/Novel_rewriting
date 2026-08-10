import { useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { isNoSample } from '@/api/errors'
import { lookupAction } from '@/constants/actions'
import { SEV, type ScreenName, type Severity } from '@/constants/vocabulary'
import type { ActionRef } from '@/types/approvals'
import type { CheckItem, CheckKind } from '@/types/stages'
import { useEnums } from '@/hooks/useEnums'
import { useAskJob } from '@/hooks/useChrome'
import { useSid } from '@/hooks/useRouteParams'
import { ActionButton } from '@/components/ui/ActionButton'
import { CheckBadge, CheckMark } from '@/components/vocab/Badges'
import { SevMark } from '@/components/vocab/Sev'
import { LoadingPane } from '@/components/state/States'

/** 屏 → 这一层的校验报告 + 重跑它的那条动作（0 成本，不调 LLM） */
const CHECK_OF: Partial<Record<ScreenName, { kind: CheckKind; action: ActionRef }>> = {
  stage1: {
    kind: 'stage1',
    action: { kind: 'check_partition', label: 'stage1 校验', extra: null },
  },
  read: {
    kind: 'stage2',
    action: { kind: 'check_blueprints', label: 'stage2 校验', extra: null },
  },
  global: {
    kind: 'stage2',
    action: { kind: 'check_blueprints', label: 'stage2 校验', extra: null },
  },
  style: {
    kind: 'stage3',
    action: { kind: 'n3_style', label: 'stage3 校验（复检已有产物）', extra: ['--check-only'] },
  },
  stage4: {
    kind: 'stage4',
    action: { kind: 'n4_plan', label: 'stage4 校验（复检已有计划）', extra: ['--check-only'] },
  },
  stage6: {
    kind: 'stage6',
    action: { kind: 'n8_hook', label: 'stage6 校验（复检已有文案）', extra: ['--check-only'] },
  },
  stage7: {
    kind: 'stage7',
    action: { kind: 'n9_rename', label: 'stage7 校验（复检已有映射）', extra: ['--check-only'] },
  },
}

const GROUPS: { key: 'hard' | 'unver' | 'info'; sev: Severity; title: string; sub: string }[] = [
  { key: 'hard', sev: 'hard', title: '阻断 hard', sub: '这一层没过，下游写了也白写' },
  { key: 'unver', sev: 'unver', title: '查不了 unver', sub: '不是「没问题」，是这条根本没验成' },
  { key: 'info', sev: 'info', title: '提示 info', sub: '要人看一眼' },
]

/**
 * 每屏的校验报告。
 *
 * 三件事必须做到：
 *   - 没有报告时说「没跑过」，配虚线圆点，**不是绿色**
 *   - unver 单独成组，绝不并进「通过」
 *   - amber 的解释里写明「查不了不等于没问题」
 */
export function CheckPanel({
  screen,
  onJumpChapter,
}: {
  screen: ScreenName
  onJumpChapter?: (n: number) => void
}) {
  const sid = useSid()
  const cfg = CHECK_OF[screen]
  const enums = useEnums()
  const ask = useAskJob()

  const q = useQuery({
    queryKey: qk.check(sid, cfg?.kind ?? 'stage1'),
    queryFn: () => api.getCheck(sid, cfg!.kind),
    enabled: !!cfg && !!sid,
    retry: false,
  })

  if (!cfg) return null

  const d = q.data ?? null
  const noSample = q.isError && isNoSample(q.error)
  const action = lookupAction(enums.data, cfg.action)

  const rerun = (
    <ActionButton
      action={action}
      size="sm"
      onClick={() => ask({ ...cfg.action, sid, ordinal: null })}
    >
      重跑这一层校验
    </ActionButton>
  )

  return (
    <div className="mx-30 mt-20 overflow-hidden rounded-card border border-line bg-card">
      {q.isPending ? (
        <LoadingPane label={cfg.kind + ' 校验报告'} rows={2} />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-12 px-20 py-14">
            <CheckMark badge={d ? d.badge : null} />
            <span className="text-section font-semibold">{cfg.kind} 校验</span>
            <CheckBadge badge={d ? d.badge : null} />
            {d ? (
              <span className="font-mono text-mini text-ink-4">
                hard {d.hard_count} · 查不了 {d.unver_count} · 查过没问题 {d.oks.length}
              </span>
            ) : null}
            <span className="ml-auto">{rerun}</span>
          </div>

          <div className="prose-cn border-t border-line-row px-20 py-11 text-sm2 text-ink-2">
            {d
              ? d.badge === 'amber'
                ? `通过了，但有 ${d.unver_count} 项查不了——查不了不等于没问题。`
                : d.badge === 'fail'
                  ? '这一层判定不通过，先在这屏改了再往下走。'
                  : '这一层的判据全部验过且通过。'
              : noSample
                ? '这一层的校验报告不在样本里。真实环境里跑一次就有——0 成本、不调 LLM。样本包只抓了 stage2 与 stage5_review 两份。'
                : '这一层还没有结论——空白不代表通过。'}
          </div>

          {d
            ? GROUPS.map((g) => {
                const items: (CheckItem | { item: string; detail: string })[] =
                  g.key === 'unver' ? d.unver : g.key === 'hard' ? d.hard : d.info
                if (!items.length) return null
                return (
                  <div key={g.key} className="border-t border-line-row">
                    <div className="flex items-center gap-10 bg-sunken-2 px-20 py-10">
                      <SevMark sev={g.sev} />
                      <span className="text-body font-semibold">{g.title}</span>
                      <span className="text-tag text-ink-4">{g.sub}</span>
                      <span className="ml-auto font-mono text-mini text-ink-4">
                        {items.length} 条
                      </span>
                    </div>
                    {items.map((it, i) => {
                      const chapters = 'chapters' in it ? (it.chapters ?? []) : []
                      const fix = 'fix' in it ? it.fix : null
                      return (
                        <div
                          key={i}
                          className="flex flex-col gap-5 border-t border-line-row px-20 py-11"
                        >
                          <div className="flex flex-wrap items-baseline gap-9">
                            <span className="text-body font-semibold">{it.item}</span>
                            {chapters.map((n) => (
                              <button
                                key={n}
                                type="button"
                                onClick={() => onJumpChapter?.(n)}
                                className="cursor-pointer rounded-tag border border-line bg-card px-7 py-1 font-mono text-mini text-ink-3"
                              >
                                ch{n}
                              </button>
                            ))}
                            {/* 没有 fix 字段的结论，界面就不该画「重跑修它」按钮 */}
                            {fix ? (
                              <ActionButton
                                action={fix}
                                size="sm"
                                onClick={() =>
                                  ask({
                                    kind: fix.kind,
                                    label: '修：' + it.item,
                                    extra: fix.extra,
                                    sid,
                                    ordinal: null,
                                  })
                                }
                              >
                                重跑修它
                              </ActionButton>
                            ) : null}
                          </div>
                          <div className="prose-cn text-sm2 text-ink-2">{it.detail}</div>
                        </div>
                      )
                    })}
                  </div>
                )
              })
            : null}

          {d && d.oks.length ? (
            <details className="border-t border-line-row">
              <summary className="cursor-pointer bg-sunken-2 px-20 py-10 text-body text-ink-3">
                查过、没问题的 {d.oks.length} 条
                <span className="ml-8 text-tag text-ink-4">
                  —— 列出来是为了让人知道「这一层到底查了什么」
                </span>
              </summary>
              <ul className="m-0 flex list-none flex-col gap-6 px-20 py-12">
                {d.oks.map((s, i) => (
                  <li key={i} className="flex items-baseline gap-8 text-sm2 text-ink-3">
                    <span
                      aria-hidden
                      className="inline-block h-6 w-6 flex-none rounded-full"
                      style={{ background: SEV.metric.border }}
                    />
                    {s}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      )}
    </div>
  )
}
