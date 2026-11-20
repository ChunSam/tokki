---
name: release
description: Tokki 새 버전을 릴리스하고 배포 산출물을 받아 설치·실행까지 확인한다. "릴리스해", "vX.Y.Z 내줘"에 발동한다.
---

# release

사용자가 릴리스를 지시했을 때만 범프·태그한다.

1. 버전 기본값 3곳 범프 → PR → 자동 머지(rebase)를 걸고, CI 두 잡 초록으로 머지되길 기다린다.
2. 머지된 main 커밋에 `git tag -a vX.Y.Z` → push. `release.yml`은
   `gh run watch`를 백그라운드로 걸어 기다린다.
3. DMG·deb와 각 `.sha256`을 새 디렉터리에 받아 그 자리에서 `shasum -c`. 마운트해 버전을 보고
   `scripts/verify_app_bundle.swift`로 검사한다. 판정은 아래 "macOS 검사 세부"를 따른다.
4. 앱 종료 → 설치본을 휴지통으로 → `ditto`로 `/Applications`에 설치 → 실행 → 바뀐 화면 확인.
5. Windows VM에서 zip 두 개를 검사하고 arm64를 설치본 자리에 넣어 실행한다.
   이 단계를 시작할 때 `references/windows.md`를 읽는다 (VM에 보낼 스크립트와 통과 기준).

## Gotchas

- 상태 항목은 `/Applications`에서 실행할 때만 보인다.
- 종료 먼저. bundle id가 같아 나중에 뜬 쪽이 물러난다. 옛 `Token Tracker.app`도 휴지통으로.
- `rm -rf`하지 않는다.
- Admin 키가 있으면 설치 뒤 키체인 암호 창이 뜬다. 사용자만 답할 수 있고, 그 전엔 비용이 안 나온다.

## macOS 검사 세부 (3·4단계)

판정을 헷갈리게 했던 것들이다. 셋 다 실제로 한 번씩 물렸다.

- `codesign -v`가 "not signed at all"을 내도 정상이다. ad-hoc 서명은 실행 파일에만 붙고
  번들 전체에는 붙지 않는다. 번들 검사는 `scripts/verify_app_bundle.swift`가 맡는다.
- 바이너리에 새 한국어 문자열이 들어갔는지는 `LC_ALL=C grep -a -F '<문자열>' <실행 파일>`로
  찾는다. `strings`는 UTF-8 한국어를 잡지 못해 "없다"로 오판한다.
- 팝업·설정창은 `screencapture -x -o -l <windowID>`로 창 번호를 지정해 찍는다. `-R`(영역)은
  그 자리에 겹친 다른 창을 찍는다. 창 번호는 `CGWindowListCopyWindowInfo`에서 소유자
  `Tokki`로 찾는다(팝오버는 layer 25). 키체인 프롬프트의 소유자는 `SecurityAgent`다.
- 팝업은 computer-use로 보이지 않는다. System Events로 연다:
  `tell application "System Events" to tell process "TokenTrackerMenuBar" to click (first menu bar item of (last menu bar))`
  (1.2.0부터 숨은 편집 메뉴 때문에 메뉴 막대가 두 개이고, 상태 항목은 마지막 것에 있다.)
