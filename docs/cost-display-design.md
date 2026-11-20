# 금액 표시 설계 (Claude · Codex)

상태: 설계 확정 (2026-11-03). 1차 검토와 결정을 반영했다. 구현은 아래 "구현 순서"대로 PR을 나눈다.

1차 검토에서 정한 것:
- 시각과 기간 경계(오늘·이번 달)는 사용자 기기의 시간대를 따른다. 특정 시간대(한국 시간이나 UTC)로 고정하지 않는다.
- 구독만 쓰고 추가 사용 금액을 꺼 둔 사용자에게는 금액이 어디에도 나오지 않는다(팝업·메뉴바·트레이·알림).

## 왜 필요한가

Tokki는 구독 플랜의 5시간·7일 한도만 보여준다. API를 쓴 만큼 돈을 내는 사용자는 Claude Code나
Codex에 API 키로 로그인하므로, 앱이 읽는 구독용 OAuth 토큰이 없고 카드에는 `로그인 필요`와 `--`만 남는다.
이 사용자에게 필요한 것은 "이번 달 얼마 나왔나"다. 구독 사용자도 한도를 넘겨 쓰는 추가 사용량(Claude)이나
크레딧(Codex)은 금액이라 지금 화면에는 나오지 않는다.

## 범위

| 단계 | 대상 | 데이터 | 새 자격 증명 |
|---|---|---|---|
| 1 | 구독 사용자 | Claude 추가 사용량, Codex 크레딧 잔액 | 없음 (지금 읽는 토큰 그대로) |
| 2 | API 사용자 | Claude·OpenAI 조직의 실제 청구 비용 | 제공자별 Admin 키 |
| 3 (보류) | 키를 넣기 싫은 API 사용자 | 이 기기의 CLI 로그로 추정한 비용 | 없음 |

3단계는 이 문서에서 대안으로만 다룬다(맨 아래).

## 데이터 출처

### 1단계 — 구독 응답에 이미 있는 금액

**Claude** (`GET /api/oauth/usage`, 지금 호출하는 엔드포인트). 2026-11-03 Pro 계정으로 확인했다:

```json
"extra_usage": { "is_enabled": false, "monthly_limit": null, "used_credits": null,
                 "utilization": null, "currency": null, "decimal_places": null, ... },
"spend": { "used": { "amount_minor": 0, "currency": "USD", "exponent": 2 },
           "limit": null, "percent": 0, "severity": "normal", "enabled": false, ... }
```

- 이 계정은 추가 사용량이 꺼져 있어 값이 비어 있다. 켜진 계정의 응답은 아직 보지 못했다.
- `spend.used`는 단위가 명시돼 있다(`amount_minor` / 10^`exponent`). 그래서 `spend`를 우선 쓰고,
  `extra_usage`(`used_credits`, `decimal_places`, `monthly_limit`)는 보조로 쓴다.
- 문서화되지 않은 필드다. 형태가 다르면 조용히 표시하지 않는다(파싱 실패가 사용량 조회 실패로
  번지면 안 된다).

**Codex** (`GET https://chatgpt.com/backend-api/wham/usage`, 지금 호출하는 엔드포인트):

```json
"credits": { "has_credits": bool, "unlimited": bool, "overage_limit_reached": bool,
             "balance": "string", "approx_local_messages": [..], "approx_cloud_messages": [..] },
"spend_control": { "reached": bool, "individual_limit": null }
```

- `balance`는 문자열이다. 숫자로 읽히면 표시하고, 아니면 표시하지 않는다.
- 크레딧을 쓰지 않는 사용자(`has_credits`가 false)에게는 아무것도 표시하지 않는다(아래 "금액 표시 조건").

### 2단계 — 조직 청구 비용 (Admin API)

**Claude (Anthropic Console 조직)** — 엔드포인트가 둘이고, 시간 단위와 반영 속도가 다르다.

| | Analytics API (신규, 베타) | Usage and Cost API (기존) |
|---|---|---|
| 비용 엔드포인트 | `GET /v1/organizations/analytics/cost` | `GET /v1/organizations/cost_report` |
| 비용 시간 단위 | `1m`·`1h`·`1d` 버킷, 또는 범위 합계 | `1d`만 (UTC 날짜) |
| 반영 지연 | 보통 4시간, 최대 24시간. 응답의 `data_refreshed_at` 이후는 비었거나 부분 값 | 보통 5분 |
| 금액 형식 | `cost.amount` = `"41280.000000"` (센트, 소수 6자리) | `amount` = `"123.45"` (센트) |
| 범위 제한 | 요청당 최대 31일, 2026-01-01 이후·최근 365일, 1시간 버킷은 경계를 정시로 내림 | 제한 적음 |
| 헤더 | `x-api-key`, `anthropic-version: 2023-06-01`, `anthropic-beta: analytics-usage-cost-2026-08-24` | `x-api-key`, `anthropic-version: 2023-06-01` |

