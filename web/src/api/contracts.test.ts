import { describe, expect, it, vi } from 'vitest'
import { qk } from './queryKeys'
import { adaptNovel, realApi, splitLog } from './endpoints'
import { projectGlobalSpoilers, SPOILER_PLACEHOLDER } from './spoilers'
import type { GlobalFactEvidence, Stage2Global } from '@/types/stages'
import type { BackendJobRecord } from '@/types/jobs'
import { READY_PROFILES } from '@/test/agent'
import { mockApi } from './mock'

function evidence(text: string): GlobalFactEvidence {
  return {
    chapter_ordinal: 2,
    source: { text, start: 3, end: 3 + text.length },
  }
}

function globalFixture(): Stage2Global {
  return {
    sid: 's',
    title: 't',
    setting: {
      时代背景: '古代',
      era_evidence: evidence('古代证据'),
      世界观: '现实世界',
      worldview_evidence: evidence('世界观证据'),
    },
    characters: [
      {
        char_id: 'c1',
        canonical: '甲',
        name_distinctiveness: 'high',
        role: '主角',
        role_evidence: evidence('主角证据'),
        fate: '真实 fate',
        fate_evidence: evidence('真实 fate 证据'),
        address: [],
      },
    ],
    relations: [],
    world_rules: [],
    secrets: [
      {
        secret_id: 'secret-1',
        fact: '真实秘密',
        fact_evidence: evidence('真实秘密证据'),
        knowledge_events: [],
        setup_kind: 'foreshadowed',
        setup_chapters: [1],
        first_reveal_chapter: 4,
      },
    ],
    source_blurb: {
      facts: [{ kind: 'supported', claim_id: 'hook_001', claim: '真实文案事实',
        evidence: evidence('真实文案事实证据') }],
      opening: { kind: 'proposed', through_chapter: 2, rationale: '开篇事件横跨前两章',
        boundary_evidence: evidence('终点证据') },
    },
    unresolved_refs: 0,
    invalid_exists: false,
    freshness: 'fresh',
    reason: '',
    ghosts: [],
    _provenance: { root_clean_sha1: '0000000000000000000000000000000000000000' },
  }
}

function backendJobRecord(node: string, flags: string[]): BackendJobRecord {
  return {
    job_id: 'job-' + node,
    start: {
      sid: 'book /1',
      node,
      flags,
      steps: [{
        argv: ['python', '-m', 'novel.cli', node, ...flags],
        cwd: 'E:/repo',
        label: node,
        paid: false,
        writes: '人工审计账',
        note: null,
        executor_backend: null,
        egress_authorized: null,
      }],
      python: 'python',
      code: 'abc123',
      paid: false,
      writes: true,
      executor_backend: null,
      egress_authorized: null,
    },
    started: 1,
    ended: 2,
    exit_code: 0,
    state: 'ok',
    steps_started: 1,
    note: null,
  }
}

