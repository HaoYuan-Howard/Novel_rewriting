import type { AgentCapabilitiesResponse } from '@/types/agent'
import type {
  ExecutorBackendDraft,
  LegacyLlmProfileDraft,
  PipelineAgentProfilesDraft,
} from '@/lib/agentProfiles'
import {
  executorBackendDraftForNode,
  legacyModelLabelById,
  legacyProviderForDraft,
  withExecutorDefault,
  withLegacyModel,
  withLegacyProvider,
  withLegacyThinking,
} from '@/lib/agentProfiles'

function ModelExecutionFields({
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
        <Field label="Provider" htmlFor={idPrefix + '-provider'}>
          <select
            id={idPrefix + '-provider'}
            aria-label={label + ' Provider'}
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

        <Field label="Model" htmlFor={idPrefix + '-model'}>
          <select
            id={idPrefix + '-model'}
            aria-label={label + ' Model'}
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

        <Field label="Thinking" htmlFor={idPrefix + '-thinking'}>
          <select
            id={idPrefix + '-thinking'}
            aria-label={label + ' Thinking'}
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

        <Field label="Effort" htmlFor={idPrefix + '-effort'}>
          <select
            id={idPrefix + '-effort'}
            aria-label={label + ' Effort'}
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
    <ModelExecutionFields
      capabilities={capabilities}
      label={label}
      idPrefix={idPrefix}
      value={value.profile}
      disabled={disabled}
      onChange={(profile) => onChange({ kind: 'legacy', profile })}
    />
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
      <ExecutorBackendFields
        capabilities={capabilities}
        label="默认模型执行配置"
        idPrefix="agent-executor-default"
        value={value.executor_default}
        disabled={disabled}
        onChange={(executorDefault) => onChange(withExecutorDefault(capabilities, value, executorDefault))}
      />

      <fieldset className="m-0 rounded-block border border-line bg-sunken-2 px-13 py-11">
        <legend className="px-4 text-body font-semibold text-ink-2">逐节点模型执行配置覆盖</legend>
        <div className="mb-8 text-tag text-ink-4">
          未启用的节点继承默认配置；节点清单完全来自 capability。标了「产品契约钉死」的节点
          必须覆盖为指定 Model（质量裁决）。
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
                      label={node + ' 模型执行配置'}
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
