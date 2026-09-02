import type { AgentProfile, ExecutorBackend, PipelineAgentProfiles } from '@/types/agent'
import { Mono } from '@/components/ui/Mono'

function profileText(profile: AgentProfile): string {
  return [
    profile.driver,
    profile.model,
    'effort=' + profile.reasoning_effort,
    'mode=' + (profile.execution_mode ?? 'null'),
  ].join(' · ')
}

function backendText(backend: ExecutorBackend): string {
  if (backend.kind === 'agent') return 'agent · ' + profileText(backend.profile)
  return [
    'legacy',
    backend.profile.provider,
    backend.profile.model,
    'effort=' + backend.profile.reasoning_effort,
    'thinking=' + backend.profile.thinking_mode,
  ].join(' · ')
}

export function ExecutorBackendSnapshot({
  backend,
  unknownLabel = 'Executor 后端未知 · 历史未记录或该节点不调用模型',
}: {
  backend: ExecutorBackend | null | undefined
  unknownLabel?: string
}) {
  return backend ? (
    <Mono className="text-mini text-ink-3">{backendText(backend)}</Mono>
  ) : (
    <span className="text-tag text-ink-5">? {unknownLabel}</span>
  )
}

export function AgentProfileSnapshot({
  profile,
  unknownLabel = 'Executor 配置未知 · 历史未记录或该节点未接 Agent',
}: {
  profile: AgentProfile | null | undefined
  unknownLabel?: string
}) {
  return profile ? (
    <Mono className="text-mini text-ink-3">{profileText(profile)}</Mono>
  ) : (
    <span className="text-tag text-ink-5">? {unknownLabel}</span>
  )
}

/**
 * 历史只展示启动时冻结的精确 id，不用今天 capability 的 label 或默认回填。
 * profiles=null 是独立的 unknown 状态，不是「沿用默认」。
 */
export function AgentProfilesSnapshot({
  profiles,
  compact = false,
}: {
  profiles: PipelineAgentProfiles | null
  compact?: boolean
}) {
  if (profiles === null) {
    return (
      <div
        data-testid="agent-profiles-unknown"
        className="rounded-tag border border-line bg-inset px-10 py-7 text-tag text-ink-4"
      >
        ? Agent 配置未知 · 历史未记录，不能按当前默认倒推
      </div>
    )
  }
  const overrides = Object.entries(profiles.executor_overrides)
  return (
    <div
      data-testid="agent-profiles-snapshot"
      className="rounded-tag border border-line bg-inset px-10 py-7 text-tag text-ink-3"
    >
      <div className={compact ? 'flex flex-wrap gap-x-12 gap-y-3' : 'grid gap-4'}>
        <span><strong>指挥：</strong><Mono>{profileText(profiles.commander)}</Mono></span>
        <span><strong>默认执行：</strong><Mono>{backendText(profiles.executor_default)}</Mono></span>
      </div>
      {overrides.length > 0 ? (
        <div className="mt-5 grid gap-3 border-t border-line-soft pt-5">
          {overrides.map(([node, backend]) => (
            <span key={node}>
              <Mono>{node}</Mono> → <Mono>{backendText(backend)}</Mono>
            </span>
          ))}
        </div>
      ) : (
        <div className="mt-4 text-ink-5">节点覆盖：无</div>
      )}
    </div>
  )
}
