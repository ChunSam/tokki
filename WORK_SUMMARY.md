# Tokki 작업 요약

앱 이름은 Token Tracker였다가 Tokki로 바뀌었다. 내부 식별자와 저장 경로는 옛 이름을 유지한다(`CLAUDE.md`).

## 목적

Claude Code와 Codex의 토큰 한도 잔량을 퍼센트로 상시 확인하는 macOS 메뉴바 앱과 Windows 트레이 앱.
두 플랫폼은 Core 로직을 1:1로 맞춘다 — 같은 규칙, 같은 테스트.

## 구현 범위

- Swift Package 기반 macOS 네이티브 메뉴바 앱 (`NSStatusItem`)
- .NET WinForms `NotifyIcon` 기반 Windows 트레이 앱
- Python + GTK3 AppIndicator 기반 Ubuntu 트레이 앱(`linux/`, .deb 배포). 사용량·카운트다운·알림 4종(기준값은 메뉴에서)·일시중지·새 버전 확인·잠자기 해제 새로고침·히스토리·예측·페이스·추가 사용량/크레딧·진단·
  Claude CLI 토큰 갱신·상태줄 브리지·자동 시작까지. 설정 창·비용은 아직 없음
- Claude Code / Codex 사용량 조회 (API 우선, 실패 시 마지막 성공 값을 한도 시간 안에서 유지, 그 뒤 `--`)
- 메뉴바 표시 모드 4종, 제공자 표기 2종(약어 / 공식 아이콘), 리셋까지 남은 시간 표기
- 설정 창: 표시 · 제공자 on/off · 새로고침 간격 · 언어(한/영) · 알림 · 히스토리 · 예측
- 알림: 5시간/7일 잔량 부족, 리셋 임박, 리셋 전 소진 예측, 수집 중단, 다시 사용 가능
  (macOS `UserNotifications` / Windows 풍선)
- 페이스: 창의 경과 비율 대비 사용 비율(빠름·적정·여유), 팝업 막대에 균등 사용 눈금
- 하루 한 번 GitHub Releases 새 버전 확인(끌 수 있음), 잠자기 해제 10초 뒤 새로고침
- 로컬 히스토리(기본 7일) 저장, 24시간 추세, 유니코드 스파크라인, CSV 내보내기
- 소진 예측(현재 소모 속도로 0%에 닿는 시각), 업데이트 일시중지(1h / 3h / 재개할 때까지)
- 만료 토큰 사전 감지, 429 지수 백오프, 제공자별 쿨다운 파일, 중복 실행 방지
- 진단 정보 복사 — 앱 버전·설정·히스토리·자격 증명 저장소(키체인/파일)와 그 신선도
- 부팅 시 자동 시작, 전용 앱 아이콘, 라이트/다크 대응
- CI: 양 플랫폼 스모크 테스트 + 배포할 `.app`/exe를 실제로 조립해 검사하는 release-path smoke, 버전 기본값 드리프트 검사
- 릴리스 워크플로: 태그 → macOS universal DMG + Windows x64/arm64 zip + 검증되는 sha256 → GitHub Release

## 프로젝트 구조

```text
Token tracker/
├── Package.swift
├── Sources/
│   ├── TokenTrackerCore/        순수 로직 (조회·포맷·예측·알림·설정·현지화)
│   ├── TokenTrackerMenuBar/     AppKit UI (상태 아이템·메뉴·설정 창·알림·진단)
│   └── TokenTrackerSmokeTests/  Core 스모크 테스트 (실행 타깃)
├── windows/
│   ├── TokenTracker.Windows/        WinForms 트레이 앱
│   ├── TokenTracker.Windows.Core/   Core의 C# 포트
│   └── TokenTracker.Windows.Tests/  Core 스모크 테스트
├── linux/
│   ├── tokki/core/              C# Core의 Python 부분 포트
│   ├── tokki/app.py             GTK3 AppIndicator 트레이
│   ├── tests/                   unittest
│   └── packaging/build_deb.sh   .deb 조립
├── scripts/
│   ├── build_app.sh             release 빌드 → `.app` 번들 (universal 지원)
│   └── verify_app_bundle.swift  조립된 `.app`이 자기 리소스를 찾는지 검사
├── .github/workflows/
│   ├── ci.yml                   PR/main: 빌드·테스트·release-path smoke·버전 드리프트
│   ├── release.yml              태그 push: DMG·zip·체크섬·GitHub Release
│   └── windows-release.yml      Windows만 수동 빌드
├── plans/                       기능 계획서와 세션 handoff 기록
├── .claude/proposals/           /wrap 회고 제안서
├── README.md                    사용자 문서 (설치·표시 규칙·문제 해결·빌드)
├── SECURITY_AUDIT.md
└── WORK_SUMMARY.md
```

