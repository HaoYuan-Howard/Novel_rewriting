/**
 * 闸门 —— 流程的核心，不是装饰。
 *
 *   stage0 剔番外 ─► stage1 切分 ─► stage2（全局设定 + 原文↔骨架，两屏都要看过）
 *                   ─► stage4 计划 ─► Stage5 写作授权 ─► stage5 ─► stage6 ─► stage7
 *
 * 文案与禁用条件源自历史原型 gateVals()，现役语义见 web/DELIVERY.md（§3 路由 / §7 UI 硬规则）。
 *
 * 这张表只被 components/gate/useGateBar.ts 消费。等后端接管这套工作流
 * （approve 返回新记录 + 它发起的作业），删这一个文件即可。
 *
 * 注意：这**不是**「前端做判定」。判定指的是 badge / paid / freshness / sev / passed，
 * 那些全部由服务端算好下发。这里是人工验收的流程本身，brief 自己画了这张图。
 */
import type { ApprovalDoc, GateKey } from '@/types/approvals'
import type { ScreenName } from './vocabulary'

export interface GateChipView {
  ok: boolean
  label: string
}

export interface GateView {
  key: GateKey
  done: boolean
  /** 前置没满足时按钮禁用。锁必须是真的。 */
  blocked: boolean
  title: string
  note: string
  chips: GateChipView[]
  btn: string
  /** 禁用时说明缺什么 */
  blockedReason: string
  doneNote: string | null
}

/** 屏 → 它守着哪一道闸门 */
export const SCREEN_GATE: Partial<Record<ScreenName, GateKey>> = {
  stage1: 's1',
  read: 's2',
  global: 's2',
  stage4: 's4',
  stage5: 's5',
  stage6: 's6',
  stage7: 's7',
}

