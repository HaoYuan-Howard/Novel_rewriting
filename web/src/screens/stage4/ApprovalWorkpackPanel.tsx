import { errorBody } from '@/api/errors'
import { ErrorHint } from '@/components/state/States'
import { Mono } from '@/components/ui/Mono'
import { useApprovalWorkpack } from '@/hooks/useApprovalWorkpack'
import type {
  ApprovalWorkpackEffect,
  ApprovalWorkpackGroupKind,
} from '@/types/approvals'

const GROUP_LABEL: Record<ApprovalWorkpackGroupKind, string> = {
  blocking: '阻断项',
  semantic_attention: '语义关注',
  disclosure: '完整披露',
  metric_only: '只读指标',
  phase_deferred: '后续阶段判定',
  coverage: '覆盖清单',
}

const EFFECT_LABEL: Record<ApprovalWorkpackEffect, string> = {
  block_b: '阻断 B',
  block_context: '阻断 Context',
  attention: '需人工关注',
  disclose: '披露保留',
  read_only: '只读',
  phase_deferred: '阶段后置',
  coverage: '覆盖导航',
}

/**
 * Stage4 A/B 的 exception-first 工作包。
 *
 * 分组、effect 与 allowed_actions 全部照画服务端响应；这里没有阈值、没有自动 accept，
 * allowed_actions 也只作 typed 入口披露，不在读面上偷偷生成付费按钮。
 */
export function ApprovalWorkpackPanel({ kind }: { kind: 'design' | 'structure' }) {
  const query = useApprovalWorkpack(kind)
  return <ApprovalWorkpackEvidence kind={kind} query={query} />
}

/**
 * 父审批面已经持有 workpack query 时复用同一 observer 的投影。若在同一屏再挂一次
 * ``useApprovalWorkpack``，staleTime=0 会在子组件 mount 时立即 background refetch，既
 * 重扫一次后端证据，也让刚成功读取的提交按钮无故闪回 disabled。
 */
