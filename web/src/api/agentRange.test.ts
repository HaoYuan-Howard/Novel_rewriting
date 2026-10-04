import { afterEach, describe, expect, it, vi } from 'vitest'
import { realApi } from './endpoints'
import { qk } from './queryKeys'
import { READY_PROFILES } from '@/test/agent'
import type { AgentRunRequest } from '@/types/agent'

afterEach(() => vi.restoreAllMocks())

describe('受管运行范围的 HTTP 契约', () => {
  it('续跑省略范围与显式 null 在传输和缓存中保持不同', async () => {
    const request: AgentRunRequest = {
      sid: '42', authorized: true, egress_authorized: true, profiles: READY_PROFILES,
      continuation: { run_id: 'old-run', source_sha256: '1'.repeat(64) },
    }
    const response = { argv: ['run'], prompt: '范围预览', authorized: true,
      egress_authorized: true, profiles: READY_PROFILES, continuation: request.continuation,
      pause_before_node: 'str06.stage5.hook' }
    const send = vi.spyOn(globalThis, 'fetch').mockImplementation(async () => new Response(
      JSON.stringify(response), { status: 200, headers: { 'Content-Type': 'application/json' } }))
    const inherited = await realApi.previewAgentRun(request)
    expect(inherited.pause_before_node).toBe('str06.stage5.hook')
    expect(JSON.parse(send.mock.calls[0]![1]!.body as string)).not.toHaveProperty('pause_before_node')
    const expanded = { ...request, pause_before_node: null }
    await realApi.previewAgentRun(expanded)
    expect(JSON.parse(send.mock.calls[1]![1]!.body as string)).toHaveProperty('pause_before_node', null)
    expect(qk.agentRunPreview(request)).not.toEqual(qk.agentRunPreview(expanded))
    await realApi.startAgentRun(request)
    expect(JSON.parse(send.mock.calls[2]![1]!.body as string)).toEqual(request)
  })
})
