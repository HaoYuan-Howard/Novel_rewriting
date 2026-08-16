import { useState } from 'react'
import {
  FRESH,
  GATE,
  JOB,
  SPEND_INK,
  STAGES,
  when,
  type JobStateToken,
} from '@/constants/vocabulary'
import { errorBody } from '@/api/errors'
import type { RunBeacon, RunEvent } from '@/types/run'
import { useRunProgress } from '@/hooks/useRunProgress'
import { useAgentRunLog, useAgentRuns, useStopAgentRun } from '@/hooks/useAgentRuns'
import { QueryStates } from '@/components/state/QueryStates'
import { Mono } from '@/components/ui/Mono'
import { Panel, PaneHeader } from '@/components/ui/Panel'
import { LinkButton } from '@/components/ui/ActionButton'
import { GateChip, JobStateChip } from '@/components/vocab/Badges'
import { cx } from '@/lib/cx'

/**
 * 信标四态借用 JOB 的色与形：
 * pending 借 unknown 的中性灰（空心虚线圆）——「还没跑到」和「不知道」都不是结论；
 * running / ok / failed 直接用 JOB 自己的文案。
 * ok 念作「跑完了」：它只表示那条命令 exit 0，**绝不渲染成「合格 / 通过」**——
 * 判定只归各层屏的 badge（信标是观察轨迹，不是判据）。
 */
