import { useState } from 'react'
import { useApplyWorkUnitGrant, useWorkUnitGrant } from '@/hooks/useWorkUnitGrant'
import { ErrorHint } from '@/components/state/States'
import { LinkButton } from '@/components/ui/ActionButton'
import type { WorkUnitGrantRequest } from '@/types/run'

export function WorkUnitBudgetGrant({ sid, unitId }: { sid: string; unitId: string }) {
  const [open, setOpen] = useState(false)
  const [additional, setAdditional] = useState(1)
  const [reason, setReason] = useState('')
  const [intent, setIntent] = useState<WorkUnitGrantRequest | null>(null)
  const preview = useWorkUnitGrant(sid, unitId, additional, open)
  const apply = useApplyWorkUnitGrant(sid, unitId)
  const current = preview.data
  const confirmed = intent ? current?.history?.find((row) => row.intent_id === intent.intent_id) : null
  return (
    <div className="py-8">
      <LinkButton onClick={() => setOpen((value) => !value)}>{open ? '收起额度详情' : '查看 / 追加额度'}</LinkButton>
      {open ? <div className="mt-8 flex min-w-[260px] flex-col gap-8 rounded-block border border-line p-12 text-mini">
        {preview.isPending ? <p>正在核验原调用、停止证明与当前主体…</p> : null}
        <ErrorHint error={preview.error} />
        {current ? <>
          <p className="m-0">{current.reason}</p>
          {current.unit ? <p className="m-0">基础 {current.unit.content_base_limit ?? '未知'} 次，已追加 {(current.unit.content_granted ?? 0) - (current.unit.content_refunded ?? 0)} 次{current.unit.content_refunded ? `，升级返还 ${current.unit.content_refunded} 次` : ''}；累计 {current.unit.content_used} / {current.unit.content_limit}，剩余 {current.unit.content_remaining} 次。</p> : null}
          {current.costs ? <p className="m-0">累计 token 成本：{current.costs.total_tokens ?? '未知'}；模型调用 {current.costs.calls} 次，失败 {current.costs.failed_calls} 次，缺用量 {current.costs.missing_usage_calls} 次。</p> : <p className="m-0">累计成本未知。</p>}
          {current.history?.map((grant) => <p className="m-0" key={grant.intent_id}>人工追加 {grant.additional} 次：{grant.reason}</p>)}
          <label>本次追加
            <select aria-label="本次追加次数" value={additional} disabled={apply.isPending}
              onChange={(event) => { setAdditional(Number(event.target.value)); setIntent(null) }}>
              {current.allowed_additions.map((value) => <option key={value} value={value}>{value} 次</option>)}
            </select>
          </label>
          <label>追加理由
            <textarea aria-label="追加理由" value={reason} maxLength={2000} disabled={apply.isPending}
              className="w-full" onChange={(event) => { setReason(event.target.value); setIntent(null) }} />
          </label>
          <p className="m-0">仅追加本工作单元；后续模型调用仍需独立付费与正文外发授权。</p>
          <LinkButton disabled={!!confirmed || (!intent && (!current.available || !current.preview_token)) || !reason.trim() || preview.isFetching || apply.isPending}
            onClick={() => {
              if (!intent && !current.preview_token) return
              // 网络响应不确定时重试同一不可变意图；修改次数/理由才建立新意图。
              const request = intent ?? { preview_token: current.preview_token!, intent_id: crypto.randomUUID(), additional, reason: reason.trim() }
              setIntent(request)
              apply.mutate(request)
            }}>
            {apply.isPending ? '核验并追加中…' : confirmed ? '本次授权已生效' : intent && apply.isError ? '核验并重试同一授权' : `明确追加 ${additional} 次`}
          </LinkButton>
          {intent && !confirmed && apply.isError ? <LinkButton onClick={() => { setIntent(null); apply.reset(); void preview.refetch() }}>重新预览并建立新授权</LinkButton> : null}
        </> : null}
        {apply.data ? <p className="m-0">已追加 {apply.data.grant.additional} 次，原轮次与成本账均保留。</p> : null}
        <ErrorHint error={apply.error} />
      </div> : null}
    </div>
  )
}
