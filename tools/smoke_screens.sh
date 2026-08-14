#!/usr/bin/env bash
# 批7 smoke：两本全链书 12 屏 API 级核对。只发 GET，零写盘。
set -u
BASE=http://127.0.0.1:8765/api
SIDS="${1:-7645085972736135960 7626951433820066622}"
fail=0

chk() { # chk <label> <url> [python 断言，d 为响应 JSON]
  local label="$1" url="$2" jq="${3:-True}"
  local body code
  body=$(curl -s -w '\n%{http_code}' "$BASE$url")
  code=$(echo "$body" | tail -1)
  body=$(echo "$body" | sed '$d')
  if [ "$code" != 200 ]; then
    echo "FAIL $label -> HTTP $code"; fail=1; return
  fi
  if ! echo "$body" | python -c "
import json,sys
d=json.load(sys.stdin)
ok=bool(eval(sys.argv[1]))
sys.exit(0 if ok else 1)
" "$jq" 2>/dev/null; then
    echo "FAIL $label -> 断言不成立: $jq"; fail=1; return
  fi
  echo "ok   $label"
}

for sid in $SIDS; do
  echo "===== $sid ====="
  chk "corpus 列表含本书" "/novels" "any(n['sid']=='$sid' for n in d['novels'])"
  chk "corpus 详情 8 层齐" "/novels/$sid" "len(d['stages'])==8"
  chk "stage0 报告" "/novels/$sid/stage0/report"
  chk "stage0 分段" "/novels/$sid/stage0/segments"
  chk "stage1 分段" "/novels/$sid/stage1/segments"
  chk "stage1 章目" "/novels/$sid/stage1/chapters" "len(d['chapters'])>0"
  chk "stage1 钩子文案" "/novels/$sid/stage1/hook_copy"
  chk "read 首章(长文本)" "/novels/$sid/reading/1" "sum(s.get('chars',0) for s in d.get('segments',[]))>100 or d.get('text_chars',0)>100"
  chk "global 设定" "/novels/$sid/stage2/global" "'characters' in d"
  chk "style 文风" "/novels/$sid/stage3/style"
  chk "depth stage4" "/novels/$sid/stage4/1"
  chk "stage5 材料" "/novels/$sid/stage5/1"
  chk "review 审判" "/novels/$sid/review/1" "'finding_counts' in d"
  chk "stage6 文案" "/novels/$sid/stage6" "'candidates' in d and 'freshness' in d"
  chk "stage7 人名(书级)" "/novels/$sid/stage7" "'book_txt' in d and 'freshness' in d"
  chk "stage7 人名(章级)" "/novels/$sid/stage7/1" "d['rows'] is not None"
  chk "inbox 待人工" "/novels/$sid/inbox"
  chk "approvals 闸门" "/novels/$sid/approvals"
  for k in stage0 stage1 stage2 stage3 stage4 stage5 stage6 stage7; do
    chk "check/$k rules[]" "/novels/$sid/check/$k" "isinstance(d.get('rules'), list)"
  done
done
echo "---"; [ $fail -eq 0 ] && echo "SMOKE PASS" || echo "SMOKE FAIL"
exit $fail
