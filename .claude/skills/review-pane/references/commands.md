# review-pane — 명령 원문

2026-11-20 세션(`d2783f2c`)에서 리뷰어 세 번, 이 스킬을 만든 세션(`93c6af24`)에서 한 번 이 순서로 실행했다.

## 창 열기와 리뷰어 시작

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

## 프롬프트 보내기

Bash 도구가 앞에서 기다릴 수 있는 시간은 10분이 최대다. 리뷰는 그보다 오래 걸리므로
`run_in_background: true`로 실행하고, 끝났다는 알림을 기다린다.

```sh
herdr agent prompt reviewer "$(cat "$SP/reviewer-prompt.md")" --wait --timeout 1500000 \
  | python3 -I -c "import json,sys; print(json.load(sys.stdin)['result']['agent']['agent_status'])"
```

긴 프롬프트는 스크래치패드 파일로 써서 넘긴다.

## 결과 읽기

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

## 닫기

```sh
herdr pane close "$P"
```

## 그 밖의 주의

- force-push나 rebase로 SHA가 바뀌면 재검토 프롬프트에 새 SHA를 적는다.
- 리뷰어 종류로 Codex(`--kind codex`)도 고를 수 있다. 다만 이 저장소에서 아직 써 보지 않았다.
- 리뷰어도 같은 계정의 사용 한도를 쓴다. 11-20에는 리뷰어 세션(`3b3fa5a3`)도 한도에 걸려 멈췄다.
- 권한 파일이 허용하는 것: 읽기(Read·Grep·Glob), `git diff/log/show/status/blame/merge-base/rev-parse`, 테스트 실행
  (`swift build`, `swift run TokenTrackerSmokeTests`, `python3 -m unittest`), `jq`, `grep`, `sed -n`, `ls`, `wc`.
  거부하는 것: Edit/Write, commit, push, switch, checkout, reset, stash, rebase, `gh`.
  `LC_ALL=... wc -m`처럼 환경 변수를 앞에 붙인 명령은 허용 목록에 걸리지 않는다.