describe('request-shaped cache and diagnostics', () => {
  it('sends the full typed Stage4 decision through the one real HTTP seam', async () => {
    const subject = 'a'.repeat(64)
    const request = {
      actor: 'reviewer-a',
      reason: '已逐章核对结构',
      review_token: 's4sr1_' + subject,
      chapters: [{ output_ordinal: 1, decision: 'accept', reason: '本章可执行' }],
    }
    const response = {
      v: 6,
      sid: 'book /1',
      gate: {
        s0: true, s1: true, s2: true,
        s4: false, s5: false, s6: false, s7: false,
      },
      seen: { read: true, global: true },
      writing: false,
      design_review: {
        state: 'missing', candidate_sha256: 'c'.repeat(64),
        aggregate_subject_sha256: 'd'.repeat(64), current_decision: null, history: [],
        binding_infeasibility: null,
        preflight: null,
        repair_capability: {
          repairable: true, reason_code: 'ready',
          chapters: [{ output_ordinal: 1, committed_rounds: 0, request_repair_available: true }],
        },
      },
      structure: {
        state: 'missing', current_subject_sha256: subject,
        current_decision: null, history: [], binding_concerns: [],
      },
      spawns: {},
    }
    const send = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(
      JSON.stringify(response),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    ))

    await expect(realApi.approveStructure('book /1', request)).resolves.toEqual(response)
    expect(send).toHaveBeenCalledWith(
      '/api/novels/book%20%2F1/approvals/structure',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'X-Novel-UI': '1' }),
        body: JSON.stringify(request),
      }),
    )
  })

  it('uses the one real HTTP seam for the Stage4A template session and exact apply body', async () => {
    const query = {
      actor: 'reviewer-a',
      reviewer_kind: 'human',
      review_run_id: 'run-01',
      reason: '核对 A 候选与证据',
    }
    const request = await mockApi.getDesignReviewTemplate('route-book', query)
    const confirmed = {
      ...request,
      strategy: {
        ...request.strategy,
        placeholder: false,
        template_token: null,
        reason: '已核对策略，要求重规划',
      },
      chapters: request.chapters.map((chapter) => ({
        ...chapter,
        placeholder: false,
        template_token: null,
        reason: '已核对本章，要求定向返修',
      })),
    }
    const response = {
      v: 6,
      sid: 'book /1',
      gate: {
        s0: true, s1: true, s2: true,
        s4: false, s5: false, s6: false, s7: false,
      },
      seen: { read: true, global: true },
      writing: false,
      design_review: {
        state: 'strategy_replan_requested',
        candidate_sha256: confirmed.candidate_sha256,
        aggregate_subject_sha256: confirmed.aggregate_subject_sha256,
        current_decision: null,
        history: [],
        binding_infeasibility: null,
        preflight: null,
        repair_capability: confirmed.repair_capability,
      },
      structure: {
        state: 'unavailable', current_subject_sha256: null,
        current_decision: null, history: [], binding_concerns: [],
      },
      spawns: {},
    }
    const send = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(
        JSON.stringify(request),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))
      .mockResolvedValueOnce(new Response(
        JSON.stringify(response),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))

    await expect(realApi.getDesignReviewTemplate('book /1', query)).resolves.toEqual(request)
    expect(request.strategy).toMatchObject({ placeholder: true })
    expect(request.chapters[0]).toMatchObject({ placeholder: true })
    const templateCall = send.mock.calls[0]
    expect(templateCall?.[1]).toBeUndefined()
    const templateUrl = new URL(String(templateCall?.[0]), 'http://test.local')
    expect(templateUrl.pathname).toBe('/api/novels/book%20%2F1/approvals/design/template')
    expect(Object.fromEntries(templateUrl.searchParams.entries())).toEqual(query)

    await expect(realApi.approveDesignReview('book /1', confirmed)).resolves.toEqual(response)
    expect(send).toHaveBeenNthCalledWith(
      2,
      '/api/novels/book%20%2F1/approvals/design',
      expect.objectContaining({
        method: 'POST',
        headers: expect.objectContaining({ 'X-Novel-UI': '1' }),
        body: JSON.stringify(confirmed),
      }),
    )
  })

  it('sends Stage5 edit through read-only preview then exact audited double-SHA apply', async () => {
    const segments = [{ slot: 's01', text: '改稿正文。' }]
    const preview = {
      output_ordinal: 3,
      current_chapter_sha256: '1'.repeat(64),
      preview_subject_sha256: '2'.repeat(64),
      normalized_segments: segments,
      findings: [],
      current_hard_findings: [],
      introduced_hard_findings: [],
      resolved_hard_findings: [],
      required_confirmation: 'formal',
    }
    const apply = {
      segments: preview.normalized_segments,
      actor: 'reviewer-a',
      reason: '已核对本次改稿',
      current_chapter_sha256: preview.current_chapter_sha256,
      preview_subject_sha256: preview.preview_subject_sha256,
      confirmation: 'formal' as const,
    }
    const send = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(
        JSON.stringify(preview),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))
      .mockResolvedValueOnce(new Response(
        JSON.stringify(backendJobRecord('stage5.write', ['--apply-edit'])),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))

    await expect(realApi.previewDraftEdit('book /1', 3, { segments })).resolves.toEqual(preview)
    expect(send).toHaveBeenNthCalledWith(
      1,
      '/api/novels/book%20%2F1/review/3/edit/preview',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ segments }) }),
    )
    await expect(realApi.saveDraftEdit('book /1', 3, apply)).resolves.toMatchObject({
      id: 'job-stage5.write',
      kind: 'stage5.write',
      state: 'ok',
    })
    expect(send).toHaveBeenNthCalledWith(
      2,
      '/api/novels/book%20%2F1/review/3/edit',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(apply) }),
    )
  })

  it('uses the Stage5 closure template/apply endpoints and batch trim body without recomputing SHA', async () => {
    const template = {
      actor: '',
      reason: '',
      review_token: 's5cr1_' + '3'.repeat(64),
      workpack_sha256: '3'.repeat(64),
      review_sha256: '4'.repeat(64),
      must_appear: [],
      group_acks: [],
    }
    const closure = {
      actor: 'commander-a',
      reason: '全书已复核',
      review_token: template.review_token,
      must_appear: [],
      group_acks: [],
    }
    const trim = {
      actor: 'commander-a',
      reason: '逐章保留 pretrim',
      items: [{
        output_ordinal: 3,
        audit_sha256: '5'.repeat(64),
        chapter_sha256: '6'.repeat(64),
        reason: '',
      }],
    }
    const response = { marker: 'approval-doc' }
    const send = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(new Response(
        JSON.stringify(template),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))
      .mockResolvedValueOnce(new Response(
        JSON.stringify(response),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))
      .mockResolvedValueOnce(new Response(
        JSON.stringify(backendJobRecord('stage5.review', ['--retain-pretrim-batch'])),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ))

    await expect(realApi.getStage5ClosureTemplate('book /1')).resolves.toEqual(template)
    expect(send).toHaveBeenNthCalledWith(
      1,
      '/api/novels/book%20%2F1/approvals/stage5/template',
      undefined,
    )
    await expect(realApi.approveStage5Closure('book /1', closure)).resolves.toEqual(response)
    expect(send).toHaveBeenNthCalledWith(
      2,
      '/api/novels/book%20%2F1/approvals/stage5',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(closure) }),
    )
    await expect(realApi.retainPretrimBatch('book /1', trim)).resolves.toMatchObject({
      kind: 'stage5.review',
      state: 'ok',
    })
    expect(send).toHaveBeenNthCalledWith(
      3,
      '/api/novels/book%20%2F1/stage5/trim/retain-pretrim-batch',
      expect.objectContaining({ method: 'POST', body: JSON.stringify(trim) }),
    )
  })

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
      drivers: [],
      legacy_providers: [],
      executor_nodes: [],
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
  it('masks hook evidence and failed locators while preserving their distinct source state', () => {
    const raw = globalFixture()
    raw.source_blurb.facts = [{
      kind: 'hook_supported', claim_id: 'hook_001', claim: '钩子独有秘密',
      hook_evidence: { source: { text: '钩子证据', start: 0, end: 4 }, unresolved_locator: null },
    }, {
      kind: 'hook_supported', claim_id: 'hook_002', claim: '待定位的钩子主张',
      hook_evidence: { source: null, unresolved_locator: '不能暴露的失败定位' },
    }]
    const hidden = projectGlobalSpoilers(raw, false)
    expect(hidden.source_blurb.facts[0]).toMatchObject({
      kind: 'hook_supported', claim: SPOILER_PLACEHOLDER,
      hook_evidence: { source: { text: SPOILER_PLACEHOLDER }, unresolved_locator: null },
    })
    expect(hidden.source_blurb.facts[1]).toMatchObject({
      hook_evidence: { source: null, unresolved_locator: SPOILER_PLACEHOLDER },
    })
    expect(JSON.stringify(hidden)).not.toContain('钩子独有秘密')
    expect(JSON.stringify(hidden)).not.toContain('不能暴露的失败定位')
    expect(projectGlobalSpoilers(raw, true)).toBe(raw)
  })

  it('projects all four configured paths before screens consume them', () => {
    const raw = globalFixture()
    const hidden = projectGlobalSpoilers(raw, false)
    expect(hidden.characters[0]?.fate).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.characters[0]?.fate_evidence?.source?.text).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.secrets).toHaveLength(1)
    expect(hidden.secrets[0]?.fact).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.source_blurb.facts[0]?.claim).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.source_blurb.opening.kind === 'proposed' && hidden.source_blurb.opening.rationale).toBe(SPOILER_PLACEHOLDER)
    expect(hidden.source_blurb.facts[0]?.kind === 'supported' && hidden.source_blurb.facts[0].evidence.source?.text).toBe(
      SPOILER_PLACEHOLDER,
    )
    expect(JSON.stringify(hidden)).not.toContain('真实秘密')
    expect(JSON.stringify(hidden)).not.toContain('真实 fate 证据')
    expect(JSON.stringify(hidden)).not.toContain('真实弧线证据')
    expect(projectGlobalSpoilers(raw, true)).toBe(raw)
  })
})
