import Foundation

public enum AppLanguage: String, CaseIterable {
    case system
    case english
    case korean

    public var label: String {
        switch self {
        case .system: "System"
        case .english: "English"
        case .korean: "한국어"
        }
    }
}

public struct Localizer {
    public let language: AppLanguage

    public init(language: AppLanguage) {
        if language == .system {
            let preferred = Locale.preferredLanguages.first ?? "en"
            self.language = preferred.lowercased().hasPrefix("ko") ? .korean : .english
        } else {
            self.language = language
        }
    }

    public func text(_ key: L10nKey) -> String {
        switch language {
        case .korean:
            return korean[key] ?? english[key] ?? key.rawValue
        case .system, .english:
            return english[key] ?? key.rawValue
        }
    }

    private let english: [L10nKey: String] = [
        .noUsageLoaded: "No usage loaded yet",
        .updated: "Updated",
        .refreshNow: "Refresh Now",
        .preferences: "Preferences...",
        .displayMode: "Display Mode",
        .providerLabelStyle: "Provider Labels",
        .providers: "Providers",
        .refreshInterval: "Refresh Interval",
        .language: "Language",
        .launchAtLogin: "Launch at Login",
        .quit: "Quit",
        .windowNotOpen: "none yet (opens on first use)",
        .windowNotReported: "not reported",
        .error: "Error",
        .technicalError: "Technical error",
        .diagnostics: "Diagnostics",
        .copyDiagnostics: "Copy Diagnostics",
        .openClaudeCredentials: "Open Claude Credentials",
        .openCodexAuth: "Open Codex Auth",
        .lastSuccessfulUpdate: "Last successful update",
        .noSuccessfulUpdate: "No successful update yet",
        .duplicateInstances: "Running instances",
        .refreshIntervalWarning: "Short refresh intervals can trigger provider rate limits.",
        .notifications: "Notifications",
        .fiveHourAlertTitle: "5h usage is low",
        .sevenDayAlertTitle: "7d usage is low",
        .resetAlertTitle: "Usage window reset soon",
        .resetAlertBody: "{provider} {window} resets in {time} with {percent}% unused",
        .stalledAlertTitle: "Usage stopped updating",
        .stalledAlertBody: "{provider} last measured {time} ago",
        .fiveHourAlertThreshold: "5h alert threshold",
        .sevenDayAlertThreshold: "7d alert threshold",
        .resetAlertMinutes: "Reset alert window",
        .history: "History",
        .historyTrend: "24h trend:",
        .notEnoughHistory: "Not enough history yet",
        .exportHistoryCSV: "Export History CSV...",
        .historyRetentionDays: "History retention",
        .staleToleranceHours: "Keep last reading for",
        .forecastLabel: "Projected depletion",
        .forecastBeforeReset: "empties before reset",
        .depletionAlertTitle: "May run out before reset",
        .depletionAlertBody: "{provider} {window}: ~{time} → 0% (before reset)",
        .pace: "Pace",
        .paceAhead: "ahead",
        .paceOnTrack: "on track",
        .paceBehind: "under",
        .freedUpAlertTitle: "Usage available again",
        .freedUpAlertBody: "{provider} {window} has reset: {percent}% left",
        .updateAvailable: "New version {version}",
        .downloadUpdate: "Download",
        .checkForUpdates: "Check for updates",
        .renewClaudeTokenWithCLI: "Auto-renew token via Claude Code",
        .showForecastLabel: "Show depletion forecast",
        .showResetCountdownLabel: "Show time until reset in the menu bar",
        .depletionAlertToggle: "Depletion alert (before reset)",
        .pausePolling: "Pause updates",
        .pause1h: "For 1 hour",
        .pause3h: "For 3 hours",
        .pauseUntilResumed: "Until I resume",
        .resumeNow: "Resume now",
        .updatesPaused: "Updates paused",
        .launchFailed: "Launch at Login failed",
        .statusEnabled: "Enabled",
        .statusDisabled: "Disabled",
        .statusNotFound: "App bundle not found",
        .statusRequiresApproval: "Requires approval in System Settings",
        .statusUnknown: "Unknown",
        .statusOK: "OK",
        .statusOKDetail: "Usage loaded normally.",
        .statusDisabledProvider: "Disabled",
        .statusDisabledProviderDetail: "This provider is turned off.",
        .statusRateLimited: "Rate limited",
        .statusRateLimitedDetail: "The provider temporarily rejected usage checks because requests were too frequent.",
        .statusMissingCredentials: "Login required",
        .statusMissingCredentialsDetail: "Tokki could not find local credentials for this provider.",
        .statusExpiredCredentials: "Saved token expired",
        .statusExpiredCredentialsDetail: "The saved access token has expired. Tokki reads the token the provider's CLI stores and cannot refresh it itself.",
        .credentialStale: "Credentials not refreshed in {age}",
        .statusInvalidResponse: "Unexpected response",
        .statusInvalidResponseDetail: "The provider returned data in a format Tokki did not expect.",
        .statusTimedOut: "Timed out",
        .statusTimedOutDetail: "The provider did not respond before the request timed out.",
        .statusNetworkIssue: "Network issue",
        .statusNetworkIssueDetail: "The request failed before a usable provider response was received.",
        .statusHTTPError: "Provider HTTP error",
        .statusHTTPErrorDetail: "The provider returned an HTTP error while checking usage.",
        .statusUsingCachedData: "Using cached data",
        .statusUsingCachedDataDetail: "The latest check failed, so the last successful value is being shown.",
        .statusFromStatusLine: "From status line",
        .statusFromStatusLineDetail: "Claude Code's status line reported this usage.",
        .statusUnavailable: "Unavailable",
        .statusUnavailableDetail: "No usage value is currently available.",
        .recoveryEnableProvider: "Enable the provider if you want Tokki to check it again.",
        .recoveryWaitForCooldown: "Wait for the cooldown to pass or use a longer refresh interval.",
        .recoverySignInClaude: "Run `claude` and type `/login` to sign in.",
        .recoverySignInCodex: "Run `codex login` to sign in.",
        .recoverySignInAgainClaude: "Starting `claude` refreshes the saved token. If it still fails, run `/login` inside it.",
        .recoverySignInAgainCodex: "Send one request in `codex` to refresh the saved token. If it still fails, run `codex login`.",
        .recoveryUpdateOrTryLater: "Try again later. If this keeps happening, update Tokki.",
        .recoveryCheckNetwork: "Check your network connection and try again.",
        .recoveryRefreshLater: "Refresh again later.",
        .recoveryTryAgainLater: "Try again later.",
        .now: "now",
        .ago: "ago",
        .general: "General",
        .display: "Display",
        .data: "Data",
        .enableNotifications: "Send notifications",
        .fiveHourWindow: "5-hour",
        .sevenDayWindow: "7-day",
        .resetsIn: "resets in {time}",
        .more: "More",
        .lowestRemaining: "Lowest remaining",
        .both: "Claude + Codex",
        .codexOnly: "Codex only",
        .claudeOnly: "Claude only",
        .officialLogos: "Official logos",
        .costExtraUsage: "Extra usage",
        .costCreditBalance: "Credit balance",
        .costThisMonth: "This month",
        .costToday: "Today",
        .costAsOf: "as of {time}",
        .costNotYetTallied: "not tallied yet",
        .costBudget: "{percent}% of {limit} budget",
        .costLoading: "Loading",
        .costAdminKeyRejected: "Admin key rejected",
        .costFetchFailed: "Couldn't get cost",
        .subscriptionLimits: "Subscription limits",
        .apiCost: "API cost (Admin API)",
        .adminKeyButton: "Admin Key…",
        .monthlyBudget: "Monthly budget (USD, 0 is off)",
        .adminKeyTitle: "{organization} Admin Key",
        .adminKeyGuidance: "Create a key just for this app in your organization's console and paste it here. It is stored encrypted on this computer only. Give an Anthropic key an expiry date.",
        .adminKeyStored: "Stored key: {suffix}",
        .adminKeyNone: "No key stored",
        .adminKeySave: "Save",
        .adminKeyDelete: "Delete Key",
        .adminKeyCancel: "Cancel",
        .adminKeyInvalid: "This isn't an Admin key. Admin keys look like {prefix}…",
        .adminKeySaveFailed: "The key couldn't be saved.",
        .costSectionTitle: "{organization} API cost",
        .costBudgetAlertTitle: "API cost budget",
        .costBudgetAlertBody: "{organization} API: {amount} this month, {percent}% of the {budget} budget",
        .extraUsageAlertTitle: "Claude extra usage",
        .extraUsageAlertBody: "Extra usage is at {value}.",
        .creditLimitAlertTitle: "Codex credits",
        .creditLimitAlertBody: "Codex has reached its credit overage limit."
    ]