export function ApprovalWorkpackEvidence({
  kind,
  query,
}: {
  kind: 'design' | 'structure'
  query: ReturnType<typeof useApprovalWorkpack>
}) {
  const label = kind === 'design' ? 'Stage4A 候选工作包' : 'Stage4B 结构工作包'

  if (query.isLoading) {
    return (
      <div className="border-b border-line-row bg-sunken-2 px-20 py-14 text-sm2 text-ink-4">
        读取{label}…
      </div>
    )
  }
  if (query.isError) {
    return (
      <div className="border-b border-warn-line bg-warn-bg px-20 py-14 text-sm2 text-warn-ink">
        {label}读取失败：{errorBody(query.error)}
        <ErrorHint error={query.error} />
      </div>
    )
  }
  const workpack = query.data
  if (!workpack) return null
  if (!Array.isArray(workpack.groups) || !Array.isArray(workpack.chapter_ordinals)) {
    return (
      <div className="border-b border-warn-line bg-warn-bg px-20 py-14 text-sm2 text-warn-ink">
        {label}响应形状不完整，无法安全呈现；未据此生成任何结论或动作。
      </div>
    )
  }
  const suspended = workpack.disposition === 'suspended'

  return (
    <section
      aria-label={label}
      className="flex flex-col gap-10 border-b border-line-row bg-sunken-2 px-20 py-14"
    >
      <div className="flex flex-wrap items-baseline gap-8">
        <span className="text-body font-semibold text-ink-2">{label}</span>
        <Mono className="rounded-tag bg-card px-7 py-2 text-micro">v{workpack.v}</Mono>
        <span className="text-tag text-ink-4">
          异常优先，只读呈现；不预填裁决、不自动接受、不在此发起作业
        </span>
        <span className={workpack.disposition === 'already_current'
          ? 'rounded-tag bg-gate-ok-bg px-8 py-2 text-tag font-semibold text-gate-ok-ink'
          : suspended
            ? 'rounded-tag border border-warn-line bg-warn-bg px-8 py-2 text-tag font-semibold text-warn-ink'
            : 'rounded-tag bg-inset px-8 py-2 text-tag text-ink-3'}>
          {workpack.disposition === 'already_current'
            ? '同一当前工作包已批准 · 默认跳过重复人工整读'
            : suspended
              ? 'raw 审阅已记录 · 缺验证权威，暂停下游授权且无需重复整读'
              : '当前工作包需要审阅'}
        </span>
      </div>
      <div className="flex min-w-0 flex-col gap-3 text-tag text-ink-4">
        {workpack.candidate_sha256 ? (
          <>
            <span>Stage4A 候选 SHA-256</span>
            <Mono className="break-all text-mini text-ink-2">{workpack.candidate_sha256}</Mono>
          </>
        ) : null}
        <span>工作包主体 SHA-256</span>
        <Mono className="break-all text-mini text-ink-2">{workpack.subject_sha256}</Mono>
        {workpack.current_decision_sha256 ? (
          <>
            <span>当前决定 SHA-256</span>
            <Mono className="break-all text-mini text-ink-2">
              {workpack.current_decision_sha256}
            </Mono>
          </>
        ) : null}
        <span>覆盖章节：{workpack.chapter_ordinals.join('、') || '（服务端给出空覆盖）'}</span>
      </div>

      <div className="flex flex-col gap-8">
        {workpack.groups.map((group) => (
          <section
            key={group.group}
            aria-label={GROUP_LABEL[group.group]}
            className="overflow-hidden rounded-btn border border-line-row bg-card"
          >
            <div className="flex flex-wrap items-baseline gap-8 border-b border-line-row px-12 py-9">
              <span className="text-sm2 font-semibold text-ink-2">{GROUP_LABEL[group.group]}</span>
              <Mono className="text-micro text-ink-4">{group.group}</Mono>
              <span className="text-tag text-ink-4">{group.scanned}</span>
              <span className="ml-auto text-tag text-ink-4">{group.items.length} 项</span>
            </div>
            {group.items.length ? (
              <div className="flex flex-col divide-y divide-line-row">
                {group.items.map((item, index) => (
                  <article
                    key={item.code + ':' + (item.chapter_ordinal ?? 'book') + ':' + index}
                    className="grid gap-8 px-12 py-10 text-sm2"
                    style={{ gridTemplateColumns: 'minmax(0,0.75fr) minmax(0,1.5fr)' }}
                  >
                    <div className="flex min-w-0 flex-col gap-4">
                      <div className="flex flex-wrap items-center gap-6">
                        <Mono className="font-semibold text-ink-2">{item.code}</Mono>
                        <Mono className="rounded-tag bg-inset px-6 py-1 text-micro text-ink-3">
                          {EFFECT_LABEL[item.effect]} · {item.effect}
                        </Mono>
                      </div>
                      <span className="text-tag text-ink-4">
                        {item.chapter_ordinal === null ? '全书' : '第 ' + item.chapter_ordinal + ' 章'}
                        {item.rule ? ' · ' + item.rule : ''}
                      </span>
                      <span className="text-tag text-ink-4">
                        sev：{item.sev ?? '（无）'} · status：{item.status ?? '（无）'}
                      </span>
                      {item.repair_owner ? (
                        <span className="text-tag text-ink-4">repair_owner：{item.repair_owner}</span>
                      ) : null}
                    </div>
                    <div className="flex min-w-0 flex-col gap-5 text-ink-2">
                      <span className="prose-cn">{item.detail || '（服务端未给补充说明）'}</span>
                      <span className="text-micro text-ink-4">
                        scene_refs：{item.scene_refs.join('、') || '（无）'}
                      </span>
                      <span className="text-micro text-ink-4">
                        refs：{item.refs.join('、') || '（无）'}
                      </span>
                      <span className="text-micro text-ink-4">
                        服务端允许入口：{item.allowed_actions.join('、') || '（无；只读/披露项不造动作）'}
                      </span>
                      <span className="text-micro text-ink-4">
                        当前章变化：{item.changed ? '是' : '否'} · 历史 accept 可复用：
                        {item.historical_accept_reusable ? '是' : '否'}
                      </span>
                      {item.source_decision_sha256 ? (
                        <span className="text-micro text-ink-4">
                          历史决定 SHA-256：<Mono className="break-all">{item.source_decision_sha256}</Mono>
                        </span>
                      ) : null}
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <div className="px-12 py-9 text-tag text-ink-4">
                本组 0 项；扫描口径以上方 scanned 为准，不把空数组翻译成“全部通过”。
              </div>
            )}
          </section>
        ))}
      </div>
    </section>
  )
}