export function resolveGate(screen: ScreenName, doc: ApprovalDoc): GateView | null {
  const g = doc.gate
  const seen = doc.seen
  const key = SCREEN_GATE[screen]
  if (!key) return null
  const done = g[key]

  switch (key) {
    case 's1':
      return {
        key,
        done,
        blocked: !g.s0,
        title: done ? '切分已确认' : g.s0 ? '这一刀切对了吗？' : '番外还没剔，切了也要重切',
        note: '确认后发起 stage2（全局设定 + 逐章骨架）。机械风格由 Stage5/6 从 Stage1 文本即时计算。',
        chips: [{ ok: g.s0, label: 'stage0 剔番外' }],
        btn: done ? '已确认' : g.s0 ? '确认切分' : '先过 stage0 剔除番外',
        blockedReason: 'stage0 还没确认删除范围。番外没剔就切分，切完还要重切。',
        doneNote: done
          ? 'stage2 已发起；后续机械风格无需单独产物或人工确认。'
          : null,
      }

    case 's2': {
      const both = seen.read && seen.global
      return {
        key,
        done,
        blocked: !both,
        title: done ? 'stage2 已确认' : 'stage2 两屏都要过一遍',
        note: 'stage2 = 全局设定 + 逐章骨架。确认后发起 stage4 重构计划。',
        chips: [
          { ok: seen.global, label: '全局设定' },
          { ok: seen.read, label: '原文↔骨架' },
        ],
        btn: done ? '已确认' : both ? '确认 stage2，启动 stage4' : '两屏都看过才能确认',
        blockedReason: 'stage2 有两屏产物。只看过一屏就确认，等于替另一屏签了字。',
        doneNote: done ? 'stage4 已发起。' : null,
      }
    }

    case 's4':
      return {
        key,
        done,
        blocked: false,
        title: done ? '计划已确认' : '这份重构计划能写吗？',
        note: '逐章接受当前计划后，可以授权 Stage5 全书闭卷写作；机械风格从 Stage1 即时计算。',
        chips: [],
        btn: done ? '已确认' : '确认计划',
        blockedReason: '',
        doneNote: done ? '计划已确认 → 可以授权 Stage5 全书闭卷写作。' : null,
      }

    case 's5': {
      const ready = g.s4 && doc.writing
      return {
        key,
        done,
        blocked: !ready,
        title: done ? '成稿已确认' : ready ? '这些章可以定稿了吗？' : '上游还没齐，先别定稿',
        note: ready
          ? '判据在左、成稿在右，改完再确认。确认后发起 stage6 开篇文案。'
          : '成稿必须建立在当前已确认的 Stage4 计划与全书写作授权上。',
        chips: [
          { ok: g.s4, label: 'stage4 计划' },
          { ok: doc.writing, label: doc.writing ? '全书写作已授权' : '全书写作未授权' },
        ],
        btn: done ? '已确认' : ready ? '确认成稿，启动 stage6' : '等计划确认与写作授权',
        blockedReason: '当前计划或全书写作授权尚未成立。',
        doneNote: done ? 'stage6 已发起。' : null,
      }
    }

    case 's6':
      return {
        key,
        done,
        blocked: !g.s5,
        title: done ? '文案已确认' : g.s5 ? '开篇文案定哪一版？' : '成稿没确认，文案先别定',
        note: g.s5
          ? '确认后发起 stage7 人名替换。'
          : '文案是给成稿写的招牌。成稿还在改，文案定了也要重来。',
        chips: [{ ok: g.s5, label: 'stage5 成稿' }],
        btn: done ? '已确认' : g.s5 ? '确认文案，启动 stage7' : '等 stage5 成稿确认',
        blockedReason: '成稿还没确认。文案是给成稿写的招牌，成稿变了文案也要重来。',
        doneNote: done ? 'stage7 已发起。' : null,
      }

    case 's7':
      return {
        key,
        done,
        blocked: !g.s6,
        title: done ? '已成书' : g.s6 ? '人名替换对了就能成书' : '文案没确认，还不能成书',
        note: g.s6
          ? '确认后按原书结构拼装（文案 + 正文各章），落到 out/ 下的成书 txt。'
          : '成书要把文案和正文一起拼进去。stage6 文案没确认，拼出来的是半本。',
        chips: [
          { ok: g.s5, label: 'stage5 成稿' },
          { ok: g.s6, label: 'stage6 文案' },
        ],
        btn: done ? '已成书' : g.s6 ? '确认并成书' : '等 stage6 文案确认',
        blockedReason: 'stage6 文案没确认，拼出来的是半本。',
        doneNote: done ? '已写到 out/rebuilt.txt——语料库那屏能看到它的落盘时间。' : null,
      }

    default:
      return null
  }
}

/**
 * 左导航的锁。上游没确认时下游禁用 ——
 * 「导航上写着锁、屏上却能点」是评审揪出来的真实缺陷，别复现。
 */
export const RAIL_LOCK: Partial<Record<ScreenName, { need: GateKey; why: string }>> = {
  stage1: { need: 's0', why: '先过 stage0：番外还没剔，切了也要重切' },
  stage6: { need: 's5', why: '先确认 stage5 成稿：文案是给成稿写的招牌' },
  stage7: { need: 's6', why: '先确认 stage6 文案：不然拼出来的是半本' },
}

/** gate key → 一句人话。左导航锁定项右侧的小字用它，别在屏里散落硬编码。 */
export const GATE_LABEL: Record<GateKey, string> = {
  s0: 'stage0 剔除番外',
  s1: 'stage1 切分确认',
  s2: 'stage2 两屏审阅',
  s4: 'stage4 计划确认',
  s5: 'stage5 成稿确认',
  s6: 'stage6 文案确认',
  s7: 'stage7 人名定稿',
}

/** 锁定项右侧的常驻小字：「先过 stage0 剔除番外」 */
export function gateNeedText(key: GateKey): string {
  return '先过 ' + GATE_LABEL[key]
}

/** 左导航上打 ✓ 的依据 */
export const RAIL_GATE: Partial<Record<ScreenName, GateKey>> = {
  stage0: 's0',
  stage1: 's1',
  read: 's2',
  global: 's2',
  stage4: 's4',
  stage5: 's5',
  stage6: 's6',
  stage7: 's7',
}
