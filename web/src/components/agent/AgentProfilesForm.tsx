import type {
  AgentAvailability,
  AgentCapabilitiesResponse,
  AgentRole,
} from '@/types/agent'
import type {
  AgentProfileDraft,
  ExecutorBackendDraft,
  LegacyLlmProfileDraft,
  PipelineAgentProfilesDraft,
} from '@/lib/agentProfiles'
import {
  driverForDraft,
  executorBackendDraftForNode,
  legacyModelLabelById,
  legacyProviderForDraft,
  withBackendKind,
  withExecutorDefault,
  withLegacyModel,
  withLegacyProvider,
  withLegacyThinking,
  withProfileDriver,
  withProfileModel,
} from '@/lib/agentProfiles'
import { cx } from '@/lib/cx'

const AVAILABILITY_LABEL: Record<AgentAvailability, string> = {
  ready: '可用',
  missing: '未安装',
  unknown: '状态未知',
  unauthenticated: '未登录',
}

export function AgentProfileFields({
  capabilities,
  role,
  label,
  idPrefix,
  value,
  onChange,
  disabled = false,
}: {
  capabilities: AgentCapabilitiesResponse
  role: AgentRole
  label: string
  idPrefix: string
  value: AgentProfileDraft
  onChange: (next: AgentProfileDraft) => void
  disabled?: boolean
}) {
  const drivers = capabilities.drivers.filter((driver) => driver.roles.includes(role))
  const driver = driverForDraft(capabilities, value)
  const model = driver?.models.find((candidate) => candidate.id === value.model) ?? null
  const driverReady = driver?.availability === 'ready' && driver.roles.includes(role)

  return (
    <fieldset
      disabled={disabled}
      className="m-0 flex flex-col gap-9 rounded-block border border-line bg-sunken-2 px-13 py-11"
    >
      <legend className="px-4 text-body font-semibold text-ink-2">{label}</legend>

      <div className="grid grid-cols-2 gap-9">
        <Field label="Driver" htmlFor={idPrefix + '-driver'}>
          <select
            id={idPrefix + '-driver'}
            aria-label={label + ' driver'}
            value={value.driver}
            onChange={(event) => onChange(withProfileDriver(value, event.target.value))}
            className="w-full rounded-btn border border-line-btn bg-card px-9 py-7 text-body text-ink"
          >
            <option value="">请选择</option>
            {drivers.map((candidate) => (
              <option
                key={candidate.id}
                value={candidate.id}
                disabled={candidate.availability !== 'ready'}
              >
                {candidate.label} · {AVAILABILITY_LABEL[candidate.availability]}
              </option>
            ))}
          </select>
        </Field>

        <Field label="模型" htmlFor={idPrefix + '-model'}>
          <select
            id={idPrefix + '-model'}
            aria-label={label + ' 模型'}
            value={value.model}
            disabled={disabled || !driverReady}
            onChange={(event) => onChange(withProfileModel(value, event.target.value))}
            className="w-full rounded-btn border border-line-btn bg-card px-9 py-7 text-body text-ink disabled:bg-inset disabled:text-ink-5"
          >
            <option value="">请选择</option>
            {(driver?.models ?? []).map((candidate) => (
              <option key={candidate.id} value={candidate.id}>{candidate.label}</option>
            ))}
          </select>
        </Field>

        <Field label="思考强度" htmlFor={idPrefix + '-effort'}>
          <select
            id={idPrefix + '-effort'}
            aria-label={label + ' 思考强度'}
            value={value.reasoning_effort}
            disabled={disabled || !driverReady || model === null}
            onChange={(event) => onChange({ ...value, reasoning_effort: event.target.value })}
            className="w-full rounded-btn border border-line-btn bg-card px-9 py-7 text-body text-ink disabled:bg-inset disabled:text-ink-5"
          >
            <option value="">请选择</option>
            {(model?.reasoning_efforts ?? []).map((effort) => (
              <option key={effort} value={effort}>{effort}</option>
            ))}
          </select>
        </Field>

        <Field label="执行模式" htmlFor={idPrefix + '-mode'}>
          {model && model.execution_modes.length === 0 ? (
            <div className="rounded-btn border border-line bg-inset px-9 py-7 text-body text-ink-5">
              服务端未发布可选模式
            </div>
          ) : (
            <select
              id={idPrefix + '-mode'}
              aria-label={label + ' 执行模式'}
              value={value.execution_mode}
              disabled={disabled || !driverReady || model === null}
              onChange={(event) => onChange({ ...value, execution_mode: event.target.value })}
              className="w-full rounded-btn border border-line-btn bg-card px-9 py-7 text-body text-ink disabled:bg-inset disabled:text-ink-5"
            >
              <option value="">请选择</option>
              {(model?.execution_modes ?? []).map((mode) => (
                <option key={mode} value={mode}>{mode}</option>
              ))}
            </select>
          )}
        </Field>
      </div>

      <div className="grid gap-5" data-testid={idPrefix + '-availability'}>
        {drivers.map((candidate) => (
          <div
            key={candidate.id}
            className={cx(
              'rounded-tag border px-9 py-6 text-tag',
              candidate.availability === 'ready'
                ? 'border-gate-ok-line bg-gate-ok-strip text-gate-ok-ink'
                : 'border-line-btn bg-inset text-ink-4',
            )}
          >
            <span className="font-semibold">
              {candidate.label} · {AVAILABILITY_LABEL[candidate.availability]}
            </span>
            {candidate.version ? <span> · {candidate.version}</span> : null}
            <span>：{candidate.availability_reason}</span>
          </div>
        ))}
      </div>
    </fieldset>
  )
}

