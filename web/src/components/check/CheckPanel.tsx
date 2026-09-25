import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import { isNoSample } from '@/api/errors'
import { SEV, type ScreenName, type Severity } from '@/constants/vocabulary'
import type { Finding, RuleLine } from '@/types/common'
import type { CheckKind } from '@/types/stages'
import type { EnumsResponse } from '@/types/enums'
import { useAskJob } from '@/hooks/useChrome'
import { useEnums } from '@/hooks/useEnums'
import { useSid } from '@/hooks/useRouteParams'
import { lookupAction } from '@/constants/actions'
import { CheckBadge, CheckMark } from '@/components/vocab/Badges'
import { SevMark } from '@/components/vocab/Sev'
import { LoadingPane } from '@/components/state/States'
import { cx } from '@/lib/cx'
import type { ReactNode } from 'react'

/**
 * 屏 → 这一层的校验 kind。六个 kind 与服务端 `STAGES` 逐值相等（FE-02）。
 * 「重跑校验」不再是一条动作：真后端的 `GET /check/:kind` 本身就是 0 成本现算复检，
 * 按钮 = refetch。
 */
const CHECK_OF: Partial<Record<ScreenName, CheckKind>> = {
  stage0: 'stage0',
  stage1: 'stage1',
  read: 'stage2',
  global: 'stage2',
  stage4: 'stage4',
  stage5: 'stage5',
  stage7: 'stage7',
}

/** `remediation` → 人话标签（处置路由表在 docs/BACKEND_CONTRACT.md §七，这里只是呈现）。 */
const REMEDIATION_LABEL: Record<RuleLine['remediation'], string> = {
  deterministic: '确定性可修',
  llm: '可付费自救（≤2 轮）',
  human: '待人工',
  none: '读数',
}

function TwoAxisChips({ line }: { line: RuleLine }) {
  return (
    <span className="flex flex-wrap items-baseline gap-6">
      <span className="rounded-tag border border-line bg-sunken-2 px-6 py-1 text-mini text-ink-4">
        {line.check_kind === 'semantic' ? '语义' : '机械'}
      </span>
      {line.status === 'hit' && line.remediation !== 'none' ? (
        <span className="rounded-tag border border-line bg-sunken-2 px-6 py-1 text-mini text-ink-3">
          {REMEDIATION_LABEL[line.remediation]}
        </span>
      ) : null}
    </span>
  )
}

function FindingRow({
  f,
  onJumpChapter,
}: {
  f: Finding
  onJumpChapter?: (n: number) => void
}) {
  const ordinal = f.output_ordinal
  return (
    <div className="flex flex-col gap-4 border-t border-line-row px-20 py-9">
      <div className="flex flex-wrap items-baseline gap-9">
        <span className="text-sm2 font-semibold">{f.item}</span>
        {ordinal != null ? (
          <button
            type="button"
            disabled={!onJumpChapter}
            onClick={() => onJumpChapter?.(ordinal)}
            className="rounded-tag border border-line bg-card px-6 py-1 font-mono text-mini text-ink-4 disabled:cursor-default"
          >
            第 {ordinal} 章
          </button>
        ) : null}
        {f.wontfix ? (
          <span className="rounded-tag border border-line bg-sunken-2 px-6 py-1 text-mini text-ink-4">
            已豁免：{f.wontfix.why}
          </span>
        ) : null}
        {f.unresolved_rounds ? (
          <span className="font-mono text-mini text-ink-4">已试 {f.unresolved_rounds} 轮未消</span>
        ) : null}
        {f.owner ? (
          <span className="font-mono text-mini text-ink-4">根因第一嫌疑：{f.owner}</span>
        ) : null}
        {f.identity_hit ? (
          <span className="rounded-tag border border-warn-line bg-warn-bg px-6 py-1 text-mini text-warn-ink">
            identity_hit · 恒等基线也命中
          </span>
        ) : null}
        {f.identity_dead ? (
          <span className="rounded-tag border border-warn-line bg-warn-bg px-6 py-1 text-mini text-warn-ink">
            identity_dead · 判据失去判别力
          </span>
        ) : null}
      </div>
      <div className="prose-cn text-sm2 whitespace-pre-wrap text-ink-2">{f.detail}</div>
    </div>
  )
}

