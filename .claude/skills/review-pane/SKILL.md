---
name: review-pane
description: herdr 새 창에 읽기 전용 리뷰 에이전트를 띄워 검토받고, 고친 뒤 같은 에이전트에게 재검토받는다. "리뷰어 pane", "리뷰 전담 에이전트"에 발동한다. 이 세션이 직접 리뷰하는 것은 code-review다.
---

# review-pane

2026-11-20 세션(`d2783f2c`)에서 리뷰어 세 번, 이 스킬을 만든 세션(`93c6af24`)에서 한 번 이 순서로 실행했다.

## 1. 권한 파일 복사

`references/reviewer-settings.json`을 스크래치패드로 복사한다. 필요하면 허용 명령만 더한다.
**원본은 `references/`다** — 스크래치패드 사본은 다음 세션에 없다.

- 허용: 읽기(Read·Grep·Glob), `git diff/log/show/status/blame/merge-base/rev-parse`, 테스트 실행
  (`swift build`, `swift run TokenTrackerSmokeTests`, `python3 -m unittest`), `jq`, `grep`, `sed -n`, `ls`, `wc`.
- 거부: Edit/Write, commit, push, switch, checkout, reset, stash, rebase, `gh`.
- `LC_ALL=... wc -m`처럼 환경 변수를 앞에 붙인 명령은 허용 목록에 걸리지 않는다.

## 2. 창 열기와 리뷰어 시작

`pane split`은 JSON을 돌려준다. 창 ID는 `result.pane.pane_id`에 있다.

```sh
SP=<이 세션의 스크래치패드>
cp .claude/skills/review-pane/references/reviewer-settings.json "$SP/"
P=$(herdr pane split --current --direction down --cwd "$PWD" --no-focus \
  | python3 -I -c "import json,sys; print(json.load(sys.stdin)['result']['pane']['pane_id'])")
herdr pane wait-output "$P" --regex '[%$#>❯] *$' --timeout 15000 >/dev/null 2>&1
herdr agent start reviewer --kind claude --pane "$P" --timeout 60000 -- \
  --permission-mode dontAsk --settings "$SP/reviewer-settings.json"
```

리뷰어 종류로 Codex(`--kind codex`)도 고를 수 있다. 다만 이 저장소에서 아직 써 보지 않았다.

## 3. 프롬프트 보내기

프롬프트에 넣을 것: 읽기만 · 대상 SHA · 변경 요지 · 볼 것 · 통과한 검증 · "확인된 문제만
심각도순, 위치·원인·재현·확신, 없으면 없다고". 긴 프롬프트는 스크래치패드 파일로 써서 넘긴다.

Bash 도구가 앞에서 기다릴 수 있는 시간은 10분이 최대다. 리뷰는 그보다 오래 걸리므로
`run_in_background: true`로 실행하고, 끝났다는 알림을 기다린다.

```sh
herdr agent prompt reviewer "$(cat "$SP/reviewer-prompt.md")" --wait --timeout 1500000 \
  | python3 -I -c "import json,sys; print(json.load(sys.stdin)['result']['agent']['agent_status'])"
```

## 4. 결과 읽기

`--wait`의 출력은 상태(JSON)뿐이다. 본문은 리뷰어의 트랜스크립트에서 마지막 응답을 꺼낸다.
`herdr agent read --source recent-unwrapped`는 화면의 끝부분만 보여 긴 답이 잘린다.

```sh
S=$(herdr agent get reviewer | python3 -I -c "import json,sys; print(json.load(sys.stdin)['result']['agent']['agent_session']['value'])")
python3 -I - ~/.claude/projects/<저장소 슬러그>/$S.jsonl <<'EOF'
import json,sys
last=None
for line in open(sys.argv[1]):
    try: o=json.loads(line)
    except Exception: continue
    if o.get('type')=='assistant':
        t=[c.get('text') for c in o['message'].get('content',[]) if isinstance(c,dict) and c.get('type')=='text']
        if t: last='\n'.join(t)
print(last)
EOF
```

## 5. 재검토와 닫기

- 고친 뒤 재검토는 새 SHA를 주고, 앞서 문제없던 동작이 그대로인지도 묻는다.
  force-push나 rebase로 SHA가 바뀌었으면 그 새 SHA를 적는다.
- 지적이 없으면 창을 닫는다: `herdr pane close "$P"`

## Gotchas

- 리뷰어도 같은 계정의 사용 한도를 쓴다. 11-20에는 리뷰어 세션(`3b3fa5a3`)도 한도에 걸려 멈췄다.