## 핵심 기능

### 메뉴바 표시

표시 모드는 설정 창에서 고른다.

- `Lowest remaining`: Claude/Codex 중 잔량이 더 낮은 값을 `AI 72%` 형태로 표시
- `Claude + Codex`: `Cdx 91% · Cl 63%` 형태로 둘 다 표시
- `Codex only`: `Cdx 91%`
- `Claude only`: `Cl 63%`

제공자 표기는 약어(`Cdx` / `Cl`) 또는 공식 아이콘 중 선택한다.

표시 규칙:

- 기본은 5시간 잔량. 7일 잔량이 10% 이하면 7일 잔량을 대신 보여주고 파스텔 레드로 강조한다.
- 표시 중인 창의 리셋까지 남은 시간을 퍼센트 옆에 붙인다 (`Cl 63% 2h10m`). 30초마다 로컬에서
  다시 그리므로 새로고침 간격과 무관하게 분 단위로 맞는다. 설정에서 끌 수 있다.
- 캐시된 값(`staleCache`)에는 남은 시간을 붙이지 않는다 — 값은 멈춰 있는데 시간만 흘러 틀린
  시간을 보여주게 되기 때문이다.

### 상세 메뉴

제공자별 블록:

- 5시간 / 7일 잔량, 상태(정상 · 캐시 데이터 · 요청 제한 · 다시 로그인 필요 · …)와 설명, 복구 방법
- Claude 토큰이 만료됐고 자격 증명 저장소가 8시간 넘게 갱신되지 않았으면 경과 시간 한 줄 추가
  (`자격 증명이 2d 3h째 갱신되지 않음`) — "다시 로그인했는데도 안 되는" 이유가 이 줄이다
- 예상 소진(`~2h 10m`, 리셋 전 소진이면 표시), 5시간/7일 리셋, 데이터 출처, 플랜, 기술 오류

그 아래:

- 업데이트 시각, 마지막 성공 업데이트
- 지금 새로고침, 설정…, 업데이트 일시중지(1시간 / 3시간 / 재개할 때까지)
- 진단 ▸ 진단 정보 복사, Claude/Codex 인증 파일 열기, 실행 중인 인스턴스 수
- 히스토리 ▸ 24시간 추세(5h·7d 변화량), 제공자별 스파크라인, 보관 기간, CSV 내보내기
- 로그인 시 실행, 종료

### Codex 사용량 조회

1. `~/.codex/auth.json`에서 access token과 account id를 읽는다.
2. JWT `exp`로 만료를 먼저 확인한다 — 만료된 토큰으로 요청하면 401만 돌아와 원인을 알 수 없다.
3. 쿨다운 파일(`codex-rate-limit.json`)에 유효한 쿨다운이 있으면 요청하지 않는다.
4. `https://chatgpt.com/backend-api/wham/usage`를 호출한다.
5. 응답의 rate-limit 창을 **위치가 아니라 길이**로 5h/7d 레인에 배정한다 — 2026-07부터 API가
   주간 창을 `primary_window`로 보낼 수 있어서 위치 매핑은 주간 사용량을 5h 자리에 보여준다.