    private let korean: [L10nKey: String] = [
        .noUsageLoaded: "아직 사용량을 불러오지 못했습니다",
        .updated: "업데이트",
        .refreshNow: "지금 새로고침",
        .preferences: "설정...",
        .displayMode: "표시 방식",
        .providerLabelStyle: "제공자 표기",
        .providers: "제공자",
        .refreshInterval: "새로고침 간격",
        .language: "언어",
        .launchAtLogin: "로그인 시 실행",
        .quit: "종료",
        .windowNotOpen: "아직 없음(첫 사용 시 시작)",
        .windowNotReported: "제공되지 않음",
        .error: "오류",
        .technicalError: "기술 오류",
        .diagnostics: "진단",
        .copyDiagnostics: "진단 정보 복사",
        .openClaudeCredentials: "Claude 인증 파일 열기",
        .openCodexAuth: "Codex 인증 파일 열기",
        .lastSuccessfulUpdate: "마지막 성공 업데이트",
        .noSuccessfulUpdate: "아직 성공한 업데이트 없음",
        .duplicateInstances: "실행 중인 인스턴스",
        .refreshIntervalWarning: "짧은 새로고침 간격은 제공자 요청 제한을 유발할 수 있습니다.",
        .notifications: "알림",
        .fiveHourAlertTitle: "5시간 사용량 부족",
        .sevenDayAlertTitle: "7일 사용량 부족",
        .resetAlertTitle: "사용량 창 리셋 임박",
        .resetAlertBody: "{provider} {window} {time} 후 리셋 — {percent}% 남음",
        .stalledAlertTitle: "사용량 갱신 멈춤",
        .stalledAlertBody: "{provider} 마지막 측정 {time} 전",
        .fiveHourAlertThreshold: "5시간 알림 기준",
        .sevenDayAlertThreshold: "7일 알림 기준",
        .resetAlertMinutes: "리셋 알림 시간",
        .history: "히스토리",
        .historyTrend: "24시간 추세:",
        .notEnoughHistory: "아직 히스토리가 부족합니다",
        .exportHistoryCSV: "히스토리 CSV 내보내기...",
        .historyRetentionDays: "히스토리 보관 기간",
        .staleToleranceHours: "마지막 값 유지 기간",
        .forecastLabel: "예상 소진",
        .forecastBeforeReset: "리셋 전 소진",
        .depletionAlertTitle: "리셋 전 소진 예상",
        .depletionAlertBody: "{provider} {window}: ~{time} → 0% (리셋 전)",
        .pace: "페이스",
        .paceAhead: "빠름",
        .paceOnTrack: "적정",
        .paceBehind: "여유",
        .freedUpAlertTitle: "다시 사용 가능",
        .freedUpAlertBody: "{provider} {window} 리셋됨 — 잔량 {percent}%",
        .updateAvailable: "새 버전 {version}",
        .downloadUpdate: "다운로드",
        .checkForUpdates: "업데이트 확인",
        .renewClaudeTokenWithCLI: "Claude Code로 토큰 자동 갱신",
        .showForecastLabel: "소진 예측 표시",
        .showResetCountdownLabel: "메뉴바에 리셋까지 남은 시간 표시",
        .depletionAlertToggle: "소진 예측 알림 (리셋 전)",
        .pausePolling: "업데이트 일시중지",
        .pause1h: "1시간",
        .pause3h: "3시간",
        .pauseUntilResumed: "재개할 때까지",
        .resumeNow: "지금 재개",
        .updatesPaused: "업데이트 일시중지됨",
        .launchFailed: "로그인 시 실행 설정 실패",
        .statusEnabled: "켜짐",
        .statusDisabled: "꺼짐",
        .statusNotFound: "앱 번들을 찾을 수 없음",
        .statusRequiresApproval: "시스템 설정에서 승인 필요",
        .statusUnknown: "알 수 없음",
        .statusOK: "정상",
        .statusOKDetail: "사용량을 정상적으로 불러왔습니다.",
        .statusDisabledProvider: "꺼짐",
        .statusDisabledProviderDetail: "이 제공자는 비활성화되어 있습니다.",
        .statusRateLimited: "요청 제한",
        .statusRateLimitedDetail: "요청이 너무 잦아 제공자가 사용량 확인을 일시적으로 거절했습니다.",
        .statusMissingCredentials: "로그인 필요",
        .statusMissingCredentialsDetail: "이 제공자의 로컬 인증 정보를 찾지 못했습니다.",
        .statusExpiredCredentials: "저장된 토큰 만료",
        .statusExpiredCredentialsDetail: "저장된 액세스 토큰이 만료되었습니다. Tokki는 제공자 CLI가 저장한 토큰을 읽기만 하며 직접 갱신하지 않습니다.",
        .credentialStale: "자격 증명이 {age}째 갱신되지 않음",
        .statusInvalidResponse: "예상과 다른 응답",
        .statusInvalidResponseDetail: "제공자가 Tokki가 예상하지 못한 형식의 데이터를 반환했습니다.",
        .statusTimedOut: "시간 초과",
        .statusTimedOutDetail: "요청 제한 시간 안에 제공자가 응답하지 않았습니다.",
        .statusNetworkIssue: "네트워크 문제",
        .statusNetworkIssueDetail: "사용 가능한 제공자 응답을 받기 전에 요청이 실패했습니다.",
        .statusHTTPError: "제공자 HTTP 오류",
        .statusHTTPErrorDetail: "사용량 확인 중 제공자가 HTTP 오류를 반환했습니다.",
        .statusUsingCachedData: "캐시 데이터 사용 중",
        .statusUsingCachedDataDetail: "최신 확인에 실패해 마지막 성공 값을 표시하고 있습니다.",
        .statusFromStatusLine: "상태줄 데이터",
        .statusFromStatusLineDetail: "Claude Code 상태줄이 보고한 사용량입니다.",
        .statusUnavailable: "사용 불가",
        .statusUnavailableDetail: "현재 표시할 사용량 값이 없습니다.",
        .recoveryEnableProvider: "다시 확인하려면 제공자를 켜세요.",
        .recoveryWaitForCooldown: "쿨다운이 끝날 때까지 기다리거나 새로고침 간격을 늘리세요.",
        .recoverySignInClaude: "`claude`를 실행한 뒤 `/login`을 입력해 로그인하세요.",
        .recoverySignInCodex: "`codex login`을 실행해 로그인하세요.",
        .recoverySignInAgainClaude: "`claude`를 한 번 실행하면 저장된 토큰이 갱신됩니다. 그래도 안 되면 그 안에서 `/login`을 입력하세요.",
        .recoverySignInAgainCodex: "`codex`에서 요청을 한 번 보내면 저장된 토큰이 갱신됩니다. 그래도 안 되면 `codex login`을 실행하세요.",
        .recoveryUpdateOrTryLater: "나중에 다시 시도하세요. 계속 발생하면 Tokki를 업데이트하세요.",
        .recoveryCheckNetwork: "네트워크 연결을 확인한 뒤 다시 시도하세요.",
        .recoveryRefreshLater: "나중에 다시 새로고침하세요.",
        .recoveryTryAgainLater: "나중에 다시 시도하세요.",
        .now: "지금",
        .ago: "전",
        .general: "일반",
        .display: "표시",
        .data: "데이터",
        .enableNotifications: "알림 받기",
        .fiveHourWindow: "5시간",
        .sevenDayWindow: "7일",
        .resetsIn: "{time} 후 리셋",
        .more: "더 보기",
        .lowestRemaining: "가장 낮은 잔량",
        .both: "Claude + Codex",
        .codexOnly: "Codex만",
        .claudeOnly: "Claude만",
        .officialLogos: "공식 로고",
        .costExtraUsage: "추가 사용량",
        .costCreditBalance: "크레딧 잔액",
        .costThisMonth: "이번 달 사용",
        .costToday: "오늘",
        .costAsOf: "{time} 기준",
        .costNotYetTallied: "집계 전",
        .costBudget: "예산 {limit} 중 {percent}%",
        .costLoading: "불러오는 중",
        .costAdminKeyRejected: "Admin 키 거부됨",
        .costFetchFailed: "비용을 가져오지 못함",
        .subscriptionLimits: "구독 한도",
        .apiCost: "API 비용 (Admin API)",
        .adminKeyButton: "Admin 키…",
        .monthlyBudget: "월 예산 (USD, 0이면 끔)",
        .adminKeyTitle: "{organization} Admin 키",
        .adminKeyGuidance: "조직 Console에서 이 앱 전용 Admin 키를 새로 만들어 붙여넣으세요. 키는 이 컴퓨터에만 암호화해 저장됩니다. Anthropic 키는 만료일을 걸어 두세요.",
        .adminKeyStored: "저장된 키: {suffix}",
        .adminKeyNone: "저장된 키 없음",
        .adminKeySave: "저장",
        .adminKeyDelete: "키 삭제",
        .adminKeyCancel: "취소",
        .adminKeyInvalid: "Admin 키가 아닙니다. Admin 키는 {prefix}… 형식입니다.",
        .adminKeySaveFailed: "키를 저장하지 못했습니다.",
        .costSectionTitle: "{organization} API 비용",
        .costBudgetAlertTitle: "API 비용 예산",
        .costBudgetAlertBody: "{organization} API: 이번 달 {amount}, 예산 {budget}의 {percent}%",
        .extraUsageAlertTitle: "Claude 추가 사용량",
        .extraUsageAlertBody: "추가 사용량이 {value}입니다.",
        .creditLimitAlertTitle: "Codex 크레딧",
        .creditLimitAlertBody: "Codex 크레딧 초과 사용 한도에 도달했습니다."
    ]
}

