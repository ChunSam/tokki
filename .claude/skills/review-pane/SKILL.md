---
name: review-pane
description: herdr 새 창에 읽기 전용 리뷰 에이전트를 띄워 검토받고, 고친 뒤 같은 에이전트에게 재검토받는다. "리뷰어 pane", "리뷰 전담 에이전트"에 발동한다. 이 세션이 직접 리뷰하는 것은 code-review다.
---

# review-pane

2026-11-20 세션(`d2783f2c`)에서 리뷰어 세 번, 이 스킬을 만든 세션(`93c6af24`)에서 한 번 이 순서로 실행했다.

## 1. 권한 파일 복사

`references/reviewer-settings.json`을 스크래치패드로 복사한다. 필요하면 허용 명령만 더한다.
**원본은 `references/`다** — 스크래치패드 사본은 다음 세션에 없다.
스크래치패드가 없거나 도중에 사라지면 이 세션 전용 임시 폴더를 `SP`로 정하고, 권한 파일과
프롬프트 파일을 그 자리에 다시 만든다 (2026-11-27).

- 허용: 읽기(Read·Grep·Glob), `git diff/log/show/status/blame/merge-base/rev-parse`, 테스트 실행
  (`swift build`, `swift run TokenTrackerSmokeTests`, `python3 -m unittest`), `jq`, `grep`, `sed -n`, `ls`, `wc`.
- 거부: Edit/Write, commit, push, switch, checkout, reset, stash, rebase, `gh`.
- `LC_ALL=... wc -m`처럼 환경 변수를 앞에 붙인 명령은 허용 목록에 걸리지 않는다.

## 2. 창 열기와 리뷰어 시작

`pane split`은 JSON을 돌려준다. 창 ID는 `result.pane.pane_id`에 있다.
`--current requires HERDR_PANE_ID`로 실패하면 `herdr pane list`에서 이 세션의 창을 찾아
`--current` 자리에 그 ID를 넘긴다(예: `herdr pane split w1:p1 …`). `focused: true`만으로 고르지 말고
창 제목이 이 세션의 것인지도 맞춰 본다. 2026-11-27에는 herdr 창 안에서 돌았는데도 Bash 환경에 이 변수가 없었다.

```sh
SP=<이 세션의 스크래치패드>
cp .claude/skills/review-pane/references/reviewer-settings.json "$SP/"
P=$(herdr pane split --current --direction down --cwd "$PWD" --no-focus \
  | python3 -I -c "import json,sys; print(json.load(sys.stdin)['result']['pane']['pane_id'])")
herdr pane wait-output "$P" --regex '[%$#>❯] *$' --timeout 15000 >/dev/null 2>&1
herdr agent start reviewer --kind claude --pane "$P" --timeout 60000 -- \
  --permission-mode dontAsk --settings "$SP/reviewer-settings.json"
```

### Codex 리뷰어

2026-11-27에 Codex로 제안서를 읽기 전용 검토했다(재검토는 아직 안 해 봤다). 권한 파일은 Claude용이라
Codex에는 쓰지 않는다.

```sh
herdr agent start <이름> --kind codex --pane "$P" --timeout 90000 -- --sandbox read-only --ask-for-approval never
```

- **시작하면서 스스로 업데이트할 수 있다.** 이날은 `npm install -g @openai/codex`(0.154.0 → 0.172.0)에
  11분이 걸렸고, 그동안 `codex` 명령이 사라져 있었다. `agent start`는 `agent_not_ready`로 끝난다.
  `herdr pane read "$P"`로 업데이트 중인지 보고, 끝나면("Please restart Codex") `codex --version`을
  확인한 뒤 같은 창에서 다시 시작한다.
- **업데이트 직후 첫 프롬프트는 사라질 수 있다.** 모델 변경 안내가 뜨면서 `--wait`가 바로 `idle`을
  돌려줬고 답이 없었다. 창에 작업 흔적이 없으면 같은 프롬프트를 다시 보낸다.
- **결과는 창에서 읽는다.** 4단계의 트랜스크립트 조회는 Claude 리뷰어용이다. Codex는
  `herdr pane read "$P" --source recent-unwrapped --lines 600`으로 답 전체를 읽었다.

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

`--wait`의 출력은 상태(JSON)뿐이다. 본문은 리뷰어의 트랜스크립트에서 마지막 응답을 꺼낸다
(Claude 리뷰어. Codex는 위 "Codex 리뷰어"를 따른다).
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

- 리뷰어를 띄울 때 `--add-dir`을 붙이지 않는다. 처음 보는 폴더면 "이 폴더를 믿느냐" 확인 창이
  떠서 시작 단계에서 멈춘다(`agent_not_ready`). 그 답은 사용자의 보안 판단이라 대신 고르지 않는다.
  다른 저장소를 봐야 하면 권한 파일에 `Bash(git -C <경로> show:*)` 같은 줄을 더하고,
  프롬프트에 `git -C <경로> show HEAD:<파일>`로 읽으라고 적는다 (2026-11-20).
- 리뷰어도 같은 계정의 사용 한도를 쓴다. 11-20에는 리뷰어 세션(`3b3fa5a3`)도 한도에 걸려 멈췄다.