6. `used_percent` → `remainingPercent = 100 - used_percent`.
7. 실패하면 마지막 성공 값을 `staleCache`로 유지한다(아래 공통 정책). 로컬 로그 fallback은 없다.

### Claude 사용량 조회

1. macOS Keychain의 `Claude Code-credentials`, 그다음 `~/.claude/.credentials.json`을 후보로 읽는다.
2. `expiresAt`이 지난 후보는 제외한다. 전부 만료면 요청 없이 "다시 로그인 필요"로 보고한다 —
   만료 토큰으로 요청하면 429가 돌아와 백오프가 그것을 진짜 한도로 오해한다.
3. 쿨다운 파일(`claude-rate-limit.json`)에 유효한 쿨다운이 있으면 요청하지 않는다. 쿨다운은 그것을
   만든 토큰의 지문에 묶여 있어 다시 로그인하면 즉시 풀린다.
4. `https://api.anthropic.com/api/oauth/usage`를 호출한다. `utilization` → `remainingPercent = 100 - utilization`.
5. 429면 쿨다운을 기록한다: `max(120초, Retry-After)`에서 시작해 연속 실패마다 2배(상한 1800초),
   지터 가산. 서버가 더 긴 `Retry-After`를 주면 그쪽을 따른다.

6. API가 아무것도 못 주면 상태줄 브리지 파일을 읽는다 — 아래 참고.

공통 정책 — 새 값을 못 얻은 제공자는 마지막 성공 값을 `staleCache`로 계속 보여준다. 유지 기간은
설정의 `마지막 값 유지 기간`(기본 12시간). 캐시 값은 히스토리·추세·예측에 들어가지 않고 남은 시간도
붙지 않는다.