function RuleBlock({
  line,
  sid,
  allowRerun,
  enums,
  onJumpChapter,
}: {
  line: RuleLine
  sid: string
  allowRerun: boolean
  enums: EnumsResponse | undefined
  onJumpChapter?: (n: number) => void
}) {
  const ask = useAskJob()
  const fix = line.fix
  const action = fix
    ? lookupAction(enums, { kind: fix.node, extra: fix.flags })
    : null
  return (
    <div className="border-t border-line-row">
      <div className="flex flex-wrap items-center gap-10 px-20 py-9">
        <SevMark sev={line.sev} />
        <span className="text-body font-semibold">{line.item}</span>
        <span className="font-mono text-mini text-ink-4">{line.rule}</span>
        <TwoAxisChips line={line} />
        {/* 「重跑修它」只画在服务端下发了 fix 的行上 —— 前端不拼 argv（R-11.8） */}
        {allowRerun && line.status === 'hit' && fix && fix.available !== false ? (
          <button
            type="button"
            className="cursor-pointer rounded-tag border border-line bg-card px-8 py-2 text-mini text-ink-2"
            onClick={() =>
              ask({
                kind: fix.node,
                label: '修：' + line.item,
                extra: fix.flags,
                required_inputs: action?.required_inputs,
                sid,
                ordinal: null,
              })
            }
          >
            {fix.paid ? '付费重跑修它' : '重跑修它（0 成本）'}
          </button>
        ) : null}
        {allowRerun && line.status === 'hit' && fix && fix.available === false ? (
          <span className="rounded-tag border border-warn-line bg-warn-bg px-8 py-2 text-mini text-warn-ink">
            不能自动重跑：{fix.unavailable_reason || '服务端没有给出可执行参数'}
          </span>
        ) : null}
        <span className="ml-auto font-mono text-mini text-ink-4">
          {line.status === 'hit' ? line.findings.length + ' 条' : null}
        </span>
      </div>
      {line.status === 'hit'
        ? line.findings.map((f, i) => <FindingRow key={i} f={f} onJumpChapter={onJumpChapter} />)
        : null}
      {line.note ? (
        <div className="prose-cn border-t border-line-row bg-sunken-2 px-20 py-9 text-sm2 whitespace-pre-wrap text-ink-3">
          {line.note}
        </div>
      ) : null}
      {line.status === 'unver' || line.status === 'skipped' ? (
        <div className="prose-cn border-t border-line-row px-20 py-9 text-sm2 text-ink-3">
          {line.why ?? '这一条这次没验成——不是「没问题」。'}
        </div>
      ) : null}
    </div>
  )
}

/** 分组顺序：命中（hard 前 info 后）→ 查不了/没跑 → 度量读数 → 通过（收起）。 */
function groupRules(rules: RuleLine[]) {
  const sevRank: Record<Severity, number> = { hard: 0, info: 1, metric: 2, unver: 3 }
  const hits = rules
    .filter((r) => r.status === 'hit' && r.sev !== 'metric')
    .sort((a, b) => sevRank[a.sev] - sevRank[b.sev])
  const unver = rules.filter((r) => r.status === 'unver' || r.status === 'skipped')
  const metrics = rules.filter((r) => r.status === 'hit' && r.sev === 'metric')
  const oks = rules.filter((r) => r.status === 'ok')
  return { hits, unver, metrics, oks }
}

/**
 * 每屏的校验报告。按 `rules[]` 渲染（FE-04），两轴 chip 随行（check_kind × remediation）。
 *
 * 三件事必须做到：
 *   - 没有报告时说「没跑过」，配虚线圆点，**不是绿色**
 *   - unver/skipped 单独成组，绝不并进「通过」
 *   - amber 的解释里写明「查不了不等于没问题」
 */
