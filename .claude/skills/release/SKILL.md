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
   `scripts/verify_app_bundle.swift`로 검사한다. 세부 → `references/macos.md`.
4. 앱 종료 → 설치본을 휴지통으로 → `ditto`로 `/Applications`에 설치 → 실행 → 바뀐 화면 확인.
5. Windows VM에서 zip 두 개를 검사하고 arm64를 설치본 자리에 넣어 실행한다 → `references/windows.md`.

## Gotchas
- 상태 항목은 `/Applications`에서 실행할 때만 보인다.
- 종료 먼저. bundle id가 같아 나중에 뜬 쪽이 물러난다. 옛 `Token Tracker.app`도 휴지통으로.
- `rm -rf`하지 않는다.
- Admin 키가 있으면 설치 뒤 키체인 암호 창이 뜬다. 사용자만 답할 수 있고, 그 전엔 비용이 안 나온다.
