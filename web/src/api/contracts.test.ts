import { describe, expect, it, vi } from 'vitest'
import { qk } from './queryKeys'
import { adaptNovel, realApi, splitLog } from './endpoints'
import { READY_PROFILES } from '@/test/agent'
import { mockApi, SAMPLE_SID } from './mock'

describe('request-shaped cache and diagnostics', () => {
  it('人工闸写面已从 Api 退役，本地历史审批键不构成授权', async () => {
    // 人工逐 Stage 过闸模式退役后数据缝上不再有审批/重置类方法；mock 也不再读
    // 本地审批账 ⇒ 浏览器里遗留的旧 gate 记录没有任何读者，不能冒充授权。
    // 名字不写死：按形状查，旧链缝哪天改名回来也当场红。
    const retiredShaped = Object.keys(mockApi).filter((key) =>
      /approv|reset/i.test(key))
    expect(retiredShaped).toEqual([])
    const key = 'nrc.v1.approvals.' + SAMPLE_SID
    localStorage.setItem(key, JSON.stringify({ v: 6, gate: { s4: true }, writing: true }))
    await expect(mockApi.getNovels()).resolves.toMatchObject({
      novels: expect.any(Array),
    })
    localStorage.removeItem(key)
  })

  it('includes runtime chapter args in preview cache identity', () => {
    const base = { kind: 'stage0.remove', label: '写', extra: null, sid: 's' }
    expect(qk.jobPreview({ ...base, args: ['--source', 'a.txt'] })).not.toEqual(
      qk.jobPreview({ ...base, args: ['--source', 'b.txt'] }),
    )
  })

  it('uses the whole agent request, including nested overrides, as preview cache identity', () => {
    const base = {
      sid: 's',
      authorized: false,
      egress_authorized: true,
      profiles: READY_PROFILES,
    }
    expect(qk.agentRunPreview(base)).not.toEqual(qk.agentRunPreview({
      ...base,
      profiles: {
        ...base.profiles,
        executor_overrides: {
          'node.dynamic': { ...base.profiles.executor_default },
        },
      },
    }))
    expect(qk.agentRunPreview(base)).not.toEqual(qk.agentRunPreview({
      ...base,
      egress_authorized: false,
    }))
  })

  it('gets agent capabilities through the single HTTP seam', async () => {
    const capabilities = {
      legacy_providers: [],
      executor_nodes: [],
      legacy_model_pins: {},
      concurrency: {
        nodes: [], node_choices: [], default_node: 4,
        defaults: { node_overrides: {}, per_book: 4, per_provider: 8 },
        total_min: 1, total_max: 200, scope: 'output_root',
      },
      default_profiles: null,
    }
    const send = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify(capabilities),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ))

    await expect(realApi.getAgentCapabilities()).resolves.toEqual(capabilities)
    expect(send).toHaveBeenCalledWith('/api/agent/capabilities', undefined)
  })

  it('takes the dynamic chapter count from the stage1-backed overview', () => {
    const adapted = adaptNovel({
      sid: 's',
      title: 't',
      dirname: 's',
      chapters: 11,
      stages: [],
      summary: {
        verdict: 'pending',
        text: '',
        detail: '',
        artifact_count: 0,
        latest_mtime: null,
      },
    })
    expect(adapted.chapters).toBe(11)
  })

  it('keeps formal and active INVALID freshness in separate adapter fields', () => {
    const adapted = adaptNovel({
      sid: 's',
      title: 't',
      dirname: 's',
      chapters: 1,
      stages: [{
        stage: 'stage2',
        done: false,
        chapters_done: [],
        reviewable: true,
        artifacts: [{
          rel: 'stage2/chapter_blueprints.json',
          exists: true,
          bytes: 1,
          mtime: 1,
          freshness: 'fresh',
          display_freshness: 'fresh',
          condition: 'invalid',
          reason: 'match',
          ghosts: [],
          invalid: true,
          invalid_freshness: 'unknown',
          invalid_reason: 'consumed_unresolved',
          readable: true,
        }],
      }],
      summary: {
        verdict: 'pending',
        text: '',
        detail: '',
        artifact_count: 1,
        latest_mtime: 1,
      },
    })
    expect(adapted.stages.stage2?.[0]).toMatchObject({
      freshness: 'fresh',
      reason: 'match',
      invalid: true,
      invalid_freshness: 'unknown',
      invalid_reason: 'consumed_unresolved',
    })
  })

  it('keeps null, malformed, unknown and error diagnostics expandable', () => {
    const lines = splitLog(
      [
        '@observe null',
        '@observe {bad',
        '@observe {"event":"new_event","chapter":7,"detail":"chapter detail"}',
        '@observe {"event":"llm_attempt_finished","state":"error","attempt":1,"max_attempts":1,"purpose":"judge","error_kind":"Timeout","detail":"wire failed"}',
      ].join('\n'),
    )
    expect(lines).toHaveLength(4)
    expect(lines[0]?.kind).toBe('malformed')
    expect(lines[1]?.details).toHaveProperty('raw_payload')
    expect(lines[2]?.text).toContain('new_event')
    expect(lines[2]?.details).toMatchObject({ chapter: 7, detail: 'chapter detail' })
    expect(lines[3]?.text).toContain('error_kind=Timeout')
    expect(lines[3]?.details).toMatchObject({ detail: 'wire failed' })
  })

  // STR-06 切换（F8c）：旧链读屏缝（reading / stage4 / stage5 / review / inbox /
  // stage7 / book）与 stage2 层级复检随旧链整层退役，对应 HTTP 缝用例不再维护；
  // 层级复检取值面只剩 stage0/stage1，新链条目复检是节点级 --check-only。
  // 人工闸面（approvals / workpack / reset）同日退役，前端不再持本地审批账。
})
