import { DELIVERY_LABEL } from '@/constants/vocabulary'
import { RunMetricsPanel } from './RunMetricsPanel'
import { BlueprintProgressPanel } from './BlueprintProgressPanel'
import { useEffect, useId, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { keepPreviousData, useQueries, useQuery } from '@tanstack/react-query'
import { api } from '@/api/endpoints'
import { qk } from '@/api/queryKeys'
import {
  CHAPTERED_SCREENS,
  AGENT_STOPPED,
  FRESH,
  GATE,
  JOB,
  SPEND_INK,
  STAGE_SCREEN,
  STAGES,
  when,
  type JobStateToken,
  type ScreenName,
} from '@/constants/vocabulary'
import { errorBody } from '@/api/errors'
import { ErrorHint } from '@/components/state/States'
import type {
  RunBeacon,
  RunEvent,
  RunNextHandler,
  RunResumeFrontier,
} from '@/types/run'
import { CHECK_KINDS, type CheckReport } from '@/types/stages'
import { useRunProgress } from '@/hooks/useRunProgress'
import { WorkUnitBudgetGrant } from '@/components/agent/WorkUnitBudgetGrant'
import { useSid } from '@/hooks/useRouteParams'
import {
  useAgentRunLog,
  useAgentRuns,
  usePauseAgentRun,
  useResumeAgentRun,
  useStopAgentRun,
} from '@/hooks/useAgentRuns'
import type { AgentRunState } from '@/types/agent'
import { useSampleCoverage } from '@/hooks/useSample'
import { QueryStates } from '@/components/state/QueryStates'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { LinkButton } from '@/components/ui/ActionButton'
import { AgentProfilesSnapshot } from '@/components/agent/AgentProfilesSnapshot'
import {
  AgentStoppedChip,
  CheckBadge,
  GateChip,
  JobStateChip,
} from '@/components/vocab/Badges'
import { cx } from '@/lib/cx'

/**
 * 信标五态借用 JOB 的色与形：
 * pending 借 unknown 的中性灰（空心虚线圆）——它只表示没有这条节点的信标记录，
 * 不推断「还没跑到」，也不与阶段完成度互相改写；
 * running / ok / landed_red / failed 直接用 JOB 自己的文案。
 * ok 念作「跑完了」：它只表示那条命令 exit 0，**绝不渲染成「合格 / 通过」**——
 * 判定只归各层屏的 badge（信标是观察轨迹，不是判据）。
 */
const BEACON: Record<RunBeacon, { token: JobStateToken; label: string }> = {
  pending: { token: JOB.unknown, label: '无运行记录' },
  running: { token: JOB.running, label: JOB.running.label },
  ok: { token: JOB.ok, label: JOB.ok.label },
  landed_red: { token: JOB.landed_red, label: JOB.landed_red.label },
  stopped: { token: AGENT_STOPPED, label: AGENT_STOPPED.label },
  failed: { token: JOB.failed, label: JOB.failed.label },
}

/** 与 vocab/Badges.tsx 的 JobStateChip 同一个画法，只是标签归信标自己 */
function BeaconChip({ beacon }: { beacon: RunBeacon }) {
  const { token, label } = BEACON[beacon]
  const dot: React.CSSProperties = {
    display: 'inline-block',
    flex: 'none',
    width: 8,
    height: 8,
    boxSizing: 'border-box',
    ...(token.shape === 'circle'
      ? { borderRadius: '50%', background: token.color }
      : token.shape === 'square'
        ? { borderRadius: 1.5, background: token.color }
        : { borderRadius: '50%', border: '1.5px dashed ' + token.color }),
  }
  return (
    <span
      className="inline-flex items-center gap-7 whitespace-nowrap rounded-tag px-8 py-2 text-tag font-semibold"
      style={{ color: token.color, background: token.bg }}
    >
      <span aria-hidden style={dot} />
      {label}
    </span>
  )
}

/** amber 的未验证原因直接来自 CheckReport，不在前端重判。 */
function UnverifiedHelp({ report }: { report: CheckReport }) {
  const explanationId = useId()
  const [open, setOpen] = useState(false)
  const rows = report.rules.filter(
    (row) => row.status === 'unver' || row.status === 'skipped',
  )
  if (rows.length === 0) return null
  const explanation = rows
    .map((row) => `${row.item}：${row.why ?? row.note ?? '本次没有完成验证'}`)
    .join('\n')
  return (
    <span className="inline-flex flex-col items-start">
      <button
        type="button"
        aria-label={`查看 ${rows.length} 项未验证原因`}
        aria-expanded={open}
        aria-controls={explanationId}
        title={explanation}
        onClick={() => setOpen((value) => !value)}
        className="inline-flex h-18 w-18 cursor-help items-center justify-center rounded-full border border-line-btn bg-card text-micro font-semibold text-ink-4"
      >
        ?
      </button>
      {open ? (
        <span
          id={explanationId}
          role="note"
          className="mt-6 block w-[220px] rounded-block border border-line-btn bg-card px-10 py-8 text-left text-tag leading-relaxed whitespace-pre-line text-ink-2"
        >
          {explanation}
        </span>
      ) : null}
    </span>
  )
}

/** 新记录是布尔；历史事件未采集正文外发授权时必须显示未知，不能倒成已授权。 */
function AuthorizationChip({ value, label }: { value: boolean | null; label: string }) {
  if (value !== null) return <GateChip ok={value} label={label} />
  return (
    <span className="whitespace-nowrap rounded-chip border border-line px-10 py-4 text-tag text-ink-4">
      ? {label} · 历史未记录
    </span>
  )
}

/** Stage6 改选后的重建排队 / 成书对齐状态。 */
function Stage6RebuildPanel({ sid }: { sid: string }) {
  const q = useQuery({
    queryKey: qk.stage6Rebuild(sid),
    queryFn: () => api.getStage6Rebuild(sid),
    retry: false,
    refetchInterval: (query) => (query.state.data?.pending ? 4000 : false),
  })
  return (
    <Panel>
      <PaneHeader title="Stage6 改选重建" meta="后端 /stage6/rebuild" />
      <div data-testid="stage6-rebuild" className="flex flex-col gap-8 px-18 py-14">
        {q.isPending ? (
          <div className="text-body text-ink-5">读取重建状态…</div>
        ) : q.isError ? (
          <div className="text-body text-warn-ink">
            取不到重建状态：{errorBody(q.error)}
            <ErrorHint error={q.error} />
          </div>
        ) : (
          <>
            {q.data.pending ? (
              <span className="rounded-tag border border-warn-line bg-warn-bg px-8 py-3 text-tag font-semibold text-warn-ink">
                重建排队中
              </span>
            ) : (
              <span className="rounded-tag border border-gate-ok-line bg-gate-ok-bg px-8 py-3 text-tag font-semibold text-gate-ok-ink">
                无待办重建
              </span>
            )}
            <div className="prose-cn text-body text-ink-3">
              {q.data.pending && !q.data.published
                ? '当前下载对应旧版本；新选择正在生成。'
                : q.data.published
                  ? '已对齐当前选择。'
                  : '成书尚未对齐当前选择。'}
            </div>
            <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-14 gap-y-4 text-tag text-ink-4">
              <dt>排队深度</dt>
              <dd className="m-0" data-testid="rebuild-queue-depth">
                {q.data.queue_depth ?? (q.data.pending ? '≥1' : 0)}
              </dd>
              <dt>期望版本</dt>
              <dd className="m-0" data-testid="rebuild-expected-version">
                {q.data.expected_slot
                  ? `${q.data.expected_slot} · ${q.data.expected_approved_text_sha1 ?? '?'}`
                  : q.data.published
                    ? '与盘面当前选择一致'
                    : '未知（尚无排队意图指纹）'}
              </dd>
              <dt>已发布</dt>
              <dd className="m-0" data-testid="rebuild-published-version">
                {q.data.published ? '是（成书 == 当前期望）' : '否（下载仍可能是旧成书）'}
              </dd>
            </dl>
          </>
        )}
      </div>
    </Panel>
  )
}



/**
 * 交付终验徽标 + **可展开的判定依据**。
 *
 * ⚠ `reason` 曾经只挂在 `title` 上。`title` 只在鼠标悬停时出现：触摸与键盘用户
 * 一个字都读不到，而它是「为什么没交付、下一步做什么」的**唯一**出口
 * （后端那句话里带着 counts、缺的是哪一格、阻塞待办的样本）。
 * ⇒ 与 `UnverifiedHelp` 同一条呈现纪律：按钮可聚焦、`aria-expanded`、展开成正文。
 */
interface StoppedStageTarget {
  to: string
  label: string
}

function DeliveryChip({
  outcome,
  stoppedStageTarget,
}: {
  outcome: NonNullable<import('@/types/agent').AgentRunRecord['outcome']>
  stoppedStageTarget: StoppedStageTarget | null
}) {
  const ok = outcome.state === 'ready' || outcome.state === 'ready_with_disclosures'
  const explanationId = useId()
  const [open, setOpen] = useState(outcome.state === 'stopped')
  return (
    <span className="inline-flex flex-col items-start gap-4">
      <button
        type="button"
        aria-label={'查看交付终验依据：' + DELIVERY_LABEL[outcome.state]}
        aria-expanded={open}
        aria-controls={explanationId}
        title={outcome.reason}
        onClick={() => setOpen((value) => !value)}
        className="cursor-pointer border-none bg-transparent p-0 text-left"
      >
        <GateChip ok={ok} label={'交付终验：' + DELIVERY_LABEL[outcome.state]} />
      </button>
      {open ? (
        <span
          id={explanationId}
          role="note"
          data-testid="delivery-reason"
          className="block max-w-[420px] rounded-block border border-line-btn bg-card px-10 py-8 text-tag leading-relaxed whitespace-pre-line text-ink-2"
        >
          <span className="block">{outcome.reason}</span>
          {outcome.state === 'stopped' && stoppedStageTarget ? (
            <Link
              data-testid="stopped-stage-link"
              to={stoppedStageTarget.to}
              className="mt-7 inline-block font-semibold text-accent hover:underline"
            >
              返回当前阶段：{stoppedStageTarget.label}
            </Link>
          ) : null}
        </span>
      ) : null}
    </span>
  )
}

/** 秒数 → 人读的「N 秒 / N 分 N 秒」。running 步的 elapsed 由后端算好下发，前端不掐表。 */
function secs(n: number): string {
  if (n < 60) return n + ' 秒'
  return Math.floor(n / 60) + ' 分 ' + (n % 60) + ' 秒'
}

/** 信标事件一行：时间 / 节点 / 开始·结束 / 退出码 / 备注 */
function EventRow({ e }: { e: RunEvent }) {
  const exitToken = e.phase === 'end' && e.exit_code !== null && e.beacon !== 'ok'
    ? BEACON[e.beacon].token
    : null
  return (
    <div className="flex items-baseline gap-10 border-b border-line-row px-18 py-8">
      <Mono className="flex-none text-mini text-ink-5">{when(e.ts)}</Mono>
      <Mono className="w-170 flex-none truncate text-mini" title={e.node}>
        {e.node}
      </Mono>
      <span className="flex-none text-tag text-ink-3">{e.phase === 'start' ? '开始' : '结束'}</span>
      {e.exit_code !== null ? (
        <Mono
          className="flex-none text-mini"
          style={exitToken ? { color: exitToken.color } : undefined}
        >
          exit {e.exit_code}
          {e.beacon === 'stopped' ? ' · ' + AGENT_STOPPED.label : ''}
        </Mono>
      ) : null}
      {e.note ? <span className="min-w-0 truncate text-tag text-ink-4">{e.note}</span> : null}
    </div>
  )
}

/**
 * agent 代跑进程块：观察（最近一条的状态徽标 + 可展开日志尾部）+ 唯一一个动作（熄火，二次确认）。
 * 互斥不归前端管 —— 服务端 409（JobConflict）是裁决，这里只把记录画出来。
 */
function AgentRunBlock({
  stoppedStageTarget,
}: {
  stoppedStageTarget: StoppedStageTarget | null
}) {
  const [resumingRunId, setResumingRunId] = useState<string | null>(null)
  const runs = useAgentRuns(undefined, resumingRunId !== null)
  const latest = runs.data?.runs[0] ?? null
  const activeEpoch = latest?.epochs.find((epoch) => epoch.epoch_id === latest.active_epoch_id) ?? null
  const shownEpoch = activeEpoch ?? latest?.epochs.at(-1) ?? null
  const [logOpen, setLogOpen] = useState(false)
  const [confirmingStop, setConfirmingStop] = useState(false)
  const log = useAgentRunLog(logOpen ? latest : null)
  const pause = usePauseAgentRun()
  const resume = useResumeAgentRun()
  const stop = useStopAgentRun()

  useEffect(() => {
    if (resumingRunId === null) return
    if (latest?.run_id === resumingRunId && latest.state === 'paused') return
    setResumingRunId(null)
  }, [latest?.run_id, latest?.state, resumingRunId])

  useEffect(() => {
    if (resumingRunId === null) return
    // 202 的响应可能丢在网络途中：这段窗口继续按服务端快照收敛，不能因
    // mutation onError 立刻停轮询。若服务端确实没有受理，15 秒后恢复重试入口。
    const timer = window.setTimeout(() => {
      setResumingRunId((current) => (
        current === resumingRunId ? null : current
      ))
    }, 15_000)
    return () => window.clearTimeout(timer)
  }, [resumingRunId])

  const stateChip = (state: AgentRunState) => {
    if (state === 'stopped') return <AgentStoppedChip />
    if (state !== 'pending' && state !== 'paused') return <JobStateChip state={state} />
    return (
      <span className="rounded-full border border-line px-9 py-3 text-tag text-ink-4">
        {state === 'pending' ? '等待启动' : '已暂停'}
      </span>
    )
  }

  return (
    <Panel>
      <PaneHeader title="agent 代跑" />
      {runs.isPending ? (
        <div className="px-18 py-14 text-body text-ink-5">取代跑记录中 …</div>
      ) : runs.isError ? (
        <div className="px-18 py-14 text-body text-ink-5">取不到代跑记录。</div>
      ) : !latest ? (
        <div className="px-18 py-14 text-body text-ink-5">
          还没有 agent 代跑记录 —— 可从语料库书卡或 Stage0 审校屏发起。
        </div>
      ) : (
        <div className="flex flex-col gap-10 px-18 py-14">
          <div className="flex flex-wrap items-center gap-10">
            {latest.outcome ? (
              <DeliveryChip
                outcome={latest.outcome}
                stoppedStageTarget={
                  latest.state === 'stopped' ? stoppedStageTarget : null
                }
              />
            ) : null}
            {stateChip(latest.state)}
            <AuthorizationChip value={latest.start.authorized} label="付费授权" />
            <AuthorizationChip
              value={latest.start.egress_authorized}
              label="正文外发授权"
            />
            <Mono className="text-mini text-ink-4">{latest.run_id}</Mono>
            <Mono className="text-mini text-ink-5">
              起于 {when(latest.started)}
              {latest.ended ? ' · 止于 ' + when(latest.ended) : ''}
              {latest.exit_code !== null ? ' · exit ' + latest.exit_code : ''}
            </Mono>
            {shownEpoch ? (
              <span
                data-testid="agent-epoch"
                className="rounded-tag border border-line bg-inset px-8 py-3 text-tag text-ink-3"
              >
                {activeEpoch ? '当前执行 epoch' : '最后 epoch'}：#{shownEpoch.ordinal} ·{' '}
                {shownEpoch.reason} · {shownEpoch.code_version.slice(0, 12)}
                {latest.epochs.length > 1 ? ` · 已换 epoch ${latest.epochs.length - 1} 次` : ''}
              </span>
            ) : null}
            <span className="ml-auto flex items-center gap-8">
              <LinkButton onClick={() => setLogOpen((v) => !v)}>
                {logOpen ? '收起日志' : '日志尾部'}
              </LinkButton>
              {latest.allowed_controls.includes('pause') && !confirmingStop ? (
                <button
                  type="button"
                  disabled={pause.isPending}
                  onClick={() => pause.mutate(latest.run_id)}
                  className={cx(
                    'rounded-btn border border-line bg-card px-12 py-6 text-body text-ink-3',
                    pause.isPending ? 'cursor-not-allowed opacity-40' : 'cursor-pointer',
                  )}
                >
                  暂停
                </button>
              ) : null}
              {latest.allowed_controls.includes('resume') && !confirmingStop ? (
                <button
                  type="button"
                  disabled={resume.isPending || resumingRunId === latest.run_id}
                  onClick={() => {
                    setResumingRunId(latest.run_id)
                    resume.mutate(latest.run_id)
                  }}
                  className={cx(
                    'rounded-btn border-none bg-ink px-12 py-6 text-body font-semibold text-card',
                    resume.isPending || resumingRunId === latest.run_id
                      ? 'cursor-not-allowed opacity-40'
                      : 'cursor-pointer',
                  )}
                >
                  {resumingRunId === latest.run_id ? '恢复中…' : '继续'}
                </button>
              ) : null}
              {latest.allowed_controls.includes('stop') && !confirmingStop ? (
                <button
                  type="button"
                  onClick={() => setConfirmingStop(true)}
                  className="cursor-pointer rounded-btn border border-warn-line bg-warn-bg px-12 py-6 text-body text-warn-ink"
                >
                  熄火
                </button>
              ) : null}
              {latest.allowed_controls.includes('stop') && confirmingStop ? (
                <>
                  <span className="text-tag text-warn-ink">确认熄火？agent 进程会被杀掉。</span>
                  <button
                    type="button"
                    disabled={stop.isPending}
                    onClick={() =>
                      stop.mutate(latest.run_id, { onSettled: () => setConfirmingStop(false) })
                    }
                    className={cx(
                      'rounded-btn border-none bg-spend px-12 py-6 text-body font-semibold text-card',
                      stop.isPending ? 'cursor-not-allowed opacity-40' : 'cursor-pointer',
                    )}
                  >
                    确认熄火
                  </button>
                  <LinkButton onClick={() => setConfirmingStop(false)}>返回</LinkButton>
                </>
              ) : null}
            </span>
          </div>
          {latest.note ? (
            <div
              role="note"
              data-testid="agent-run-note"
              className="rounded-block border border-line-btn bg-inset px-13 py-9 text-body leading-relaxed text-ink-3"
            >
              {latest.note}
            </div>
          ) : null}
          <AgentProfilesSnapshot profiles={latest.start.profiles} compact />
          {logOpen ? (
            <pre className="m-0 max-h-[220px] overflow-y-auto rounded-btn-lg border border-line bg-inset px-14 py-12 font-mono text-mini leading-[1.9] whitespace-pre-wrap text-ink-2">
              {log.isPending
                ? '取日志中 …'
                : log.isError
                  ? '取不到这条代跑的日志'
                  : log.data?.text
                    ? log.data.text
                    : '（还没有输出）'}
            </pre>
          ) : null}
          {pause.isError || resume.isError || stop.isError ? (
            <div className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-body text-warn-ink">
              {errorBody(pause.error ?? resume.error ?? stop.error)}
              <ErrorHint error={pause.error ?? resume.error ?? stop.error} />
            </div>
          ) : null}
        </div>
      )}
    </Panel>
  )
}

/** 节点 id → 它产物所在的屏。stage2 两个节点分属两屏；查不到的节点保持纯文本。 */
function nodeScreen(node: string): ScreenName | null {
  const stage = node.split('.')[0]
  if (stage === 'stage2') return node === 'stage2.global' ? 'global' : 'read'
  return STAGE_SCREEN[stage] ?? null
}

const NEXT_HANDLER_LABEL: Record<RunNextHandler, string> = {
  code: '代码（0 成本）',
  executor: 'Executor（执行既有决定）',
  human: '人工（Stage6 改选等只读监控面上的例外动作）',
  none: '没有后续节点',
  unknown: '尚不可判',
}

function ResumeFrontier({
  frontier,
  steps,
}: {
  frontier: RunResumeFrontier
  steps: Array<{ node: string; label: string; beacon: RunBeacon }>
}) {
  const running = steps.filter((step) => step.beacon === 'running')
  const title =
    frontier.state === 'unknown'
      ? '下一节点尚不可判'
      : frontier.state === 'pipeline_complete'
        ? '流水线节点已全部越过'
        : `下一节点：${frontier.label}`
  return (
    <Panel>
      <PaneHeader
        title="当前执行与下一节点"
        meta="当前执行来自信标；下一节点与处理者来自后端 operator 快照"
      />
      <div data-testid="resume-frontier" className="flex flex-col gap-9 px-18 py-14">
        <div className="flex flex-wrap items-center gap-9">
          <span className="text-section font-semibold text-ink">{title}</span>
          {frontier.node ? (
            <Mono className="text-mini text-ink-4">{frontier.node}</Mono>
          ) : null}
          <span className="rounded-tag border border-line bg-inset px-8 py-3 text-tag font-semibold text-ink-3">
            下一处理者：{NEXT_HANDLER_LABEL[frontier.next_handler]}
          </span>
          <span className="rounded-tag border border-line bg-card px-8 py-3 text-tag font-semibold text-ink-3">
            {running.length
              ? `当前执行：${running.map((step) => `${step.label}（${step.node}）`).join('、')}`
              : '当前执行：没有节点信标'}
          </span>
          <span className="rounded-tag border border-line bg-card px-8 py-3 text-tag text-ink-4">
            当前快照已确认无需重跑 {frontier.verified_skipped_count} 项
          </span>
          {frontier.paid !== null ? (
            <span
              className="text-tag font-semibold"
              style={frontier.paid ? { color: SPEND_INK } : undefined}
            >
              {frontier.paid ? '节点执行：付费' : '节点执行：0 成本'}
            </span>
          ) : null}
        </div>
        <div className="prose-cn max-w-[82em] text-body leading-relaxed text-ink-3">
          {frontier.why}
        </div>
        {frontier.waiting_on ? (
          <Mono className="text-mini text-ink-5">waiting_on: {frontier.waiting_on}</Mono>
        ) : null}
      </div>
    </Panel>
  )
}

/**
 * 跑书进度。
 *
 * 这一屏回答「真实节点图跑到哪了、当前这步在不在跑、每步最近退出码是什么」。
 * 它是观察屏：不放任何操作按钮，也不自行做判定。
 * 信标只说命令跑没跑完；各层合不合格在呈现层直接组合现有 CheckReport。
 */
export function RunScreen() {
  const sid = useSid()
  const q = useRunProgress()
  const overview = useQuery({
    queryKey: qk.novel(sid),
    queryFn: () => api.getNovel(sid),
    retry: false,
  })
  const { defaultOrdinal } = useSampleCoverage()
  const stepsRef = useRef<HTMLDivElement>(null)
  const [priorStepsOpen, setPriorStepsOpen] = useState(false)

  const nodePath = (node: string): string | null => {
    const screen = nodeScreen(node)
    if (!screen) return null
    return (
      '/n/' +
      sid +
      '/' +
      screen +
      (CHAPTERED_SCREENS.has(screen) ? '/' + defaultOrdinal(sid) : '')
    )
  }

  const scrollToCurrent = () =>
    stepsRef.current
      ?.querySelector<HTMLElement>('[data-frontier]')
      ?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })

  const stoppedStageTarget = (() => {
    const frontier = q.data?.resume_frontier
    if (!frontier?.node || !frontier.label) return null
    const to = nodePath(frontier.node)
    return to ? { to, label: frontier.label } : null
  })()

  const checks = useQueries({
    queries: CHECK_KINDS.map((kind) => ({
      queryKey: qk.check(sid, kind),
      queryFn: () => api.getCheck(sid, kind),
      // reviewable 是后端给出的“已有可审产物”事实。未到这一层时不提前打
      // /check，更不能靠 404/500 猜阶段进度；stale/INVALID 仍会是 true。
      enabled: overview.data?.stage_reviewable[kind] === true,
      retry: false,
      // 轮询 refetch 期间沿用上一份数据——不闪成「校验未知」（U-00：未知 ≠ 没问题）
      placeholderData: keepPreviousData,
    })),
  })

  return (
    <section className="flex flex-col gap-16 px-30 py-24">
      <header className="flex items-end justify-between gap-24">
        <div className="flex flex-col gap-5">
          <h1 className="m-0 text-screen font-semibold">跑书进度</h1>
          <div className="prose-cn max-w-[52em] text-body text-ink-3">
            UI、队列与 agent 共用同一套运行机制。
          </div>
        </div>
      </header>

      <QueryStates q={q} loadingLabel="读跑书进度">
        {(d) => <>
          <BlueprintProgressPanel sid={sid} progress={d.blueprint_progress} />
          <Stage6RebuildPanel sid={sid} />
          <ResumeFrontier frontier={d.resume_frontier} steps={d.steps} />
        </>}
      </QueryStates>

      {/* agent 代跑进程：独立查询，进度取数失败也不挡这块 */}
      <AgentRunBlock stoppedStageTarget={stoppedStageTarget} />
      <RunMetricsPanel key={sid} sid={sid} />

      {q.data && !q.isError && (
      <QueryStates q={q} loadingLabel="读取运行明细">
        {(d) => (
          <>

            <Panel>
              <PaneHeader title="当前审阅与恢复" meta="审批状态与当前节点恢复动作均由后端提供" />
              <div className="flex flex-col gap-9 px-18 py-14 text-body">
                <p className="m-0">当前恢复：{d.current_recovery_actions == null ? '未知' :
                  d.current_recovery_actions.length ? d.current_recovery_actions.join('；') : '无已登记恢复动作'}</p>
                {d.resume_frontier.review_states == null ? <p className="m-0">审阅状态未知</p> : (
                  <ul className="m-0 pl-18" aria-label="当前审阅状态">
                    {d.resume_frontier.review_states.map((review) => (
                      <li key={`${review.node}:${review.scope}`}>{review.node} · {review.scope}：{review.state}</li>
                    ))}
                  </ul>
                )}
              </div>
            </Panel>

            <Panel>
              <PaneHeader title="工作单元与内容预算" meta="处理来源显示最近一轮，额度累计保留；生成回答仍须经过质量审阅" />
              {d.work_units == null ? (
                <p className="px-18 text-body text-ink-3">工作单元状态未知：{d.work_units_unavailable_reason ?? '缺少可核验的账面信息'}</p>
              ) : d.work_units.length === 0 ? (
                <p className="px-18 text-body text-ink-3">尚无已登记的工作单元</p>
              ) : (
                <div className="overflow-x-auto px-18 py-12">
                  <table className="w-full text-left text-body" aria-label="工作单元预算">
                    <thead><tr><th>节点 / 范围</th><th>角色</th><th>内容调用</th><th>最近处理</th><th>当前审阅</th><th>状态 / 恢复入口</th></tr></thead>
                    <tbody>{d.work_units.map((unit) => (
                      <tr key={unit.unit_id}>
                        <td className="py-9">{unit.node} · {unit.scope}</td>
                        <td>{unit.role}</td>
                        <td>基础 {unit.content_base_limit ?? '未知'} + 追加 {unit.content_granted ?? '未知'}<br />累计 <span>{unit.content_used} / {unit.content_limit}，剩余 {unit.content_remaining}</span></td>
                        <td>{unit.processing ? <>
                          总量 {unit.processing.total}，待处理 {unit.processing.pending}<br />
                          新生成 {unit.processing.generated}，机械处理 {unit.processing.mechanical}<br />
                          历史采用 {unit.processing.history_adopted}，缓存 {unit.processing.cached}<br />
                          失败 {unit.processing.failed}，未知 {unit.processing.unknown}
                          {unit.processing.reason ? <p className="m-0">{unit.processing.reason}</p> : null}
                        </> : unit.registered === false ? '尚无处理记录' : '处理来源未知'}</td>
                        <td>{unit.review ? <>
                          义务总量 {unit.review.total}<br />
                          待审 {unit.review.pending}，决定继承 {unit.review.decision_inherited}
                        </> : unit.role === 'commander' ? `审阅进度未知：${unit.review_unavailable_reason ?? '尚无当前义务投影'}` : '由人工审阅'}</td>
                        <td><span>{unit.state}</span><br /><span>{unit.recovery_action ?? '无已登记恢复动作'}</span>
                          {unit.registered !== false ? <WorkUnitBudgetGrant sid={sid} unitId={unit.unit_id} /> : null}
                        </td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
              )}
            </Panel>

            {/* NODES 清单：seq / 节点 / 付费标记 / 信标 / 最近退出码 / 耗时 */}
            <Panel>
              <PaneHeader
                title={'流水线节点 · ' + d.steps.length + ' 项'}
                meta="每行是一条命令记录；无记录不代表阶段未完成"
                right={
                  <LinkButton
                    disabled={!d.resume_frontier.node}
                    title="滚到后端判定的下一节点"
                    onClick={scrollToCurrent}
                  >
                    跳到下一节点
                  </LinkButton>
                }
              />
              <div ref={stepsRef}>
                {d.resume_frontier.verified_skipped_count > 0 ? (
                  <button
                    type="button"
                    aria-expanded={priorStepsOpen}
                    onClick={() => setPriorStepsOpen((value) => !value)}
                    className="flex w-full cursor-pointer items-center gap-8 border-x-0 border-t-0 border-b border-line-row bg-inset px-18 py-9 text-left text-body text-ink-3"
                  >
                    <span aria-hidden>{priorStepsOpen ? '▾' : '▸'}</span>
                    当前快照已确认无需重跑 {d.resume_frontier.verified_skipped_count} 个前置节点
                    <span className="text-tag text-ink-5">
                      {priorStepsOpen ? '收起' : '展开查看'}
                    </span>
                  </button>
                ) : null}
                {d.steps.filter(
                  (step) =>
                    priorStepsOpen ||
                    !d.resume_frontier.verified_skipped_nodes.includes(step.node),
                ).map((s) => {
                  const to = nodePath(s.node)
                  return (
                    <div
                      key={s.seq}
                      data-running={s.beacon === 'running' ? '' : undefined}
                      data-frontier={s.node === d.resume_frontier.node ? '' : undefined}
                      className={cx(
                        'flex items-center gap-12 border-b border-line-row px-18 py-10',
                        s.beacon === 'running' && 'bg-accent-bg',
                      )}
                    >
                      <Mono className="w-20 flex-none text-mini text-ink-5">
                        {String(s.seq).padStart(2, '0')}
                      </Mono>
                      {to ? (
                        <Link
                          to={to}
                          title={s.node}
                          className="w-190 flex-none text-body font-semibold text-ink hover:text-accent"
                        >
                          {s.label}
                        </Link>
                      ) : (
                        <span className="w-190 flex-none text-body font-semibold">{s.label}</span>
                      )}
                      <Mono className="w-150 flex-none truncate text-mini text-ink-4" title={s.node}>
                        {s.node}
                      </Mono>
                      <Mono
                        className="w-40 flex-none text-mini"
                        style={s.paid ? { color: SPEND_INK } : undefined}
                      >
                        {s.paid ? '花钱' : '免费'}
                      </Mono>
                      <span className="flex-none">
                        <BeaconChip beacon={s.beacon} />
                      </span>
                      <Mono className="ml-auto flex-none text-mini text-ink-4">
                        {s.last_exit === null ? '—' : 'exit ' + s.last_exit}
                      </Mono>
                      <Mono className="w-110 flex-none text-right text-mini text-ink-4">
                        {s.beacon === 'running' && s.elapsed !== null ? '已跑 ' + secs(s.elapsed) : ''}
                      </Mono>
                      <Mono className="w-110 flex-none text-right text-mini text-ink-5">
                        {s.last_ts === null ? '' : when(s.last_ts)}
                      </Mono>
                    </div>
                  )
                })}
              </div>
            </Panel>

            {/* 各层产物完成度：盘点自产物盘，与信标无关；书目录还没建时全是「判不了」 */}
            <Panel>
              <PaneHeader
                title="各层产物完成度与校验"
                meta="完成度来自产物盘；badge 与计数来自各层 CheckReport，二者互不改写"
              />
              <div className="flex flex-wrap gap-8 px-18 py-14">
                {STAGES.map((stage, index) => {
                  const done = d.stage_done[stage.key]
                  const reviewable = overview.data?.stage_reviewable[stage.key]
                  const checkQuery = checks[index]
                  // placeholderData 保住了 refetch 期间的上一份——这里只看「有没有数据」，
                  // 不再看 isFetching，否则每次轮询都会闪回「校验未知」
                  const report = (
                    reviewable === true && checkQuery && !checkQuery.isError
                      ? checkQuery.data ?? null
                      : null
                  ) as CheckReport | null
                  const hard = report?.rules.filter(
                    (row) => row.sev === 'hard' && row.status === 'hit',
                  ).length ?? 0
                  const info = report?.rules.filter(
                    (row) => row.sev === 'info' && row.status === 'hit',
                  ).length ?? 0
                  const unver = report ? report.counts.unver + report.counts.skipped : 0
                  const tone =
                    done === true
                      ? { color: GATE.ok.chipColor, bg: GATE.ok.chipBg, text: '✓ 已完成' }
                      : done === false
                        ? { color: GATE.wait.chipColor, bg: GATE.wait.chipBg, text: '· 待继续' }
                        : { color: FRESH.unknown.color, bg: FRESH.unknown.bg, text: '? 判不了' }
                  return (
                    <div
                      key={stage.key}
                      className="flex min-w-210 flex-col gap-6 rounded-tag border border-line bg-card px-10 py-8"
                    >
                      <div className="flex items-baseline gap-6 text-tag">
                        <span className="font-semibold">{stage.label}</span>
                        <Mono className="text-micro text-ink-4">{stage.key}</Mono>
                        <span
                          className="ml-auto rounded-tag px-7 py-2"
                          style={{ color: tone.color, background: tone.bg }}
                        >
                          {tone.text}
                        </span>
                      </div>
                      <div className="flex items-center gap-7">
                        {report ? (
                          <>
                            <CheckBadge badge={report.badge} />
                            {report.badge === 'amber' ? <UnverifiedHelp report={report} /> : null}
                            <Mono className="text-micro text-ink-5">
                              hard {hard} · info {info} · unver {unver}
                            </Mono>
                          </>
                        ) : reviewable === false ? (
                          <span className="text-tag text-ink-4">· 尚不可检查</span>
                        ) : (
                          <span className="text-tag text-ink-4">? 校验未知</span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </Panel>

            {/* 信标事件尾部：人读时间线，新的在上 */}
            <Panel>
              <PaneHeader title="信标事件（最近）" meta="agent 每执行一条命令，前后各打一点" />
              {d.events_tail.length === 0 ? (
                <div className="px-18 py-16 text-body text-ink-5">
                  还没有任何信标事件 —— agent 还没开始跑，或还没打点。
                </div>
              ) : (
                [...d.events_tail].reverse().map((e) => (
                  <EventRow key={e.ts + ':' + e.node + ':' + e.phase} e={e} />
                ))
              )}
            </Panel>
          </>
        )}
      </QueryStates>
      )}
    </section>
  )
}