function LegacyLlmProfileFields({
  capabilities,
  label,
  idPrefix,
  value,
  onChange,
  disabled = false,
}: {
  capabilities: AgentCapabilitiesResponse
  label: string
  idPrefix: string
  value: LegacyLlmProfileDraft
  onChange: (next: LegacyLlmProfileDraft) => void
  disabled?: boolean
}) {
  const provider = legacyProviderForDraft(capabilities, value)
  const model = provider?.models.find((candidate) => candidate.id === value.model) ?? null
  const thinking = model?.thinking_modes.find(
    (candidate) => candidate.id === value.thinking_mode,
  ) ?? null

  return (
    <fieldset
      disabled={disabled}
      className="m-0 flex flex-col gap-9 rounded-block border border-line bg-sunken-2 px-13 py-11"
    >
      <legend className="px-4 text-body font-semibold text-ink-2">{label}</legend>
      <div className="grid grid-cols-2 gap-9">
        <Field label="API Provider" htmlFor={idPrefix + '-provider'}>
          <select
            id={idPrefix + '-provider'}
            aria-label={label + ' provider'}
            value={value.provider}
            onChange={(event) => onChange(withLegacyProvider(value, event.target.value))}
            className="w-full rounded-btn border border-line-btn bg-card px-9 py-7 text-body text-ink"
          >
            <option value="">请选择</option>
            {capabilities.legacy_providers.map((candidate) => (
              <option key={candidate.id} value={candidate.id}>{candidate.label}</option>
            ))}
          </select>
        </Field>

        <Field label="模型" htmlFor={idPrefix + '-model'}>
          <select
            id={idPrefix + '-model'}
            aria-label={label + ' 模型'}
            value={value.model}
            disabled={disabled || provider === null}
            onChange={(event) => onChange(withLegacyModel(value, event.target.value))}
            className="w-full rounded-btn border border-line-btn bg-card px-9 py-7 text-body text-ink disabled:bg-inset disabled:text-ink-5"
          >
            <option value="">请选择</option>
            {(provider?.models ?? []).map((candidate) => (
              <option key={candidate.id} value={candidate.id}>{candidate.label}</option>
            ))}
          </select>
        </Field>

        <Field label="思考模式" htmlFor={idPrefix + '-thinking'}>
          <select
            id={idPrefix + '-thinking'}
            aria-label={label + ' 思考模式'}
            value={value.thinking_mode}
            disabled={disabled || model === null}
            onChange={(event) => onChange(withLegacyThinking(value, event.target.value))}
            className="w-full rounded-btn border border-line-btn bg-card px-9 py-7 text-body text-ink disabled:bg-inset disabled:text-ink-5"
          >
            <option value="">请选择</option>
            {(model?.thinking_modes ?? []).map((candidate) => (
              <option key={candidate.id} value={candidate.id}>{candidate.label}</option>
            ))}
          </select>
        </Field>

        <Field label="思考强度" htmlFor={idPrefix + '-effort'}>
          <select
            id={idPrefix + '-effort'}
            aria-label={label + ' 思考强度'}
            value={value.reasoning_effort}
            disabled={disabled || thinking === null}
            onChange={(event) => onChange({ ...value, reasoning_effort: event.target.value })}
            className="w-full rounded-btn border border-line-btn bg-card px-9 py-7 text-body text-ink disabled:bg-inset disabled:text-ink-5"
          >
            <option value="">请选择</option>
            {(thinking?.reasoning_efforts ?? []).map((effort) => (
              <option key={effort} value={effort}>{effort}</option>
            ))}
          </select>
        </Field>
      </div>
      {model ? (
        <div className="rounded-tag border border-line-btn bg-card px-9 py-6 text-tag text-ink-4">
          可承接节点：{model.executor_nodes.join('、')}
        </div>
      ) : null}
    </fieldset>
  )
}