토큰 갱신은 각 CLI(`claude`, `codex`)만 한다. Tokki는 리프레시 토큰을 쓰지 않는다 —
일회용이라 앱이 쓰면 CLI가 로그아웃되고(CodexBar #1161), 약관이 서드파티의 Claude 자격 증명 보유를
금한다. 대신 Claude는 위 2번에서 후보가 전부 만료면 `ClaudeCLIRefresher`가 설치된 `claude`를 잠깐
실행한다(설정 `renewClaudeTokenWithCLI`, 기본 켬). 데스크톱 앱은 이 저장소를 갱신하지 않으므로, 이게
없으면 터미널에서 `claude`를 가끔 실행해야 했다.

### Claude CLI 토큰 갱신

측정(2026-11-03, CLI 2.1.314): 대화형으로 띄운 CLI는 시작 ~0.7초 만에, 첫 화면보다 먼저 만료 토큰을
갱신한다. `claude auth status`는 갱신하지 않고, `claude -p`는 TTY 없이 토큰을 읽는 데까지 가지만
갱신 여부는 미확인이며 `~/.claude.json`에 쓰고 내장 훅 모듈을 올린다. 그래서:

- macOS는 PTY(`openpty`)로 띄운다. 작업 폴더는 신뢰된 적 없는 `claude-cli-refresh/`라 CLI가 신뢰
  확인 화면에서 멈추고 훅·플러그인·세션에 닿지 않는다. 인자로 `--strict-mcp-config`와
  `disableAllHooks`/`remoteControlAtStartup:false` 설정을 덧붙이고, 환경에서 `ANTHROPIC_*`·`CLAUDE*`를
  빼고 `DISABLE_AUTOUPDATER=1`을 넣는다.
- 저장소의 수정 시각(키체인 속성 또는 파일 mtime — 비밀값은 읽지 않음)이 바뀌면 갱신으로 본다.
  1초 기다린 뒤 Enter로 신뢰 화면의 기본값 "No, exit"를 골라 스스로 나가게 하고, 안 나가면
  SIGTERM, 그래도 남으면 SIGKILL. 시간 초과는 15초.
- Windows는 PTY 대신 숨은 콘솔(`CreateNoWindow`, 리디렉션 없음)로 띄우고, 파일이 바뀌어 만료되지
  않은 토큰으로 읽히면 갱신으로 본 뒤 프로세스 트리를 종료한다. UTM의 Windows 11 Arm VM(CLI 2.1.317)
  에서 `expiresAt`을 과거로 돌려 확인했다: 앱이 +0.6초에 `claude.exe`를 띄우고 +1.3초에 파일이 8시간
  토큰으로 바뀌었으며, 콘솔 창도 남은 프로세스도 없었다. macOS와 달리 `~/.claude.json`을 한 번 쓴다.
  win-x64 빌드도 VM의 x64 에뮬레이션에서 같은 결과였다(+1.6초). 실제 x64 PC와 릴리스 zip으로는 아직
  확인하지 않았다 — `docs/windows-vm-testing.md`.
- 재시도 간격은 성공 후 5분, 실패·CLI 없음 후 30분(`ClaudeCLIRefresh.nextAttempt`, 양쪽 1:1).

### Claude 상태줄 브리지

토큰이 만료되면 위 경로는 아무것도 가져올 수 없으므로, 자격 증명을 건드리지 않는 두 번째 입력을
둔다. `scripts/claude-statusline-bridge.sh`(macOS/Linux, python3로 JSON 파싱)와 `.ps1`(Windows)이
사용자가 이미 설정해 둔 상태줄 명령을 감싸, stdin을 한 번 읽어 사용률만 기록하고 같은 stdin으로 그
명령을 실행해 출력을 그대로 넘긴다. 설치하지 않으면 동작은 전혀 바뀌지 않는다.

파일(`claude-statusline.json`, 0600, atomic rename)에는 두 창의 `used_percentage`·`resets_at`와
`measured_at`·`written_at`만 들어간다. 토큰·세션 페이로드·`spend_limit`은 저장하지 않는다.

설계의 핵심은 **다시 쓰였다는 것이 다시 측정됐다는 뜻이 아니라는 것**이다. 상태줄 명령은 CLI가
알아서 다시 실행하므로, 값이 바뀌지 않은 재실행에서는 `measured_at`을 그대로 두고 `written_at`만
움직인다. 수집기는 `measured_at`으로만 나이를 재고 `written_at`은 진단에만 쓴다. 같은 창 인스턴스
(`resets_at` 동일)에서 더 낮은 사용률은 더 오래된 읽기이므로 덮어쓰지 않고, 페이로드에서 빠진 창은
이미 기록된 값을 지우지 않는다(리셋이 지난 창은 읽을 때 버린다).

`ClaudeStatusLineBridge`(양쪽 Core 1:1)가 그 파일을 읽는다. 읽은 값은 `.api`가 아니라
`.statusLine`이고, 30분 안이면 현재 값으로(카운트다운 포함) 보여주고, 그 뒤로는 읽기를 포기해 기존
`staleCache` 층이 `캐시 데이터 사용 중`으로 넘겨받고, 유지 기간이 지나면 `--`가 된다. 히스토리에는
들어가지 않는다 — 폴링마다 같은 측정을 새 시각으로 기록하면 예측이 "사용이 멈췄다"고 읽는다.
형식·설치·근거는 `docs/claude-statusline-bridge.md`.

### 알림

`알림` 설정을 켜면 5시간 잔량 부족(기본 20%), 7일 잔량 부족(기본 10%), 리셋 임박(기본 10분 전),
그리고 켜둔 경우 "리셋 전 소진 예상"을 보낸다. 같은 창 인스턴스에 대해 한 번만 보내도록 id로 중복
제거한다.

리셋 임박은 "안 쓰면 사라지는 양"이 있을 때만 보낸다. 측정값(`api`·`statusLine`)이고 그 창의 잔량이
알림 기준보다 많아야 하며, 본문에 남은 양을 적는다(`Claude 5h 5m 후 리셋 — 60% 남음`). 기준 이하인 창의
리셋은 반가운 소식이라 알리지 않고, 리셋 뒤 "다시 사용 가능"이 대신 간다. 캐시 값은 그 뒤에 썼을 수
있어 제외한다. 잔량 알림 기준이 0이면 조금이라도 남았을 때 보낸다.

"다시 사용 가능"은 직전 값과 이번 측정을 비교한다. 직전 값이 그 창의 알림 기준 이하였고 리셋이
지났으며 이번 측정(`api`·`statusLine`)이 기준을 넘으면 한 번 보낸다. 직전 값은 제공자별 마지막
'숫자가 있는 값'이고(`UsageAlertEvaluator.lastReadings`), 측정만 이를 교체하며 캐시 값은 빈 자리만
채운다. 시작할 때 캐시 스냅샷으로 채워 두므로 앱이 꺼져 있던 동안의 리셋도 잡힌다.

### 히스토리 · 예측 · 일시중지

- 히스토리는 실측값(`api`)만 저장한다. 60초 안의 연속 기록은 합치고 보관 기간(기본 7일)을 넘긴 것은
  버린다.
- 예측은 현재 창 인스턴스의 기록(직전 리셋 이후)만으로 소모 속도를 구한다. 10분 미만 구간이거나
  줄어들지 않으면 예측하지 않는다. 5h 창이 비면(현재 Codex) 7d 창으로 대신한다.
- 일시중지는 `pollPausedUntil`로 저장돼 재시작해도 유지된다. `지금 새로고침`은 명시적 요청이므로
  일시중지를 푼다.

### 부팅 시 자동 시작

`ServiceManagement`의 `SMAppService.mainApp`을 사용해 로그인 항목을 등록/해제한다.

팝업의 `⋯` 메뉴 항목:

- `Launch at Login: Enabled`
- `Launch at Login: Disabled`
- `Launch at Login: Requires approval in System Settings`
- `Launch at Login: App bundle not found`

## 주요 파일

macOS UI (`Sources/TokenTrackerMenuBar/`)

- `AppDelegate.swift` — 새로고침 루프, 타이머, 팝업 내용 구성, 액션 핸들러
- `StatusItemRenderer.swift` — 메뉴바 텍스트/아이콘 그리기. 리소스 번들은 `Bundle.module`이 아니라
  `Contents/Resources`에서 직접 찾는다(릴리스 `.app`에서 `Bundle.module`은 trap한다)
- `StatusPanelController.swift` — 상태 항목 클릭 시 여는 `NSPopover`. 바깥 클릭은 전역 모니터로 닫는다
  (`.transient`는 상태 항목을 다시 눌러도 닫히지 않는다)
- `StatusPanelView.swift` — 팝업 내용(SwiftUI): 제공자 카드, 잔량 막대, 하단 버튼과 `⋯` 메뉴
- `PreferencesWindowController.swift` — 설정 창(SwiftUI 그룹형 Form)
- `UsageNotificationCoordinator.swift` — 알림 권한·중복 제거·발송
- `DiagnosticsReporter.swift` — 진단 텍스트
- `LoginItemManager.swift` — 로그인 항목 등록/해제

Core (`Sources/TokenTrackerCore/`)

- `ClaudeUsageClient.swift` / `CodexUsageClient.swift` — 조회, 만료 감지, 쿨다운
- `ClaudeStatusLineBridge.swift` — 상태줄 브리지 파일 파싱과 신선도 판정(재생 데이터 차단)
- `CodexWindowMapper.swift` — 창 길이 기준 5h/7d 배정
- `UsageService.swift` — 두 제공자 통합, stale cache 정책
- `CredentialExpiry.swift` / `CredentialSource.swift` — 만료 판정, 저장소 종류·신선도, 메뉴 안내 줄
- `RateLimitBackoff.swift` / `RateLimitState.swift` / `RateLimitStore.swift` — 429 백오프와 영속화
- `UsageIssueFormatter.swift` — 오류 문자열 → 상태/설명/복구 안내
- `DisplayFormatter.swift` — 메뉴바 문자열, 표시 창 선택, 리셋 카운트다운
- `UsageForecast.swift` / `Sparkline.swift` / `UsageHistoryStore.swift` — 예측, 스파크라인, 히스토리
- `UsageAlertEvaluator.swift` — 알림 후보 계산(다시 사용 가능 포함)
- `UsagePace.swift` — 균등 사용선 대비 페이스와 메뉴 줄
- `UpdateCheck.swift` — 최신 릴리스 조회·버전 비교·확인 주기
- `ClaudeCLIRefresh.swift` — 만료된 Claude 토큰을 CLI 실행으로 갱신(재시도 간격·실행 인자·PTY 실행기)
- `Cost.swift` — 금액 표시 Core: 로컬 시간대 기간 경계, Anthropic·OpenAI 비용 파서, 구독 추가 사용량·크레딧,
  표시 조건, 금액 형식, 예산, 조회 주기. 구독 추가 사용량·크레딧(1단계)만 사용량 파서가 `ProviderUsage`에
  실어 카드·트레이 줄(`SubscriptionCostText`)로 나가고, Admin API 비용은 `AdminCost`·`CostDisplay`가 맡는다. 히스토리에는
  금액을 남기지 않는다. 설계는 `docs/cost-display-design.md`
- `AdminCost.swift` / `AdminKeyStore.swift` — 금액 표시 2단계 수집: Admin API 요청·페이지 순회·401/429 분류,
  제공자별 조회 주기와 캐시(`cost-cache.json`), 키체인 보관, 진단 줄. 설정 토글이 꺼져 있으면 키를 읽지 않는다
- `CostAlerts.swift` — 금액 알림: API 비용 월 예산 80%·100%, Claude 추가 사용량 단계, Codex 크레딧 한도. 로컬 달마다
  한 번이고, 보낸 id는 설정(`deliveredCostAlerts`)에 남겨 재실행에도 반복하지 않는다
- `CostDisplay.swift` — Admin API 비용의 화면 문구(카드·트레이 줄·상태), 모델 이름 줄이기, 메뉴바 금액과
  `$` 트레이 아이콘 규칙. 설정 창의 API 비용 토글·월 예산·Admin 키 시트, 카드의 금액 영역, 메뉴바 금액이 이것을 쓴다
- `PauseController.swift` / `InstanceArbiter.swift` — 일시중지, 중복 실행 판정
- `Settings.swift` / `Localization.swift` / `Paths.swift` — UserDefaults, 한/영 문자열, 파일 경로

Windows (`windows/`)

- `TokenTracker.Windows/TrayAppContext.cs` — 트레이 메뉴와 새로고침 루프 (macOS `AppDelegate` +
  `StatusPanelView`에 대응)
- `TokenTracker.Windows.Core/` — 위 Core 파일들의 C# 포트. 파일명이 1:1로 대응한다
- `TokenTracker.Windows.Tests/Program.cs` — Core 스모크 테스트

빌드·배포

- `scripts/build_app.sh` — release 빌드 후 `.app` 조립, `AppIcon.png` → `AppIcon.icns`.
  `APP_VERSION`/`APP_BUILD` 로컬 기본값을 가진다(릴리스는 태그·run number로 덮어씀)
- `scripts/verify_app_bundle.swift` — 조립된 `.app`의 실행 파일·Info.plist·리소스 번들 검사
- `scripts/utm-windows-ps.sh` — UTM Windows VM 안에서 PowerShell 스크립트를 실행하고 출력을 받아 옴
  (`--user`는 로그인한 사용자 세션에서). 개발용이며 빌드와 무관.
- `scripts/claude-statusline-bridge.sh` / `.ps1` — 상태줄 브리지. 사용자 설정이며 빌드에는 포함되지
  않는다. 양쪽 스모크 테스트가 스크립트를 실제로 실행해 재생 규칙을 검증한다
- `windows/TokenTracker.Windows/TokenTracker.Windows.csproj` — `<Version>` 로컬 기본값.
  `build_app.sh`의 `APP_VERSION`과 같아야 하며 CI가 드리프트를 막는다

## 빌드 및 실행

macOS 스모크 테스트:

```bash
swift run TokenTrackerSmokeTests
```

앱 번들 생성과 검사:

```bash
scripts/build_app.sh
swift scripts/verify_app_bundle.swift ".build/Tokki.app"
```

Intel/Apple Silicon 겸용은 `APP_ARCHS="arm64 x86_64" scripts/build_app.sh`.

앱 실행:

```bash
open ".build/Tokki.app"
```

Windows 쪽 명령(`dotnet run`, `dotnet publish`)은 README "개발" 절에 있다. **이 개발 머신에는 dotnet이
없으므로 Windows 코드의 컴파일 검증은 CI가 유일한 게이트다.**

## 검증 기준

PR마다 CI가 다음을 확인한다.

- macOS: 버전 기본값 일치 → `swift build` → 스모크 테스트 → `build_app.sh` → `verify_app_bundle.swift`
- Windows: Core 테스트 → WinForms 빌드 → self-contained single-file publish → publish된 exe의 버전 스탬프

릴리스 워크플로는 여기에 더해 DMG/zip을 만들고, 방금 쓴 sha256 파일이 그 자리에서 검증되는지
확인한 뒤 GitHub Release에 올린다.

배포 후에는 산출물을 실제로 내려받아 설치·실행해 본다. 소스가 빌드된다는 것과 배포된 파일이
동작한다는 것은 다른 주장이다 — 1.1.5까지의 DMG는 아이콘 표기에서 즉시 죽었고, Windows 앱은
모든 빌드가 `1.0.0`을 보고했으며, 체크섬 파일은 받은 자리에서 검증되지 않았다. 셋 다 소스 레벨
CI로는 보이지 않았다.

설치 확인:

```bash
open "/Applications/Tokki.app"
osascript -e 'tell application "System Events" to exists process "TokenTrackerMenuBar"'
```

## 현재 제약

- Claude 한도 퍼센트는 API, 상태줄 브리지, 또는 최근 성공 캐시에 의존한다.
- 상태줄 브리지는 상시 수집을 대신하지 못한다. CLI를 띄워 두지 않거나 다른 기기에서만 쓰면 새
  측정이 생기지 않고, 값은 정직하게 `캐시 데이터 사용 중`을 거쳐 `--`가 된다.
- Claude 로컬 JSONL 로그는 토큰 사용량은 제공하지만 한도 대비 퍼센트는 제공하지 않으므로 fallback으로
  쓰지 않는다.
- Claude 액세스 토큰(약 8시간)은 CLI만 갱신한다. 자동 갱신은 설치된 `claude`를 띄워 맡길 뿐이라,
  CLI가 없거나 리프레시 토큰까지 만료되면 마지막 값만 남는다.
- 자동 갱신은 "시작하자마자 갱신하고 신뢰 화면에서 멈춘다"는 CLI 2.1.314의 동작에 기대고 있다.
  순서가 바뀌면 시도는 `unchanged`로 끝나고 진단 정보의 `Claude CLI last renewal`에 남는다.
- 첫 Claude Keychain 접근 시 macOS 권한 허용 창이 뜰 수 있다.
- 산출물은 unsigned `.app`이다. 다른 Mac에서 더블클릭으로 열려면 Developer ID 서명과 notarization이
  필요하다.
- 이 개발 머신에는 dotnet이 없다. 대신 UTM의 Windows 11 Arm VM에서 C# 테스트·publish·앱 실행을
  한다(`docs/windows-vm-testing.md`). VM 밖에서는 여전히 CI가 유일한 C# 게이트다.

## 다음 개선 후보

- 로그인 항목 승인 상태를 System Settings로 바로 여는 버튼 추가
- signed/notarized 배포 빌드 구성
- Codex도 자격 증명 저장소 신선도를 메뉴에 표시 (지금은 Claude만)