- 사용자 시간대의 "오늘"·"이번 달" 경계를 지키려면 **시간 단위 비용**이 필요하고, 그것은 Analytics API에만
  있다. 기존 API는 UTC 날짜 단위라 다른 시간대의 하루를 쪼갤 수 없다. 그래서 기본안은 Analytics API다
  (아래 "시간대와 기간 경계", 결정 1).
  - 요청 1: 사용자 시간대의 이번 달 1일 00:00부터 지금까지, `time_bucket_width=1h`, `order_by=time`.
    시간별 합계를 로컬 날짜로 묶어 "오늘"과 "이번 달"을 만든다(최대 744행, 한 페이지 1,000행).
  - 요청 2: 같은 범위를 버킷 없이 `group_by[]=model`로 — 이번 달 모델별 비중.
- 키: `sk-ant-admin01-...`. **개인 계정은 발급받을 수 없다** — Console에서 조직을 먼저 만들어야 한다.
- 기존 API는 "legacy, remains supported" 상태다. 신선도를 우선하는 결정을 하면 이쪽을 쓴다.
  클라이언트는 엔드포인트별 파서만 바뀌도록 나눈다.

**Codex (OpenAI 조직)**

- 엔드포인트: `GET https://api.openai.com/v1/organization/costs`.
  - 헤더: `Authorization: Bearer <Admin 키>`.
  - 파라미터: `start_time`(Unix 초, 포함), `end_time`(제외), `bucket_width=1d`(현재 `1d`만 지원),
    `group_by[]=line_item`, `limit` 1~180(기본 7).
  - 응답: `data[]`의 버킷마다 `start_time`·`end_time`과 결과 배열. 결과 항목은
    `amount.value`(숫자, **달러 단위**)·`amount.currency`(`"usd"`)·`line_item`.
  - 공식 OpenAPI 명세는 결과 배열 이름을 `result`로 적고 있는데, 실제 응답 예시는 `results`를 쓴다.
    **둘 다 읽는다.**
- 키: `sk-admin-...`. 조직 Owner만 만들 수 있다. 프로젝트 키(`sk-proj-...`)로는 비용을 볼 수 없다.
- Codex CLI가 API 키로 로그인돼 있으면 `~/.codex/auth.json`이 `auth_mode: "apikey"`와 `OPENAI_API_KEY`를
  갖는다. 이 키로는 비용을 읽을 수 없지만, "구독 한도가 없는 이유"를 안내하는 데 쓴다.

### 시간대와 기간 경계

화면에 나오는 모든 시각과 "오늘"·"이번 달"의 경계는 **기기의 현재 시간대**를 따른다. 코드에 특정 시간대를
적지 않는다. macOS는 `TimeZone.current`, Windows는 `TimeZoneInfo.Local`을 쓰고, 순수 함수는 시간대를
인자로 받아 테스트에서 여러 시간대를 넣어 본다. 기기 시간대가 바뀌면(여행, 설정 변경) 다음 조회부터 새
경계로 다시 계산한다. 서머타임이 있는 시간대에서는 23시간·25시간인 날이 생기므로, 경계는 "자정 + 24시간"이
아니라 달력으로 계산한다.

- 날짜 경계를 API 요청 범위로 바꿀 때는 로컬 자정을 UTC 시각으로 변환해 넘긴다.
- **Claude (Analytics API, 1시간 버킷):** 정시 단위 오프셋 시간대(한국 +9, 미국 동부 −5/−4 등)는 경계가
  정확하다. 30분·45분 오프셋 시간대(인도 +5:30, 네팔 +5:45 등)는 API가 경계를 정시로 내리므로 최대 45분
  어긋난다. 필요하면 경계가 걸친 한 시간만 `1m` 버킷으로 다시 받아 맞춘다(구현 3단계에서 판단).
- **OpenAI (costs, `1d`만):** `start_time`을 로컬 자정으로 주면 버킷이 그 시각부터 하루씩 잘리는지, UTC
  자정에 맞춰지는지 문서로 확인되지 않는다. **실제 키로 먼저 확인한다**(결정 2).
  - 로컬 자정 기준으로 잘리면: "오늘"·"이번 달" 모두 로컬 기준으로 정확하다.
  - UTC 자정에 맞춰지면: 로컬 경계로 하루를 쪼갤 수 없다. 그때의 표시 방식은 결정 2에서 고른다.
- **반영 지연을 숨기지 않는다:** Analytics API는 몇 시간 늦게 반영되므로, 금액 옆이나 도움말에
  `data_refreshed_at`을 로컬 시각으로 보여준다(`14:20 기준`). 기준 시각이 오늘 자정보다 이르면 "오늘"
  금액은 `집계 전`으로 표시한다(0원으로 보이면 안 된다).