function ExecutorBackendFields({
  capabilities,
  label,
  idPrefix,
  value,
  onChange,
  disabled = false,
}: {
  capabilities: AgentCapabilitiesResponse
  label: string
  idPrefix: string
  value: ExecutorBackendDraft
  onChange: (next: ExecutorBackendDraft) => void
  disabled?: boolean
}) {
  return (
    <div className="flex flex-col gap-8 rounded-block border border-line-btn bg-card px-12 py-11">
      <label className="flex items-center gap-10 text-body font-semibold text-ink-2">
        <span>{label}后端</span>
        <select
          aria-label={label + ' 后端'}
          value={value.kind}
          disabled={disabled}
          onChange={(event) => onChange(withBackendKind(
            value,
            event.target.value as ExecutorBackendDraft['kind'],
          ))}
          className="ml-auto rounded-btn border border-line-btn bg-card px-9 py-7 text-body font-normal text-ink"
        >
          <option value="">请选择</option>
          <option value="agent">Agent CLI</option>
          <option value="legacy">Legacy LLM API</option>
        </select>
      </label>
      {value.kind === 'agent' ? (
        <AgentProfileFields
          capabilities={capabilities}
          role="executor"
          label={label + ' · Agent CLI'}
          idPrefix={idPrefix + '-agent'}
          value={value.agent_profile}
          disabled={disabled}
          onChange={(agentProfile) => onChange({ ...value, agent_profile: agentProfile })}
        />
      ) : value.kind === 'legacy' ? (
        <LegacyLlmProfileFields
          capabilities={capabilities}
          label={label + ' · Legacy API'}
          idPrefix={idPrefix + '-legacy'}
          value={value.legacy_profile}
          disabled={disabled}
          onChange={(legacyProfile) => onChange({ ...value, legacy_profile: legacyProfile })}
        />
      ) : (
        <div className="rounded-btn border border-line bg-inset px-10 py-8 text-tag text-ink-5">
          先选择执行后端；两种 profile 不会混用。
        </div>
      )}
    </div>
  )
}

function Field({
  label,
  htmlFor,
  children,
}: {
  label: string
  htmlFor: string
  children: React.ReactNode
}) {
  return (
    <label htmlFor={htmlFor} className="flex min-w-0 flex-col gap-4 text-sm2 text-ink-4">
      <span>{label}</span>
      {children}
    </label>
  )
}