const BEACON: Record<RunBeacon, { token: JobStateToken; label: string }> = {
  pending: { token: JOB.unknown, label: 'pending 还没跑到' },
  running: { token: JOB.running, label: JOB.running.label },
  ok: { token: JOB.ok, label: JOB.ok.label },
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

/** 新记录是布尔；历史事件未采集正文外发授权时必须显示未知，不能倒成已授权。 */
function AuthorizationChip({ value, label }: { value: boolean | null; label: string }) {
  if (value !== null) return <GateChip ok={value} label={label} />
  return (
    <span className="whitespace-nowrap rounded-chip border border-line px-10 py-4 text-tag text-ink-4">
      ? {label} · 历史未记录
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
  const bad = e.phase === 'end' && e.exit_code !== null && e.exit_code !== 0
  return (
    <div className="flex items-baseline gap-10 border-b border-line-row px-18 py-8">
      <Mono className="flex-none text-mini text-ink-5">{when(e.ts)}</Mono>
      <Mono className="w-170 flex-none truncate text-mini" title={e.node}>
        {e.node}
      </Mono>
      <span className="flex-none text-tag text-ink-3">{e.phase === 'start' ? '开始' : '结束'}</span>
      {e.exit_code !== null ? (
        <Mono className="flex-none text-mini" style={bad ? { color: JOB.failed.color } : undefined}>
          exit {e.exit_code}
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
function AgentRunBlock() {
  const runs = useAgentRuns()
  const latest = runs.data?.runs[0] ?? null
  const running = latest?.state === 'running'
  const [logOpen, setLogOpen] = useState(false)
  const [confirmingStop, setConfirmingStop] = useState(false)
  const log = useAgentRunLog(logOpen ? latest : null)
  const stop = useStopAgentRun()

  return (
    <Panel>
      <PaneHeader title="agent 代跑" meta="发起口在语料库屏；互斥由服务端 409 裁决" />
      {runs.isPending ? (
        <div className="px-18 py-14 text-body text-ink-5">取代跑记录中 …</div>
      ) : runs.isError ? (
        <div className="px-18 py-14 text-body text-ink-5">取不到代跑记录。</div>
      ) : !latest ? (
        <div className="px-18 py-14 text-body text-ink-5">
          还没有 agent 代跑记录 —— 发起口在语料库屏每本书的行尾。
        </div>
      ) : (
        <div className="flex flex-col gap-10 px-18 py-14">
          <div className="flex flex-wrap items-center gap-10">
            <JobStateChip state={latest.state} />
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
            <span className="ml-auto flex items-center gap-8">
              <LinkButton onClick={() => setLogOpen((v) => !v)}>
                {logOpen ? '收起日志' : '日志尾部'}
              </LinkButton>
              {running && !confirmingStop ? (
                <button
                  type="button"
                  onClick={() => setConfirmingStop(true)}
                  className="cursor-pointer rounded-btn border border-warn-line bg-warn-bg px-12 py-6 text-body text-warn-ink"
                >
                  熄火
                </button>
              ) : null}
              {running && confirmingStop ? (
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
          {stop.isError ? (
            <div className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-body text-warn-ink">
              {errorBody(stop.error)}
            </div>
          ) : null}
        </div>
      )}
    </Panel>
  )
}

/**
 * 跑书进度。
 *
 * 这一屏回答「真实节点图跑到哪了、当前这步在不在跑、每步最近退出码是什么」。
 * 它是观察屏：不放任何操作按钮，也不做判定 ——
 * 信标只说命令跑没跑完，各层合不合格去各层屏看 badge。
 */
export function RunScreen() {
  const q = useRunProgress()

  return (
    <section className="flex flex-col gap-16 px-30 py-24">
      <header className="flex items-end justify-between gap-24">
        <div className="flex flex-col gap-5">
          <h1 className="m-0 text-screen font-semibold">跑书进度</h1>
          <div className="prose-cn max-w-[52em] text-body text-ink-3">
            UI、队列与 agent 共用同一张真实节点图。这一屏只跟踪跑到哪一步、当前步在不在跑、每步最近的退出码——
            「跑完」不等于「合格」，判定只看各层屏的 badge。
          </div>
        </div>
      </header>

      {/* agent 代跑进程：独立查询，进度取数炸了也不挡这块 */}
      <AgentRunBlock />

      <QueryStates q={q} loadingLabel="读跑书进度">
        {(d) => (
          <>
            {/* NODES 清单：seq / 节点 / 付费标记 / 信标 / 最近退出码 / 耗时 */}
            <Panel>
              <PaneHeader
                title={'流水线节点 · ' + d.steps.length + ' 项'}
                meta="信标只说这条命令跑没跑完；合不合格看各层屏的 badge"
              />
              {d.steps.map((s) => (
                <div
                  key={s.seq}
                  className="flex items-center gap-12 border-b border-line-row px-18 py-10"
                >
                  <Mono className="w-20 flex-none text-mini text-ink-5">
                    {String(s.seq).padStart(2, '0')}
                  </Mono>
                  <span className="w-190 flex-none text-body font-semibold">{s.label}</span>
                  <Mono className="w-150 flex-none text-mini text-ink-4">{s.node}</Mono>
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
              ))}
            </Panel>

            {/* 各层产物完成度：盘点自产物盘，与信标无关；书目录还没建时全是「判不了」 */}
            <Panel>
              <PaneHeader title="各层产物完成度" meta="来自产物盘的真实盘点，与信标无关" />
              <div className="flex flex-wrap gap-8 px-18 py-14">
                {STAGES.map((stage) => {
                  const done = d.stage_done[stage.key]
                  const tone =
                    done === true
                      ? { color: GATE.ok.chipColor, bg: GATE.ok.chipBg, text: '✓ 已完成' }
                      : done === false
                        ? { color: GATE.wait.chipColor, bg: GATE.wait.chipBg, text: '· 待继续' }
                        : { color: FRESH.unknown.color, bg: FRESH.unknown.bg, text: '? 判不了' }
                  return (
                    <span
                      key={stage.key}
                      className="inline-flex items-baseline gap-6 rounded-tag px-9 py-3 text-tag"
                      style={{ color: tone.color, background: tone.bg }}
                    >
                      <span className="font-semibold">{stage.label}</span>
                      <Mono className="text-micro">{stage.key}</Mono>
                      <span>{tone.text}</span>
                    </span>
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
                [...d.events_tail].reverse().map((e, i) => <EventRow key={i} e={e} />)
              )}
            </Panel>
          </>
        )}
      </QueryStates>
    </section>
  )
}