- 청구서는 UTC 달 기준이라, 로컬 "이번 달"은 달 경계 부근에서 청구서와 몇 시간분 다를 수 있다. 화면 문구는
  "이번 달 사용"으로 하고, 청구 금액이라고 부르지 않는다.

## Core 모델 (양쪽 1:1)

```swift
public struct CostAmount: Equatable, Sendable {      // 금액은 Decimal, 통화 코드는 대문자
    public let value: Decimal
    public let currency: String                       // "USD"
}

public enum CostSource: String, Sendable {
    case subscriptionExtra   // 1단계: 구독 응답의 추가 사용량·크레딧
    case adminAPI            // 2단계: 조직 청구 비용
}

public struct ProviderCost: Equatable, Sendable {
    public let provider: Provider
    public let source: CostSource
    public let monthToDate: CostAmount?   // 기기 시간대의 이번 달 1일 00:00부터
    public let today: CostAmount?         // 기기 시간대의 오늘 00:00부터, 집계 전이면 nil
    public let limit: CostAmount?         // 추가 사용량 한도 또는 사용자가 정한 월 예산
    public let creditBalance: CostAmount? // Codex 크레딧 잔액
    public let topItems: [CostLineItem]   // 이번 달 상위 3개 (모델/라인 아이템)
    public let measuredAt: Date
    public let dataRefreshedAt: Date?
}
```

순수 함수 (`now` 주입, 진리표 테스트 대상):

- `CostPeriod.monthStart(now:timeZone:)`, `CostPeriod.dayStart(now:timeZone:)` — 기기 시간대의 달력
  경계를 UTC 시각으로 돌려준다(서머타임 포함).
- `CostPeriod.requestRange(now:timeZone:)` — 이번 달 1일 00:00(로컬) ~ 지금. 31일 제한 안에 든다.
- `AnthropicCostParser.parseHourly(json:timeZone:now:)` — Analytics 시간 버킷의 센트 문자열 → Decimal
  달러, 로컬 날짜로 묶어 이번 달·오늘 분리, `data_refreshed_at` 보존.
- `AnthropicCostParser.parseByModel(json:)` — 모델별 합계 → 상위 3개.
- `OpenAICostParser.parse(json:timeZone:now:)` — `result`/`results` 둘 다, 달러 숫자, 같은 집계.
- `SubscriptionCostParser.claude(json:)`, `.codex(json:)` — 1단계 필드. 형태가 다르면 `nil`.
- `CostFormatter.text(_:localizer:)` — `$12.34`, 1000 이상은 `$1.2K`, 0.01 미만은 `<$0.01`.
- `CostBudget.alerts(cost:budget:now:timeZone:)` — 월 예산 80%·100% 알림 후보. id는
  `{provider}-cost-{80|100}-{yyyy-MM}` (로컬 달 단위로 한 번).
- `CostVisibility.shows(...)` — 아래 "금액 표시 조건"의 진리표. 화면·메뉴바·알림이 모두 이것 하나를 본다.

