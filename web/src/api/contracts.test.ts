import { describe, expect, it, vi } from 'vitest'
import { qk } from './queryKeys'
import { adaptNovel, realApi, splitLog } from './endpoints'
import { projectGlobalSpoilers, SPOILER_PLACEHOLDER } from './spoilers'
import type { Stage2Global } from '@/types/stages'
import { READY_PROFILES } from '@/test/agent'
import { mockApi, SAMPLE_SID, stage2CoreFixture } from './mock'

function globalFixture(): Stage2Global {
  const value = stage2CoreFixture().global
  const ref = 'ev_' + '9'.repeat(24)
  value.evidence_anchors[ref] = { chapter_ordinal: 2, start: 3, end: 9, text_sha256: '9'.repeat(64) }
  value.evidence_snippets[ref] = '秘密独有证据'
  value.arc_secrets = [{ secret_id: 'secret_01', fact: '真实秘密', fact_ref: null,
    evidence_refs: [ref], setup_chapters: [1], revealed_from: 4 }]
  return value
}


describe('request-shaped cache and diagnostics', () => {
  it('历史事实样例不再提供当前阅读、分场或蓝图检查结论', async () => {
    await expect(mockApi.getReading(SAMPLE_SID, 3)).rejects.toMatchObject({ status: 501 })
    await expect(mockApi.getStage4(SAMPLE_SID, 3)).rejects.toMatchObject({ status: 501 })
    await expect(mockApi.getCheck(SAMPLE_SID, 'stage2')).rejects.toMatchObject({ status: 501 })
  })

  it.each([6, 7, 8])('旧 v%s 本地审批不能升级为新契约接受或写作授权', async (version) => {
    const key = 'nrc.v1.approvals.' + SAMPLE_SID
    localStorage.setItem(key, JSON.stringify({ v: version, gate: { s4: true }, writing: true }))
    const current = await mockApi.getApprovals(SAMPLE_SID)
    expect(current.v).toBe(9)
    expect(current.gate.s4).toBe(false)
    expect(current.writing).toBe(false)
    localStorage.removeItem(key)
  })

  // stage4 design/structure workpack、模板与 POST 写面已随 2026-09-15 全自动裁决退役；
  // 对应的两条 Stage4 人工审阅 HTTP 缝用例一并退役，Stage4 只剩只读审批投影。

  // Stage5 改稿 / 闭合 / batch trim 写面已退役；对应 HTTP 缝用例不再维护。

  it('includes runtime chapter args in preview cache identity', () => {
    const base = { kind: 'stage5.write', label: '写', extra: null, sid: 's' }
    expect(qk.jobPreview({ ...base, args: ['--chapters', '1'] })).not.toEqual(
      qk.jobPreview({ ...base, args: ['--chapters', '2'] }),
    )
  })

  it('requires the Stage5 Commander enums and publishes the exact edit preview/apply input surface', async () => {
    const enums = await mockApi.getEnums()
    expect(enums.stage5_must_appear_rulings).toEqual([
      'confirmed_present', 'accepted_exception',
    ])
    expect(enums.stage5_closure_groups).toEqual(['fact_landing', 'unver'])
    expect(enums.stage5_closure_states).toContain('review_required')
    expect(enums.job_kinds['stage5.write']?.flags).toEqual(expect.arrayContaining([
      '--preview-edit',
      '--apply-edit',
      '--current-chapter-sha256',
      '--preview-subject-sha256',
      '--edit-confirmation',
    ]))
    const preview = enums.actions.find((action) =>
      action.kind === 'stage5.write' && action.extra?.includes('--preview-edit'))
    expect(preview).toMatchObject({ paid: false, writes_disk: false, required_inputs: [] })
    const apply = enums.actions.find((action) =>
      action.kind === 'stage5.write' && action.extra?.includes('--apply-edit'))
    expect(apply?.required_inputs?.map((input) => input.flag)).toEqual([
      '--actor',
      '--reason',
      '--current-chapter-sha256',
      '--preview-subject-sha256',
      '--edit-confirmation',
    ])
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
        total_min: 1, total_max: 32, scope: 'output_root',
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
})

describe('display-only spoiler projection', () => {
  it('隐藏秘密的共享表引用与独有证据，同时保留其它事实的证据', () => {
    const raw = globalFixture()
    const publicRef = raw.setting.era_evidence_refs[0]
    const hidden = projectGlobalSpoilers(raw, false)
    expect(hidden.arc_secrets).toHaveLength(1)
    expect(hidden.arc_secrets[0]?.fact).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.arc_secrets[0]?.evidence_refs).toEqual([])
    expect(hidden.evidence_snippets[publicRef]).toBe(raw.evidence_snippets[publicRef])
    expect(JSON.stringify(hidden)).not.toContain('真实秘密')
    expect(JSON.stringify(hidden)).not.toContain('秘密独有证据')
    expect(projectGlobalSpoilers(raw, true)).toBe(raw)
  })

  it('展示遮蔽不改动原响应，空列表和隐藏态保持不同', () => {
    const raw = globalFixture()
    const before = structuredClone(raw)
    const hidden = projectGlobalSpoilers(raw, false)
    expect(raw).toEqual(before)
    expect(hidden.relations).toEqual(raw.relations)
    expect(hidden.world_rules).toEqual(raw.world_rules)
    raw.arc_secrets = []
    expect(projectGlobalSpoilers(raw, false).arc_secrets[0]?.fact).toBe(SPOILER_PLACEHOLDER)
    expect(projectGlobalSpoilers(raw, true).arc_secrets).toEqual([])
  })
})