export function PipelineAgentProfilesForm({
  capabilities,
  value,
  onChange,
  errors,
  disabled = false,
}: {
  capabilities: AgentCapabilitiesResponse
  value: PipelineAgentProfilesDraft
  onChange: (next: PipelineAgentProfilesDraft) => void
  errors: string[]
  disabled?: boolean
}) {
  return (
    <div className="flex flex-col gap-11" data-testid="pipeline-agent-profiles-form">
      <AgentProfileFields
        capabilities={capabilities}
        role="commander"
        label="指挥大脑"
        idPrefix="agent-commander"
        value={value.commander}
        disabled={disabled}
        onChange={(commander) => onChange({ ...value, commander })}
      />
      <ExecutorBackendFields
        capabilities={capabilities}
        label="默认执行者"
        idPrefix="agent-executor-default"
        value={value.executor_default}
        disabled={disabled}
        onChange={(executorDefault) => onChange(withExecutorDefault(capabilities, value, executorDefault))}
      />

      <fieldset className="m-0 rounded-block border border-line bg-sunken-2 px-13 py-11">
        <legend className="px-4 text-body font-semibold text-ink-2">逐节点执行者覆盖</legend>
        <div className="mb-8 text-tag text-ink-4">
          未启用的节点继承默认执行者；节点清单完全来自 capability。标了「产品契约钉死」的节点
          在 Legacy 下必须覆盖为指定模型（质量裁决，与 Agent/Legacy 模式无关）。
        </div>
        <div className="flex flex-col gap-7">
          {capabilities.executor_nodes.map((node) => {
            const override = value.executor_overrides[node]
            const enabled = override !== undefined
            const pinId = capabilities.legacy_model_pins[node]
            return (
              <details
                key={node}
                open={enabled}
                className="rounded-btn border border-line-btn bg-card px-10 py-7"
              >
                <summary className="flex cursor-pointer list-none items-center gap-8">
                  <input
                    type="checkbox"
                    aria-label={`单独配置 ${node}`}
                    checked={enabled}
                    disabled={disabled}
                    onClick={(event) => event.stopPropagation()}
                    onChange={(event) => {
                      const next = { ...value.executor_overrides }
                      if (event.target.checked) {
                        next[node] = executorBackendDraftForNode(capabilities, value.executor_default, node)
                      } else {
                        delete next[node]
                      }
                      onChange({ ...value, executor_overrides: next })
                    }}
                  />
                  <span className="font-mono text-mini text-ink-2">{node}</span>
                  {pinId ? (
                    <span className="text-tag text-ink-4">
                      产品契约钉死 {legacyModelLabelById(capabilities, pinId)}
                    </span>
                  ) : null}
                  <span className="ml-auto text-tag text-ink-5">
                    {enabled ? '已覆盖' : '继承默认'}
                  </span>
                </summary>
                {override ? (
                  <div className="mt-9">
                    <ExecutorBackendFields
                      capabilities={capabilities}
                      label={node + ' 执行者'}
                      idPrefix={'agent-override-' + node.replaceAll('.', '-')}
                      value={override}
                      disabled={disabled}
                      onChange={(backend) => onChange({
                        ...value,
                        executor_overrides: {
                          ...value.executor_overrides,
                          [node]: backend,
                        },
                      })}
                    />
                  </div>
                ) : null}
              </details>
            )
          })}
        </div>
      </fieldset>

      <fieldset disabled={disabled} className="m-0 rounded-block border border-line bg-sunken-2 px-13 py-11">
        <legend className="px-4 text-body font-semibold text-ink-2">并发配置</legend>
        <p className="m-0 mb-8 text-tag text-ink-4">
          默认逐节点并发 {capabilities.concurrency.default_node}，整书总上限 {capabilities.concurrency.defaults.per_book}，
          同一输出根的单供应商总上限 {capabilities.concurrency.defaults.per_provider}。实际调用同时受三个上限约束。
        </p>
        <label className="flex items-center gap-8">
          <input type="checkbox" checked={!!value.concurrency} onChange={(event) => {
            const next = { ...value }
            if (event.target.checked) {
              next.concurrency = { ...capabilities.concurrency.defaults,
                node_overrides: { ...capabilities.concurrency.defaults.node_overrides } }
            } else delete next.concurrency
            onChange(next)
          }} />
          自定义并发
        </label>
        {value.concurrency ? <div className="mt-9 grid grid-cols-2 gap-9">
          {capabilities.concurrency.nodes.map((node) => <label key={node} className="grid gap-4 text-tag">
            {node} 并发
            <select aria-label={`${node} 并发`} value={value.concurrency!.node_overrides[node] ?? ''}
              onChange={(event) => {
                const nodeOverrides = { ...value.concurrency!.node_overrides }
                if (event.target.value) nodeOverrides[node] = Number(event.target.value)
                else delete nodeOverrides[node]
                onChange({ ...value, concurrency: { ...value.concurrency!, node_overrides: nodeOverrides } })
              }}>
              <option value="">默认（{capabilities.concurrency.default_node}）</option>
              {capabilities.concurrency.node_choices.map((count) => <option key={count} value={count}>{count}</option>)}
            </select>
          </label>)}
          {(['per_book', 'per_provider'] as const).map((field) => <label key={field} className="grid gap-4 text-tag">
            {field === 'per_book' ? '整书总并发' : '供应商总并发'}
            <input type="number" min={capabilities.concurrency.total_min} max={capabilities.concurrency.total_max}
              value={value.concurrency![field]} onChange={(event) => onChange({ ...value,
                concurrency: { ...value.concurrency!, [field]: Number(event.target.value) } })} />
          </label>)}
        </div> : null}
      </fieldset>

      {errors.length > 0 ? (
        <div
          role="status"
          className="rounded-block border border-warn-line bg-warn-bg px-13 py-9 text-tag text-warn-ink"
        >
          <div className="font-semibold">配置尚未完成，因此不会请求预览或启动：</div>
          <ul className="m-0 mt-4 pl-18">
            {errors.map((error) => <li key={error}>{error}</li>)}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