금액은 부동소수점을 쓰지 않는다(Swift `Decimal`, C# `decimal`). 센트 문자열을 Double로 바꾸면 합산에서
1센트 단위 오차가 난다.

구현된 2단계 모델(2026-11-03, `AdminCost.swift`/`AdminCost.cs`)은 위 초안보다 좁다. `ProviderCost`는 Admin API가
준 값만 담는다(`monthToDate`·`today`·`topItems`·`dataRefreshedAt`·`measuredAt`). 1단계 금액은 `ProviderUsage`에,
예산은 설정에 있으므로 `source`·`limit`·`creditBalance`를 두지 않았다. 조회 사이의 상태는 `CostState`(켜짐,
키 끝 4자리, 마지막 정상 값, 마지막 실패, 다음 조회 시각)가 들고, 진단 줄은 `CostDiagnostics`가 양쪽에서 같은
글자로 만든다.

## 수집

- 비용 조회는 사용량 폴링과 **별도 주기**로 돈다. `CostPolicy.nextFetch(after:succeeded:)` = 성공 후 10분,
  실패 후 5분. Analytics API는 몇 시간 단위로 갱신되므로 더 자주 물어도 값이 바뀌지 않는다. 1단계 값은
  사용량 응답에 같이 오므로 사용량 주기를 그대로 따른다.
- 한 번 조회할 때 `CostPeriod.requestRange`(로컬 이번 달 1일 00:00 ~ 지금)를 받는다. Claude는 시간 버킷
  요청과 모델별 요청 두 번, OpenAI는 `limit=31` 한 번. `has_more`/`next_page`는 처리한다.
- 429는 기존 `RateLimitBackoff`(키 지문 기반)를 재사용한다. 401·403은 "Admin 키가 거부됨"으로 분류한다.
- 실패하면 마지막 성공 값을 캐시로 보여주되, 사용량과 같은 규칙으로 표시를 구분한다(`캐시 데이터 사용
  중`). 금액은 이미 쓴 돈이라 시간이 지나도 틀리지 않으므로, 캐시 유지 기간은 사용량보다 길게(같은 로컬
  달 안) 둔다. 캐시된 "오늘"은 날짜가 바뀌면 버린다.
- 히스토리: 하루 마감 금액만 남긴다(일 단위 막대 차트용, 4단계 후보). 이번 범위에서는 저장하지 않는다.

구현하면서 정한 것(2026-11-03):

- Anthropic의 한도는 키별이 아니라 **조직 전체 분당 60회**이고, 그 조직이 Admin API로 하는 다른 일과 나눠 쓴다.
  한 번 조회에 보통 2회(시간 버킷 1쪽 + 모델별 1쪽)다.
- 이번 달 범위가 정시 단위로 내림해 1시간이 안 되면(달의 첫 시간) Anthropic에는 묻지 않는다. API가 양 끝을
  정시로 내리므로 빈 범위가 된다. 이때는 "집계 전"이다.
- `data_refreshed_at`이 이번 달 1일 00:00(로컬)보다 이르면 이번 달 금액도 "집계 전"(nil)이다. $0으로 보이면
  안 된다는 규칙을 달에도 적용했다.
- 모델별 요청은 이번 달 금액이 있을 때만 한다.
- 페이지는 최대 10쪽까지 따라간다. 그보다 많으면 커서가 맴도는 것으로 보고 실패로 처리한다. 일부 페이지만
  더하면 청구액보다 작은 숫자가 정상처럼 보이기 때문이다. 커서가 만료되면(410) 그 회차는 실패로 두고
  5분 뒤 처음부터 다시 묻는다.
- 쿨다운 파일은 제공자별로 사용량과 따로 둔다(`claude-cost-rate-limit.json`, `codex-cost-rate-limit.json`).
- 마지막 정상 값은 `cost-cache.json`에 키 지문(키가 아니라 해시)과 함께 저장한다. 키가 바뀌면 다른 조직일 수
  있으므로 그 값을 버리고 일정과 상관없이 바로 조회한다.
- 토글이 꺼진 제공자는 키를 읽지도 않는다. 키를 넣지 않은 사용자의 키체인·키 파일은 건드리지 않는다.

## 자격 증명 저장 (2단계)

Admin 키는 조직 관리 권한(키 생성·삭제, 멤버 관리)까지 가진 강한 키다.

- macOS: 앱 소유 키체인 항목(`service: "Token Tracker"`, `account: "anthropic-admin"` /
  `"openai-admin"`, `kSecAttrAccessibleWhenUnlockedThisDeviceOnly`). 파일에 쓰지 않는다.
- Windows: DPAPI(`ProtectedData`, CurrentUser)로 암호화해 `%APPDATA%\Token Tracker\admin-keys.dat`.
  `System.Security.Cryptography.ProtectedData` 패키지가 필요하다.
- 진단 정보·로그·알림에는 키를 넣지 않는다. 설정 화면과 진단에는 `…ab12`(끝 4자리)와 마지막 조회 결과만.
- 설정 화면에 안내를 둔다: 이 앱 전용 키를 따로 만들고, Anthropic은 만료일을 걸 것(문서 권장).
  읽기 전용 권한을 고를 수 있는 제공자라면 그것을 쓰라고 안내한다(구현 시 각 Console에서 확인).
- 약관: 사용자가 자기 API 키를 관리하는 것은 Claude Code 약관이 허용하는 범위다. 구독 로그인
  정보(Claude.ai 세션·OAuth)를 앱이 보관하는 것과는 다르다.
- macOS 키체인 항목은 로그인 키체인(파일 기반)에 들어간다. 데이터 보호 키체인은 팀 ID가 있는 서명과
  권한(entitlement)이 필요한데, 이 앱은 ad-hoc 서명이다. 그래서 항목 접근 권한이 앱의 코드 서명에 묶이고,
  서명이 바뀌는 업데이트 뒤에는 다시 승인이 필요할 수 있다. 백그라운드 비용 갱신은 인증 UI를 금지하고,
  사용자가 설정의 `Admin 키…`를 열었을 때만 macOS가 승인을 요청한다.
  이 머신의 키체인으로 저장·읽기·삭제를 확인했다(2026-11-03, 별도 서비스 이름의 임시 항목).

## 설정

| 키 (macOS `Settings` / Windows `AppSettings`) | 기본값 | 설명 |
|---|---|---|
| `claudeCostEnabled` / `ClaudeCostEnabled` | false | Claude Admin API 비용 조회 (키가 있어야 의미) |
| `codexCostEnabled` / `CodexCostEnabled` | false | OpenAI Admin API 비용 조회 |
| `claudeMonthlyBudget` / `ClaudeMonthlyBudget` | 0 | 월 예산(USD, 0이면 끔) |
| `codexMonthlyBudget` / `CodexMonthlyBudget` | 0 | 월 예산(USD, 0이면 끔) |

1단계 금액은 따로 켜고 끄는 설정이 없다. 추가 사용 금액을 켠 사용자에게만 자동으로 나온다(아래 "금액
표시 조건"). 기존 `claudeEnabled`·`codexEnabled`는 "구독 한도 표시"로 의미를 좁힌다. API만 쓰는 사용자는 구독 한도를
끄고 비용만 켤 수 있어야 `로그인 필요` 카드가 사라진다. 키 이름은 바꾸지 않으므로 마이그레이션은 없다.

설정 화면 배치(양쪽): 제공자 섹션의 각 제공자 아래에 "구독 한도" 토글, "API 비용" 토글 + `Admin 키…`
버튼(시트에서 붙여넣기·삭제·마지막 4자리 표시), "월 예산" 숫자 입력.

## 화면

### 금액 표시 조건

금액(금액 줄, 예산 막대, 메뉴바 금액, 트레이 금액, 금액 알림)은 **그 사용자가 실제로 돈을 내는 경로가 있을
때만** 나온다. 구독만 쓰고 추가 사용 금액을 꺼 둔 사용자에게는 이 기능이 없는 것과 같다. 설정 화면의
"API 비용" 항목(키 입력)만 남는다.

| 제공자 | 금액을 보여주는 경우 | 근거 필드 |
|---|---|---|
| Claude 구독 | 추가 사용량이 켜져 있음 | `spend.enabled == true` 또는 `extra_usage.is_enabled == true` |
| Codex 구독 | 크레딧을 갖고 있음 | `credits.has_credits == true` (잔액이 숫자로 읽힐 때) |
| Claude·Codex API | Admin 키가 있고 "API 비용"을 켰음 | 설정 + 저장된 키 |

- 셋 다 아니면 그 제공자 카드에는 금액 영역이 없고, 메뉴바·트레이에도 금액이 없으며, 금액 알림도 없다.
- 판단은 매 조회마다 다시 한다. 사용자가 추가 사용량을 켜거나 끄면 다음 조회에 바로 반영된다.
- 필드가 없거나 형태가 다르면 "꺼짐"으로 본다(모르는 상태에서 금액을 지어내지 않는다).
- 2026-11-03 확인한 Pro 계정 응답(`spend.enabled: false`, `extra_usage.is_enabled: false`,
  `user_disabled: true`)은 표시하지 않는 경우다. 이 사용자의 화면은 지금과 똑같아야 한다.

### macOS 팝업

금액 표시 조건을 만족하는 제공자만, 카드 안의 기존 5h·7d 막대 아래에 금액 영역을 둔다. 구독 한도가 꺼진
API 사용자는 금액 영역만 있는 카드가 된다.

```
Claude  [api]                                   ● 정상
  이번 달 사용                               $42.18
  ▓▓▓▓▓▓▓▓░░░░░░░░  예산 $100 중 42%
  오늘 $3.07 · Opus 71% · Sonnet 27%     14:20 기준
Codex   [plus]                                  ● 정상
  5시간 ... 7일 ...                             (기존)
  크레딧 잔액  $18.40
```

- 기준 시각(`14:20 기준`)은 기기 시간대의 시각이다. 오늘 날짜가 아니면 `11월 2일 22:10 기준`처럼 날짜를 붙인다.

- 금액 숫자는 오른쪽 정렬, 기존 퍼센트와 같은 굵기.
- 예산이 없으면 막대를 그리지 않는다. 80% 이상이면 주황, 100% 이상이면 빨강(기존 `UsageLevel` 규칙과 같은
  색 체계).
- 1단계 Claude 추가 사용량: `추가 사용량  $1.86 / $5.00` + 막대(한도 대비).
- 상태 배지: Admin API 값은 `정상`, 캐시는 `캐시 데이터 사용 중`, 키 거부는 `Admin 키 거부됨`(빨강).
- 데이터 반영 시각: Claude는 `data_refreshed_at`을 `14:20 기준`으로 항상 보여준다. OpenAI는 응답에 반영
  시각이 없으므로 조회 시각만 도움말에 둔다.

### 메뉴바

- 구독만 쓰고 추가 사용 금액을 꺼 둔 사용자는 지금과 완전히 같다.
- 구독 한도가 켜져 있으면 지금과 같다(퍼센트). 추가 사용량·크레딧 금액은 메뉴바에 나오지 않는다(결정 3).
- 구독 한도가 꺼지고 API 비용만 켜진 제공자는 이번 달 금액을 쓴다: `Cl $42`, 로고 모드면 로고 + `$42`.
  둘 다 비용만이면 `Cl $42 · Cdx $8`.
- 예산 80% 이상이면 기존 7일 경고처럼 강조색을 쓴다.

### Windows

- 금액 표시 조건을 만족하는 제공자만, 트레이 메뉴 하위 메뉴에 `이번 달 사용: $42.18`, `오늘: $3.07`,
  `예산: $100 중 42%`, `14:20 기준`, 상위 항목 줄을 추가한다.
- 툴팁 첫 줄: 구독 한도가 없는 제공자는 `Claude $42 (이번 달)`.
- 트레이 아이콘(16×16)은 퍼센트를 그리는 자리라 금액을 그리지 않는다. 구독 한도가 모두 꺼져 있으면
  아이콘에 `$`를 그린다.

### 알림

- 금액 알림도 금액 표시 조건을 따른다. 조건을 만족하지 않는 제공자에게는 금액 알림이 없다.
- 월 예산 80%, 100% 도달 시 제공자별로 한 번씩(같은 로컬 달 안에서는 다시 보내지 않는다).
- 1단계: Claude `spend.severity`가 `normal`이 아니게 바뀌면 한 번, Codex `overage_limit_reached`가 true가 되면
  한 번.
- 기존 알림처럼 `알림 받기`가 꺼져 있으면 보내지 않는다.

## 현지화

새 키(사전 4개 + enum 2개): `costThisMonth` "이번 달 사용" / "This month", `costToday` "오늘" / "Today",
`costAsOf` "{time} 기준" / "as of {time}", `costNotYetTallied` "집계 전" / "Not tallied yet",
`costBudget` "예산 {limit} 중 {percent}%", `costExtraUsage` "추가 사용량", `costCreditBalance`
"크레딧 잔액", `costAdminKeyRejected` "Admin 키 거부됨", `costBudgetAlertTitle`, `costBudgetAlertBody`
"{provider} 이번 달 사용 {amount} — 예산의 {percent}%", 설정 문구들. 금액 형식(`$`, 천 단위 `K`)은
언어와 무관하게 같다. 시각과 날짜는 기기 지역 설정의 형식(12시간/24시간)을 따른다.

## 테스트

양쪽 Core에 같은 진리표:

- 파서: 문서 예시를 본뜬 고정 JSON — Analytics 센트 문자열(`"41280.000000"` → $412.80), 시간 버킷,
  `data_refreshed_at`(값·null), 여러 페이지, OpenAI `result`/`results` 둘 다, 통화 대소문자.
- 시간대 경계: 같은 시간 버킷 묶음을 여러 시간대로 집계해 "오늘"·"이번 달"이 각 시간대의 자정에서
  갈리는지. 한국(+9), 미국 동부(서머타임 시작·끝 날 23·25시간), 인도(+5:30, 정시 내림 허용 오차),
  UTC. 달 마지막 날 → 다음 달 1일.
- 반영 지연: `data_refreshed_at`이 오늘 자정보다 이르면 "오늘"은 `집계 전`(nil).
- 합산: Decimal로 31일 × 24시간 × 여러 항목을 더해 센트 오차가 없는지.
- 예산 알림: 79/80/100%, 예산 0, 같은 달 중복 억제, 달이 바뀌면 재무장.
- 금액 표시 조건: 지금 실측한 Pro 응답(추가 사용량 꺼짐) → 아무 데도 표시 없음, 켜짐(가정한 형태) → 표시,
  필드 없음·이상한 형태 → 표시 없음, Codex `has_credits` false → 없음, API 비용은 키·토글 둘 다 있어야
  표시. 팝업·메뉴바·트레이·알림이 같은 판단을 쓰는지.
- 클라이언트: 가짜 HTTP 핸들러로 401·403·429·200 (Windows는 `QueueHttpMessageHandler`, macOS는
  `HTTPClient` 주입).

실제 검증에는 Admin 키가 필요하다. 개발자 계정에 Anthropic 조직과 OpenAI 조직의 Admin 키를 각각 만들어
테스트하고, 키는 키체인/DPAPI에만 넣는다.

1단계 Claude 추가 사용량은 켜진 계정의 응답을 아직 보지 못했다. 그래도 화면에 연결했다(2026-11-03): 금액을
읽는 `spend.used`의 형태(`amount_minor`·`currency`·`exponent`)는 꺼진 계정의 실측 응답에도 있고, 켜짐 판정
필드나 금액 형태가 다르면 파서가 `nil`을 돌려 아무것도 표시하지 않는 쪽으로 실패한다. 켜진 계정에서 금액이
틀리게 보이면 진단 정보의 `Claude money past plan` 줄로 확인한다. Codex는 2026-11-03 Plus 계정 실측 응답이
`credits.has_credits: false`, `balance: "0"`이라 표시하지 않는 경우다.

## 구현 순서 (PR 단위)

1. Core: `CostAmount`·파서·포매터·시간대 경계·표시 조건·예산 알림 + 양쪽 진리표 (UI 없음).
2. 1단계: Codex 크레딧 잔액, Claude 추가 사용량. 둘 다 표시 조건 뒤에 있으므로, 이 개발 계정(둘 다 꺼짐)에서는
   화면이 바뀌지 않는 것을 확인한다. 1단계 알림(`spend.severity`, `overage_limit_reached`)은 5번과 같이 한다.
3. 2단계 수집: Admin 키 저장(macOS 키체인, Windows DPAPI), 클라이언트 두 개, 주기·캐시·백오프, 진단 줄.
   설정 키 `claudeCostEnabled`·`codexCostEnabled`(기본 꺼짐)도 여기서 넣었다. 화면이 없어 키를 넣을 방법이
   없으므로 사용자 화면은 그대로다. 실제 Admin 키로는 아직 확인하지 않았다.
4. 2단계 화면: 설정(토글·키 시트·예산), 팝업 카드, 메뉴바 규칙, Windows 메뉴·툴팁.
   구현하면서 정한 것(2026-11-03, `CostDisplay.swift`/`CostDisplay.cs`):
   - 화면 문구는 `AdminCostText.view`가 양쪽에서 같은 글자로 만든다. 키가 없거나 꺼져 있으면 `nil`이고, 모든
     화면이 그것을 본다.
   - 시각은 양쪽 모두 24시간 `HH:mm`, 다른 날이면 `Nov 2 22:10` / `11월 2일 22:10`. 위 "현지화"의 "기기 지역
     설정의 형식"을 따르지 않았다. 두 포트가 같은 글자를 내고 진리표로 확인할 수 있게 하려는 것이다.
   - 상태 우선순위: 키 거부 > 캐시(실패 + 이전 값) > 요청 제한 > 실패 > 불러오는 중(값 없음) > 정상.
   - 상위 항목은 이번 달 금액 대비 비율로 둘까지. Anthropic 모델 이름은 `claude-opus-5-5` → `Opus 5.5`로 줄인다.
     OpenAI 라인 아이템은 `gpt-6, input`처럼 `모델, 종류`라고 **가정**하고 쉼표 앞으로 묶어 더한다(실제 응답으로
     아직 확인하지 않았다. 쉼표가 없으면 그대로 둔다).
   - 메뉴바 금액은 $10 이상이면 달러 단위(`$42`), 미만이면 센트까지(`$3.07`), 1000 이상은 `$1.2K`.
     `가장 낮은 잔량` 모드는 퍼센트가 하나라도 있으면 그대로 `AI 63%`이고, 퍼센트가 하나도 없을 때만 금액을
     나열한다(`Cl $42 · Cdx $8.00`).
   - 월 예산은 정수 달러(`claudeMonthlyBudget`·`codexMonthlyBudget` / `ClaudeMonthlyBudget`·`CodexMonthlyBudget`,
     기본 0).
   - Admin 키 시트: macOS는 `NSAlert` + 보안 입력란, Windows는 작은 대화 상자. 키체인·키 파일은 시트를 열 때만
     읽는다. 붙여넣은 키가 `sk-ant-admin`/`sk-admin-`으로 시작하지 않으면 저장하지 않는다(일반 API 키는 비용을
     못 읽고, 붙여넣기 실수로 가장 흔할 것이다). 키를 저장하면 그 제공자의 `API 비용`을 켠다. 실제 키로 시험할 때
     키만 저장하고 토글을 켜지 않아 아무 일도 일어나지 않았다(2026-11-03).
   - 메뉴바 앱이라 주 메뉴가 없어 입력란에서 ⌘V가 동작하지 않았다. 보이지 않는 편집 메뉴를 넣어 해결했다.
   - OpenAI 조회 시각 도움말은 넣지 않았다. 반영 시각(`14:20 기준`)은 Claude만 보여준다.
   - 기존 `claudeEnabled`·`codexEnabled` 토글은 설정 화면에서 `구독 한도`로 이름을 바꿨다(키는 그대로).
   - 2026-11-03 개발 빌드를 `/Applications`에 설치해 확인했다. API 비용이 꺼져 있으면 팝업은 이전과 같았고,
     설정 화면과 키 시트(일반 API 키 거부 포함)가 의도대로 나왔다. 실제 Anthropic 조직 Admin 키로 조회가
     성공해 카드에 `이번 달 사용 $0.00`, `오늘 $0.00`, `22:49 기준`이 나왔다(그 조직의 이번 달 API 사용이 0).
     금액이 0이 아닌 경우, 예산 막대, 구독 한도를 끈 카드·메뉴바, OpenAI는 아직 보지 못했다.
5. 예산 알림, 문서(README·WORK_SUMMARY), 릴리스.
   구현하면서 정한 것(2026-11-04, `CostAlerts.swift`/`CostAlerts.cs`):
   - 금액 알림은 `CostAlertEvaluator` 하나가 정한다. 알림 id는 로컬 달(`yyyy-MM`)로 끝나고, 보낸 id는 설정
     (`deliveredCostAlerts` / `DeliveredCostAlerts`)에 남긴다. 기존 사용량 알림처럼 메모리에만 두면, 앱을 켤 때마다
     비용 캐시가 같은 값을 되살려 예산 알림을 매일 다시 보낸다. 달이 바뀐 id는 읽을 때 버린다.
   - 예산: 닿은 기준 가운데 가장 높은 것 하나만 보낸다. 한 번에 80%와 100%를 넘기면 100%만 보내고 80%는 보낸 것으로
     친다.
   - Claude 추가 사용량: `spend.severity`가 `normal`이 아니면, 그 단어를 id에 넣어 단계마다 달에 한 번. 추가 사용량이
     꺼진 계정(`extraUsage`가 없음)에는 severity 값과 상관없이 보내지 않는다.
   - Codex: `credits.has_credits`와 `overage_limit_reached`가 둘 다 true일 때만 한도 도달로 읽는다
     (`ProviderUsage.creditLimitReached`). 크레딧이 없는 계정의 플래그는 구독 한도 소진일 수도 있어 금액 알림으로
     쓰지 않는다. 히스토리에는 남기지 않는다.
   - `알림 받기`가 꺼져 있으면 보내지도, 보낸 것으로 기록하지도 않는다. 그래서 같은 달에 다시 켜면 그때 맞는 알림이 간다.
   - 켜진 계정의 severity 값이나 overage 플래그가 실제로 어떻게 오는지는 아직 보지 못했다(`normal` 외의 단어를 모른다).

## 대안: 로컬 로그 추정 (3단계, 보류)

Claude Code의 `~/.claude/projects/**/*.jsonl`과 Codex의 세션 로그에서 토큰 수를 읽어 가격표로 곱하는
방식(ccusage와 같음). 키가 필요 없지만 이 기기에서 CLI로 쓴 것만 잡히고, 가격표를 양쪽 Core에서 계속
맞춰야 하며, 청구서와 다르다. 상태줄의 `cost.total_cost_usd`도 세션 단위 추정치라(`/clear`하면 0) 월 합계로는
쓸 수 없다. 2단계를 쓸 수 없는 사용자(조직을 만들 수 없거나 Admin 키를 넣기 싫은 경우)를 위해, 2단계 이후에
필요하면 `추정` 배지를 달아 추가한다.

## 결정 (2026-11-03, 추천안으로 확정)

1. **Claude 데이터 출처: A — Analytics API 시간 버킷.** 로컬 경계가 정확하고, 4~24시간 지연은 `14:20 기준`과
   `집계 전`으로 드러낸다. 지연이 불편해지면 B(기존 API 금액을 시간별 토큰 비율로 배분)로 올린다 — 파서와
   집계만 바뀐다. C(일 단위만)는 쓰지 않는다.
2. **OpenAI 하루 버킷이 UTC 자정 기준이면: a — "이번 달"만 보여주고 "오늘"은 숨긴다.** 버킷 시작이 로컬
   자정과 정확히 같을 때만 "오늘"을 계산하므로, 실제 정렬이 어느 쪽이든 코드는 같다.
3. **메뉴바: 구독 한도가 켜진 제공자는 금액을 메뉴바에 보여주지 않는다.** 금액은 팝업에만 있다. 구독 한도가
   꺼지고 API 비용만 켜진 제공자만 메뉴바에 금액을 쓴다.
4. **예산 알림: 80%와 100%.**
5. **Admin 키: macOS 키체인 / Windows DPAPI에 보관**하고, 설정 화면에서 이 앱 전용 키와 만료일 설정을
   안내한다. 진단·로그·알림에는 끝 4자리만.
6. **통화: USD 그대로.** 환율 조회는 하지 않는다(앱이 요청하는 곳을 늘리지 않는다).
7. **로컬 로그 추정(3단계): 2단계 이후 필요해지면.**

## 참고

- [Usage and Cost API](https://platform.claude.com/docs/en/manage-claude/usage-cost-api)
- [Usage and Cost Analytics API](https://platform.claude.com/docs/en/manage-claude/usage-cost-analytics-api)
- [OpenAI Costs API](https://platform.openai.com/docs/api-reference/usage/costs) ·
  [OpenAPI 명세](https://github.com/openai/openai-openapi)
- [Claude Code 상태줄 — cost 필드](https://code.claude.com/docs/en/statusline)
- [Claude Code — Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)