export function CheckPanel({
  screen,
  onJumpChapter,
  allowRerun = true,
  embedded = false,
  headerAction,
}: {
  screen: ScreenName
  onJumpChapter?: (n: number) => void
  allowRerun?: boolean
  embedded?: boolean
  headerAction?: ReactNode
}) {
  const sid = useSid()
  const kind = CHECK_OF[screen]
  const qc = useQueryClient()
  const enums = useEnums()

  const q = useQuery({
    queryKey: qk.check(sid, kind ?? 'stage1'),
    queryFn: () => api.getCheck(sid, kind!),
    enabled: !!kind && !!sid,
    retry: false,
  })

  if (!kind) return null

  const d = q.data ?? null
  const noSample = q.isError && isNoSample(q.error)
  const groups = d ? groupRules(d.rules) : null

  const rerun = allowRerun ? (
    <button
      type="button"
      disabled={q.isFetching}
      className="cursor-pointer rounded-tag border border-line bg-card px-8 py-3 text-mini text-ink-2 disabled:cursor-not-allowed disabled:opacity-40"
      onClick={() => void qc.invalidateQueries({ queryKey: qk.check(sid, kind) })}
    >
      {q.isFetching ? '复检中…' : '重新复检（0 成本现算）'}
    </button>
  ) : null

  return (
    <div
      className={cx(
        'overflow-hidden bg-card',
        !embedded && 'mx-30 mt-20 rounded-card border border-line',
      )}
    >
      {q.isPending ? (
        <LoadingPane label={'读 ' + kind + ' 校验报告'} rows={2} />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-12 px-20 py-14">
            <CheckMark badge={d ? d.badge : null} />
            <span className="text-section font-semibold">{kind} 校验</span>
            <CheckBadge badge={d ? d.badge : null} />
            {d ? (
              <span className="font-mono text-mini text-ink-4">
                命中 {d.counts.hit} · 查不了 {d.counts.unver}
                {d.counts.skipped ? ` · 没跑成 ${d.counts.skipped}` : ''} · 查过没问题{' '}
                {d.counts.ok}
              </span>
            ) : null}
            <span className="ml-auto flex items-center gap-8">
              {rerun}
              {headerAction}
            </span>
          </div>

          {d?.provenance.reason ? (
            <div className="prose-cn border-t border-warn-line bg-warn-bg px-20 py-11 text-sm2 text-warn-ink">
              {d.provenance.freshness === 'stale'
                ? '报告已过期：'
                : d.provenance.freshness === 'unknown'
                  ? '旧产物 / 新鲜度未知：'
                  : '报告来源说明：'}
              {d.provenance.reason}
            </div>
          ) : null}

          {d?.badge === 'amber' ? (
            <div className="prose-cn border-t border-line-row px-20 py-11 text-sm2 text-ink-2">
              通过了，但有 {d.counts.unver + d.counts.skipped} 项查不了——查不了不等于没问题。
            </div>
          ) : d?.badge === 'fail' ? (
            <div className="prose-cn border-t border-line-row px-20 py-11 text-sm2 text-ink-2">
              这一层判定不通过，先在这屏改了再往下走。
            </div>
          ) : !d ? (
            <div className="prose-cn border-t border-line-row px-20 py-11 text-sm2 text-ink-2">
              {noSample
                ? '这本书不在样本里——mock 模式只有样本那一本。'
                : '这一层还没有结论——空白不代表通过。'}
            </div>
          ) : null}

          {groups && groups.hits.length ? (
            <div>
              <div className="flex items-center gap-10 border-t border-line-row bg-sunken-2 px-20 py-10">
                <span className="text-body font-semibold">命中</span>
                <span className="text-tag text-ink-4">按严重度排：阻断在前，提示在后</span>
                <span className="ml-auto font-mono text-mini text-ink-4">
                  {groups.hits.length} 条判据
                </span>
              </div>
              {groups.hits.map((line) => (
                <RuleBlock
                  key={line.rule}
                  line={line}
                  sid={sid}
                  allowRerun={allowRerun}
                  enums={enums.data}
                  onJumpChapter={onJumpChapter}
                />
              ))}
            </div>
          ) : null}

          {groups && groups.unver.length ? (
            <div>
              <div className="flex items-center gap-10 border-t border-line-row bg-sunken-2 px-20 py-10">
                <SevMark sev="unver" />
                <span className="text-body font-semibold">查不了 / 没跑成</span>
                <span className="text-tag text-ink-4">不是「没问题」，是这几条根本没验成</span>
                <span className="ml-auto font-mono text-mini text-ink-4">
                  {groups.unver.length} 条
                </span>
              </div>
              {groups.unver.map((line) => (
                <RuleBlock
                  key={line.rule}
                  line={line}
                  sid={sid}
                  allowRerun={allowRerun}
                  enums={enums.data}
                  onJumpChapter={onJumpChapter}
                />
              ))}
            </div>
          ) : null}

          {groups && groups.metrics.length ? (
            <details className="border-t border-line-row">
              <summary className="cursor-pointer bg-sunken-2 px-20 py-10 text-body text-ink-3">
                度量读数 {groups.metrics.length} 条
                <span className="ml-8 text-tag text-ink-4">—— 事实不是结论，永不告警色</span>
              </summary>
              {groups.metrics.map((line) => (
                <RuleBlock
                  key={line.rule}
                  line={line}
                  sid={sid}
                  allowRerun={false}
                  enums={enums.data}
                  onJumpChapter={onJumpChapter}
                />
              ))}
            </details>
          ) : null}

          {groups && groups.oks.length ? (
            <details className="border-t border-line-row">
              <summary className="cursor-pointer bg-sunken-2 px-20 py-10 text-body text-ink-3">
                查过、没问题的 {groups.oks.length} 条
              </summary>
              <ul className="m-0 flex list-none flex-col gap-6 px-20 py-12">
                {groups.oks.map((line) => (
                  <li key={line.rule} className="flex items-baseline gap-8 text-sm2 text-ink-3">
                    <span
                      aria-hidden
                      className="inline-block h-6 w-6 flex-none rounded-full"
                      style={{ background: SEV.metric.border }}
                    />
                    <span>{line.item}</span>
                    {line.scanned ? (
                      <span className="font-mono text-mini text-ink-4">扫过：{line.scanned}</span>
                    ) : null}
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