public enum L10nKey: String {
    case noUsageLoaded
    case updated
    case refreshNow
    case preferences
    case displayMode
    case providerLabelStyle
    case providers
    case refreshInterval
    case language
    case launchAtLogin
    case quit
    case windowNotOpen
    case windowNotReported
    case error
    case technicalError
    case diagnostics
    case copyDiagnostics
    case openClaudeCredentials
    case openCodexAuth
    case lastSuccessfulUpdate
    case noSuccessfulUpdate
    case duplicateInstances
    case refreshIntervalWarning
    case notifications
    case fiveHourAlertTitle
    case sevenDayAlertTitle
    case resetAlertTitle
    case resetAlertBody
    case stalledAlertTitle
    case stalledAlertBody
    case fiveHourAlertThreshold
    case sevenDayAlertThreshold
    case resetAlertMinutes
    case history
    case historyTrend
    case notEnoughHistory
    case exportHistoryCSV
    case historyRetentionDays
    case staleToleranceHours
    case forecastLabel
    case forecastBeforeReset
    case depletionAlertTitle
    case depletionAlertBody
    case pace
    case paceAhead
    case paceOnTrack
    case paceBehind
    case freedUpAlertTitle
    case freedUpAlertBody
    case updateAvailable
    case downloadUpdate
    case checkForUpdates
    case renewClaudeTokenWithCLI
    case showForecastLabel
    case showResetCountdownLabel
    case depletionAlertToggle
    case pausePolling
    case pause1h
    case pause3h
    case pauseUntilResumed
    case resumeNow
    case updatesPaused
    case launchFailed
    case statusEnabled
    case statusDisabled
    case statusNotFound
    case statusRequiresApproval
    case statusUnknown
    case statusOK
    case statusOKDetail
    case statusDisabledProvider
    case statusDisabledProviderDetail
    case statusRateLimited
    case statusRateLimitedDetail
    case statusMissingCredentials
    case statusMissingCredentialsDetail
    case statusExpiredCredentials
    case statusExpiredCredentialsDetail
    case credentialStale
    case statusInvalidResponse
    case statusInvalidResponseDetail
    case statusTimedOut
    case statusTimedOutDetail
    case statusNetworkIssue
    case statusNetworkIssueDetail
    case statusHTTPError
    case statusHTTPErrorDetail
    case statusUsingCachedData
    case statusUsingCachedDataDetail
    case statusFromStatusLine
    case statusFromStatusLineDetail
    case statusUnavailable
    case statusUnavailableDetail
    case recoveryEnableProvider
    case recoveryWaitForCooldown
    case recoverySignInClaude
    case recoverySignInCodex
    case recoverySignInAgainClaude
    case recoverySignInAgainCodex
    case recoveryUpdateOrTryLater
    case recoveryCheckNetwork
    case recoveryRefreshLater
    case recoveryTryAgainLater
    case now
    case ago
    case general
    case display
    case data
    case enableNotifications
    case fiveHourWindow
    case sevenDayWindow
    case resetsIn
    case more
    case lowestRemaining
    case both
    case codexOnly
    case claudeOnly
    case officialLogos
    case costExtraUsage
    case costCreditBalance
    case costThisMonth
    case costToday
    case costAsOf
    case costNotYetTallied
    case costBudget
    case costLoading
    case costAdminKeyRejected
    case costFetchFailed
    case subscriptionLimits
    case apiCost
    case adminKeyButton
    case monthlyBudget
    case adminKeyTitle
    case adminKeyGuidance
    case adminKeyStored
    case adminKeyNone
    case adminKeySave
    case adminKeyDelete
    case adminKeyCancel
    case adminKeyInvalid
    case adminKeySaveFailed
    case costSectionTitle
    case costBudgetAlertTitle
    case costBudgetAlertBody
    case extraUsageAlertTitle
    case extraUsageAlertBody
    case creditLimitAlertTitle
    case creditLimitAlertBody
}
