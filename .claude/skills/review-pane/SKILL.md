---
name: review-pane
description: herdr 새 창에 읽기 전용 리뷰 에이전트를 띄워 검토받고, 고친 뒤 같은 에이전트에게 재검토받는다. "리뷰어 pane", "리뷰 전담 에이전트"에 발동한다. 이 세션이 직접 리뷰하는 것은 code-review다.
---

# review-pane

명령 원문과 결과 읽는 법은 `references/commands.md`.

1. `references/reviewer-settings.json`을 스크래치패드로 복사한다. 필요하면 허용 명령만 더한다.
2. 창을 열고(`herdr pane split`) 리뷰어를 띄운다(`herdr agent start reviewer`).
3. 프롬프트: 읽기만 · 대상 SHA · 변경 요지 · 볼 것 · 통과한 검증 · "확인된 문제만
   심각도순, 위치·원인·재현·확신, 없으면 없다고". `agent prompt --wait`은 백그라운드로 실행한다.
4. 고친 뒤 재검토는 새 SHA를 주고, 앞서 문제없던 동작이 그대로인지도 묻는다.
5. 지적이 없으면 창을 닫는다.

## Gotchas
- 권한 원본은 `references/`다. 스크래치패드 사본은 다음 세션에 없다.
- 결과 본문은 리뷰어 트랜스크립트에서 읽는다. 창 화면은 끝부분만 있다.
- 리뷰어도 같은 계정 한도를 쓴다.
