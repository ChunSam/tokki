import Foundation
import TokenTrackerCore

func expectEqual<T: Equatable>(_ actual: T, _ expected: T, _ message: String) {
    if actual != expected {
        fputs("FAIL: \(message). expected \(expected), got \(actual)\n", stderr)
        exit(1)
    }
}

func expect(_ condition: Bool, _ message: String) {
    if !condition {
        fputs("FAIL: \(message)\n", stderr)
        exit(1)
    }
}

expectEqual(remainingPercent(fromUsed: 0), 100, "0 used leaves 100 remaining")
expectEqual(remainingPercent(fromUsed: 25.4), 75, "25.4 used rounds to 75 remaining")
expectEqual(remainingPercent(fromUsed: 100), 0, "100 used leaves 0 remaining")
expectEqual(remainingPercent(fromUsed: 120), 0, "remaining is clamped at 0")

try runClaudeUsageRegressionTests()
try runClaudeStatusLineTests()
try runClaudeCLIRefreshTests()
try runProcessDeadlineTests()
try runCostTests()
try runAdminCostTests()
try runCostDisplayTests()
try runCostAlertTests()
try runHistoryStoreTests()

let now = Date()
let snapshot = UsageSnapshot(
    claude: ProviderUsage(provider: .claude, remainingPercent5h: 63, remainingPercent7d: 80, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now),
    codex: ProviderUsage(provider: .codex, remainingPercent5h: 91, remainingPercent7d: 99, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: "plus", model: nil, updatedAt: now),
    updatedAt: now
)

expectEqual(DisplayFormatter.statusTitle(snapshot: snapshot, mode: .both), "Cdx 91% · Cl 63%", "both display mode")
expectEqual(DisplayFormatter.statusTitle(snapshot: snapshot, mode: .codexOnly), "Cdx 91%", "codex display mode")
expectEqual(DisplayFormatter.statusTitle(snapshot: snapshot, mode: .claudeOnly), "Cl 63%", "claude display mode")
expectEqual(DisplayFormatter.statusTitle(snapshot: snapshot, mode: .lowestRemaining), "AI 63%", "lowest display mode")
expectEqual(DisplayFormatter.statusTitle(snapshot: snapshot, mode: .both, labelStyle: .icon), "Codex 91% · Claude 63%", "icon display fallback text")
expectEqual(DisplayFormatter.formatPercent(nil), "--", "missing percent")

// Menu bar reset countdown. The time shown has to belong to the same window the
// percentage came from, or the two read as unrelated numbers side by side.
let countdownNow = Date()
func countdownUsage(_ provider: Provider, five: Int?, seven: Int?, reset5h: TimeInterval?, reset7d: TimeInterval?) -> ProviderUsage {
    ProviderUsage(
        provider: provider,
        remainingPercent5h: five,
        remainingPercent7d: seven,
        resetAt5h: reset5h.map { countdownNow.addingTimeInterval($0) },
        resetAt7d: reset7d.map { countdownNow.addingTimeInterval($0) },
        source: .api,
        error: nil,
        plan: nil,
        model: nil,
        updatedAt: countdownNow
    )
}

expectEqual(DisplayFormatter.formatResetCompact(countdownNow.addingTimeInterval(2550)), "42m", "under an hour shows minutes only")
expectEqual(DisplayFormatter.formatResetCompact(countdownNow.addingTimeInterval(8010)), "2h13m", "hours and minutes drop the inner space")
expectEqual(DisplayFormatter.formatResetCompact(countdownNow.addingTimeInterval(273_900)), "3d4h", "over a day shows days and hours")
expectEqual(DisplayFormatter.formatResetCompact(countdownNow.addingTimeInterval(-60)), "now", "a passed reset reads as now")
expect(DisplayFormatter.formatResetCompact(nil) == nil, "no reset instant yields no countdown at all")

let koreanReset = Localizer(language: .korean)
expectEqual(
    DisplayFormatter.formatWindowReset(remainingPercent: 63, resetAt: countdownNow.addingTimeInterval(2550)),
    "42m",
    "a window with a reset still names the time"
)
expectEqual(
    DisplayFormatter.formatWindowReset(remainingPercent: 100, resetAt: nil),
    "none yet (opens on first use)",
    "a balance with no reset means the window has not opened yet"
)
expectEqual(
    DisplayFormatter.formatWindowReset(remainingPercent: 100, resetAt: nil, localizer: koreanReset),
    "아직 없음(첫 사용 시 시작)",
    "the unopened window reads in Korean too"
)
expectEqual(
    DisplayFormatter.formatWindowReset(remainingPercent: nil, resetAt: nil),
    "--",
    "with no balance either there is nothing to claim about the window"
)
expectEqual(
    DisplayFormatter.formatWindowReset(remainingPercent: 55, resetAt: countdownNow.addingTimeInterval(-60), localizer: koreanReset),
    "지금",
    "a reset that has just passed reads in the chosen language"
)
expectEqual(
    DisplayFormatter.formatWindowReset(remainingPercent: nil, resetAt: nil, providerReported: true),
    "not reported",
    "a provider that answered without this window did not fail to read it"
)
expectEqual(
    DisplayFormatter.formatWindowReset(remainingPercent: nil, resetAt: nil, localizer: koreanReset, providerReported: true),
    "제공되지 않음",
    "the unreported window reads in Korean too"
)
expectEqual(koreanReset.text(.claudeOnly), "Claude만", "the display mode choices read in Korean")
expectEqual(koreanReset.text(.officialLogos), "공식 로고", "the provider label choices read in Korean")
expectEqual(
    DisplayFormatter.formatWindowReset(remainingPercent: 100, resetAt: nil, providerReported: true),
    "none yet (opens on first use)",
    "a window with a balance is unopened, not unreported"
)

let fiveHourLead = countdownUsage(.claude, five: 63, seven: 80, reset5h: 8010, reset7d: 273_900)
expectEqual(DisplayFormatter.displayWindow(fiveHourLead), .fiveHour, "a healthy 7d leaves the 5h window on display")
expectEqual(DisplayFormatter.displayResetAt(fiveHourLead), fiveHourLead.resetAt5h, "the countdown follows the displayed window")

let sevenDayLead = countdownUsage(.claude, five: 100, seven: 8, reset5h: 8010, reset7d: 273_900)
expectEqual(DisplayFormatter.displayWindow(sevenDayLead), .sevenDay, "a low 7d takes over the display")
expectEqual(DisplayFormatter.displayResetAt(sevenDayLead), sevenDayLead.resetAt7d, "taking over the percent takes over the countdown too")

let sevenDayOnly = countdownUsage(.codex, five: nil, seven: 100, reset5h: nil, reset7d: 273_900)
expectEqual(DisplayFormatter.displayWindow(sevenDayOnly), .sevenDay, "an absent 5h lane falls back to 7d")
expectEqual(DisplayFormatter.displayResetAt(sevenDayOnly), sevenDayOnly.resetAt7d, "the fallback window supplies the countdown")

let noWindows = countdownUsage(.claude, five: nil, seven: nil, reset5h: 8010, reset7d: 273_900)
expect(DisplayFormatter.displayWindow(noWindows) == nil, "an unavailable provider displays no window")
expect(DisplayFormatter.displayResetAt(noWindows) == nil, "an unavailable provider shows no countdown despite having reset instants")

let countdownSnapshot = UsageSnapshot(claude: fiveHourLead, codex: sevenDayOnly, updatedAt: countdownNow)
expectEqual(
    DisplayFormatter.statusTitle(snapshot: countdownSnapshot, mode: .both, showResetCountdown: true),
    "Cdx 100% 3d4h · Cl 63% 2h13m",
    "both mode appends each provider's own countdown"
)
expectEqual(
    DisplayFormatter.statusTitle(snapshot: countdownSnapshot, mode: .both),
    "Cdx 100% · Cl 63%",
    "the countdown stays off unless asked for"
)
expectEqual(
    DisplayFormatter.statusTitle(snapshot: countdownSnapshot, mode: .claudeOnly, showResetCountdown: true),
    "Cl 63% 2h13m",
    "single provider mode appends its countdown"
)
expectEqual(
    DisplayFormatter.statusTitle(snapshot: countdownSnapshot, mode: .lowestRemaining, showResetCountdown: true),
    "AI 63% 2h13m",
    "lowest mode pairs the winning percent with that provider's reset"
)
expectEqual(DisplayFormatter.lowestUsage(countdownSnapshot)?.provider, .claude, "the lower percent owns the countdown")

// A provider with no reset instant contributes a percent but no time, rather than
// a dangling "--" in the menu bar.
let noResetSnapshot = UsageSnapshot(
    claude: countdownUsage(.claude, five: 63, seven: 80, reset5h: nil, reset7d: nil),
    codex: sevenDayOnly,
    updatedAt: countdownNow
)
expectEqual(
    DisplayFormatter.statusTitle(snapshot: noResetSnapshot, mode: .both, showResetCountdown: true),
    "Cdx 100% 3d4h · Cl 63%",
    "a provider without a reset instant simply omits its countdown"
)
expectEqual(
    DisplayFormatter.statusTitle(snapshot: nil, mode: .both, showResetCountdown: true),
    "AI --",
    "no snapshot still renders the placeholder"
)

// A cached reading must not carry a live countdown: its reset instant keeps
// ticking while the value behind it stands still, so once the reset passes the
// countdown would read "now" for as long as the fallback lasts.
var staleCountdownUsage = fiveHourLead
staleCountdownUsage.source = .staleCache
let staleCountdownSnapshot = UsageSnapshot(claude: staleCountdownUsage, codex: sevenDayOnly, updatedAt: countdownNow)
expectEqual(
    DisplayFormatter.statusTitle(snapshot: staleCountdownSnapshot, mode: .claudeOnly, showResetCountdown: true),
    "Cl 63%",
    "a cached reading shows its percentage but no countdown"
)
expectEqual(
    DisplayFormatter.resetCountdownSuffix(fiveHourLead, showResetCountdown: true),
    " 2h13m",
    "a live reading still shows its countdown"
)

// History records measurements, not re-displays of one: a cached reading repeats a
// value the provider never re-reported, which would flatten the sparkline, zero the
// trend delta and tell the forecast that usage stopped moving.
let recordedSnapshot = UsageHistoryPolicy.measurementsOnly(
    UsageSnapshot(claude: staleCountdownUsage, codex: sevenDayOnly, updatedAt: countdownNow)
)
expect(recordedSnapshot.claude.remainingPercent5h == nil, "a cached reading contributes no 5h measurement")
expect(recordedSnapshot.claude.remainingPercent7d == nil, "a cached reading contributes no 7d measurement")
expectEqual(recordedSnapshot.codex.remainingPercent7d, 100, "a live reading is recorded unchanged")
expectEqual(recordedSnapshot.claude.source, UsageSource.staleCache, "clearing the value keeps the reason it was cleared")

// The trend picks its baseline per provider, so an entry recorded while one
// provider was unavailable no longer drops the other provider's delta.
// The gap entry is the OLDEST in the window, so a shared baseline would land on it
// and find no Claude percentages — dropping Claude's delta even though a usable
// reading sits right behind it. Per-provider baselines skip past it.
let trendGap = UsageHistoryEntry(
    recordedAt: countdownNow.addingTimeInterval(-3600),
    snapshot: UsageHistoryPolicy.measurementsOnly(
        UsageSnapshot(claude: staleCountdownUsage, codex: sevenDayOnly, updatedAt: countdownNow.addingTimeInterval(-3600))
    )
)
let trendBaseline = UsageHistoryEntry(
    recordedAt: countdownNow.addingTimeInterval(-1800),
    snapshot: UsageSnapshot(
        claude: countdownUsage(.claude, five: 90, seven: 95, reset5h: 8010, reset7d: 273_900),
        codex: sevenDayOnly,
        updatedAt: countdownNow.addingTimeInterval(-1800)
    )
)
let perProviderTrend = UsageHistoryFormatter.trendSummary(
    entries: [trendGap, trendBaseline],
    current: UsageSnapshot(claude: fiveHourLead, codex: sevenDayOnly, updatedAt: countdownNow)
)
expect(
    perProviderTrend.contains("Claude 5h -27"),
    "Claude's delta survives an older entry that recorded no Claude measurement"
)

let exhaustedSevenDaySnapshot = UsageSnapshot(
    claude: ProviderUsage(provider: .claude, remainingPercent5h: 100, remainingPercent7d: 0, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now),
    codex: ProviderUsage(provider: .codex, remainingPercent5h: 100, remainingPercent7d: 42, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: "plus", model: nil, updatedAt: now),
    updatedAt: now
)

expectEqual(DisplayFormatter.statusTitle(snapshot: exhaustedSevenDaySnapshot, mode: .both), "Cdx 100% · Cl 0%", "display only switches to seven day inside warning threshold")
expectEqual(DisplayFormatter.statusTitle(snapshot: exhaustedSevenDaySnapshot, mode: .codexOnly), "Cdx 100%", "codex display uses five hour when seven day is above warning threshold")
expectEqual(DisplayFormatter.statusTitle(snapshot: exhaustedSevenDaySnapshot, mode: .lowestRemaining), "AI 0%", "lowest display includes exhausted seven day window")

let healthySevenDaySnapshot = UsageSnapshot(
    claude: ProviderUsage(provider: .claude, remainingPercent5h: 100, remainingPercent7d: 90, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now),
    codex: ProviderUsage(provider: .codex, remainingPercent5h: 98, remainingPercent7d: 100, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: "plus", model: nil, updatedAt: now),
    updatedAt: now
)

expectEqual(DisplayFormatter.statusTitle(snapshot: healthySevenDaySnapshot, mode: .both), "Cdx 98% · Cl 100%", "healthy seven day value does not override five hour display")
expectEqual(DisplayFormatter.isSevenDayWarning(healthySevenDaySnapshot.claude), false, "healthy seven day value is not highlighted")
expectEqual(DisplayFormatter.isSevenDayWarning(exhaustedSevenDaySnapshot.claude), true, "exhausted seven day value is highlighted")

let sevenDayThresholdUsage = ProviderUsage(provider: .claude, remainingPercent5h: 100, remainingPercent7d: 10, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now)
let missingSevenDayUsage = ProviderUsage(provider: .claude, remainingPercent5h: 73, remainingPercent7d: nil, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now)
let missingFiveHourUsage = ProviderUsage(provider: .claude, remainingPercent5h: nil, remainingPercent7d: 42, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now)
let staleClaudeUsage = ProviderUsage(provider: .claude, remainingPercent5h: 64, remainingPercent7d: 82, resetAt5h: nil, resetAt7d: nil, source: .staleCache, error: "HTTP 429 from Claude API", plan: nil, model: nil, updatedAt: now)

expectEqual(DisplayFormatter.displayPercent(sevenDayThresholdUsage), 10, "7d value is shown at 10 percent threshold")
expectEqual(DisplayFormatter.isSevenDayWarning(sevenDayThresholdUsage), true, "7d threshold value is highlighted")
expectEqual(DisplayFormatter.displayPercent(missingSevenDayUsage), 73, "5h value is shown when 7d is missing")
expectEqual(DisplayFormatter.isSevenDayWarning(missingSevenDayUsage), false, "missing 7d is not highlighted")
expectEqual(DisplayFormatter.displayPercent(missingFiveHourUsage), 42, "7d value is shown when 5h is missing")
expectEqual(DisplayFormatter.isSevenDayWarning(missingFiveHourUsage), false, "healthy 7d fallback is not highlighted when 5h is missing")

let missingBothWindowsUsage = ProviderUsage(provider: .codex, remainingPercent5h: nil, remainingPercent7d: nil, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now)
expectEqual(DisplayFormatter.preferredForecastWindow(missingSevenDayUsage), .fiveHour, "5h window is preferred when it reports")
expectEqual(DisplayFormatter.preferredForecastWindow(missingFiveHourUsage), .sevenDay, "forecast surfaces fall back to 7d when 5h is missing")
expectEqual(DisplayFormatter.preferredForecastWindow(missingBothWindowsUsage), .fiveHour, "default window is 5h when neither reports")
expectEqual(DisplayFormatter.displayPercent(staleClaudeUsage), 64, "stale cache still displays cached percent")
expectEqual(staleClaudeUsage.source, .staleCache, "stale cache source is preserved")
expectEqual(staleClaudeUsage.error, "HTTP 429 from Claude API", "stale cache keeps the fetch failure reason")

let staleSnapshot = UsageSnapshot(
    claude: ProviderUsage(provider: .claude, remainingPercent5h: 63, remainingPercent7d: 80, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now),
    codex: ProviderUsage(provider: .codex, remainingPercent5h: 91, remainingPercent7d: 99, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now),
    updatedAt: now
)
let freshFailureSnapshot = UsageSnapshot(
    claude: ProviderUsage.unavailable(.claude, error: "HTTP 429 from Claude API"),
    codex: ProviderUsage.unavailable(.codex, error: "Disabled"),
    updatedAt: now
)
let enabledStaleSnapshot = UsageSnapshotCachePolicy.apply(
    current: freshFailureSnapshot,
    stale: staleSnapshot,
    claudeEnabled: true,
    codexEnabled: false,
    updatedAt: now
)
expectEqual(enabledStaleSnapshot.claude.source, .staleCache, "Enabled Claude can use stale cache")
expectEqual(enabledStaleSnapshot.codex.source, .unavailable, "Disabled Codex does not use stale cache")
expectEqual(enabledStaleSnapshot.codex.error, "Disabled", "Disabled Codex keeps disabled reason")

expectEqual(UsageError.httpStatus(code: 401, service: "Claude API", retryAfter: nil).localizedDescription, "HTTP 401 from Claude API", "HTTP status error names Claude API")
expectEqual(UsageError.httpStatus(code: 429, service: "Claude API", retryAfter: 300).localizedDescription, "HTTP 429 from Claude API; retrying after 5m", "HTTP 429 error includes retry delay")
expectEqual(UsageError.timedOut(service: "Claude API").localizedDescription, "Timed out contacting Claude API", "timeout error names Claude API")
expectEqual(UsageError.network(message: "offline", service: "Claude API").localizedDescription, "Network error from Claude API: offline", "network error names Claude API")

expectEqual(UsageIssueFormatter.kind(forError: "Disabled"), .disabled, "disabled error is classified")
expectEqual(UsageIssueFormatter.kind(forError: "HTTP 429 from Claude API; retrying after 5m"), .rateLimited, "429 error is classified as rate limited")
expectEqual(UsageIssueFormatter.kind(forError: "Missing credentials"), .missingCredentials, "missing credentials is classified")
expectEqual(UsageIssueFormatter.kind(forError: "Credentials expired for Codex API"), .expiredCredentials, "an expired token is classified")
expectEqual(UsageIssueFormatter.kind(forError: "HTTP 401 from Claude API"), .expiredCredentials, "401 is reported as an expired sign-in, not a bare HTTP error")
expectEqual(UsageIssueFormatter.kind(forError: "HTTP 403 from Codex API"), .expiredCredentials, "403 is reported as an expired sign-in")
expectEqual(UsageIssueFormatter.kind(forError: "HTTP 500 from Claude API"), .httpStatus, "other HTTP codes stay generic HTTP errors")
let expiredCodexIssue = UsageIssueFormatter.issue(for: .unavailable(.codex, error: "Credentials expired for Codex API"))
expect(expiredCodexIssue.recovery?.contains("codex login") == true, "Codex recovery names the Codex sign-in command")
expect(expiredCodexIssue.recovery?.contains("/login") == false, "Codex recovery does not make the user read past Claude's command")
let expiredClaudeIssue = UsageIssueFormatter.issue(for: .unavailable(.claude, error: "HTTP 401 from Claude API"))
expect(expiredClaudeIssue.recovery?.contains("/login") == true, "Claude recovery names the Claude sign-in command")
expect(expiredClaudeIssue.recovery?.contains("codex login") == false, "Claude recovery does not mention Codex")
expectEqual(UsageIssueFormatter.kind(forError: "Timed out contacting Claude API"), .timedOut, "timeout is classified")
expectEqual(UsageIssueFormatter.kind(forError: "Network error from Claude API: offline"), .network, "network error is classified")

let cachedIssue = UsageIssueFormatter.issue(for: staleClaudeUsage)
expectEqual(cachedIssue.kind, .usingCachedData, "stale cache issue is classified")
expectEqual(cachedIssue.technicalDetail, "HTTP 429 from Claude API", "stale cache keeps technical detail")

let alertSnapshot = UsageSnapshot(
    claude: ProviderUsage(provider: .claude, remainingPercent5h: 19, remainingPercent7d: 9, resetAt5h: now.addingTimeInterval(300), resetAt7d: now.addingTimeInterval(7200), source: .api, error: nil, plan: nil, model: nil, updatedAt: now),
    codex: ProviderUsage(provider: .codex, remainingPercent5h: 80, remainingPercent7d: 90, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now),
    updatedAt: now
)
let alertCandidates = UsageAlertEvaluator.candidates(
    snapshot: alertSnapshot,
    settings: UsageAlertSettings(notificationsEnabled: true, fiveHourThreshold: 20, sevenDayThreshold: 10, resetWarningMinutes: 10),
    now: now
)
expectEqual(alertCandidates.map(\.id), ["claude-5h-low", "claude-7d-low"], "a low window gets its low alert and no reset warning: the refill is good news")
let disabledAlertCandidates = UsageAlertEvaluator.candidates(
    snapshot: alertSnapshot,
    settings: UsageAlertSettings(notificationsEnabled: false, fiveHourThreshold: 20, sevenDayThreshold: 10, resetWarningMinutes: 10),
    now: now
)
expectEqual(disabledAlertCandidates.count, 0, "disabled notifications emit no alerts")

// Reset warnings: only for a measured window with more left than its low threshold,
// since what it has not used by the reset is gone.
func resetAlertBodies(unused5h: Int?, source: UsageSource = .api, threshold: Int = 20, language: AppLanguage = .english) -> [String] {
    let snapshot = UsageSnapshot(
        claude: ProviderUsage(provider: .claude, remainingPercent5h: unused5h, remainingPercent7d: 90, resetAt5h: now.addingTimeInterval(300), resetAt7d: now.addingTimeInterval(259_200), source: source, error: nil, plan: nil, model: nil, updatedAt: now),
        codex: .unavailable(.codex, error: "Disabled"),
        updatedAt: now
    )
    return UsageAlertEvaluator.candidates(
        snapshot: snapshot,
        settings: UsageAlertSettings(notificationsEnabled: true, fiveHourThreshold: threshold, sevenDayThreshold: 10, resetWarningMinutes: 10),
        now: now,
        localizer: Localizer(language: language)
    ).filter { $0.id.contains("-reset-") }.map(\.body)
}
expectEqual(resetAlertBodies(unused5h: 60), ["Claude 5h resets in 5m with 60% unused"], "a reset with plenty left is worth a warning")
expectEqual(resetAlertBodies(unused5h: 60, language: .korean), ["Claude 5h 5m 후 리셋 — 60% 남음"], "the reset warning reads in Korean")
expectEqual(resetAlertBodies(unused5h: 20), [], "a window at its low threshold gets no reset warning")
expectEqual(resetAlertBodies(unused5h: 21), ["Claude 5h resets in 5m with 21% unused"], "one point above the threshold is warned about")
expectEqual(resetAlertBodies(unused5h: 1, threshold: 0), ["Claude 5h resets in 5m with 1% unused"], "with the low alert off, anything left is worth a warning")
expectEqual(resetAlertBodies(unused5h: 0, threshold: 0), [], "nothing left is nothing to lose")
expectEqual(resetAlertBodies(unused5h: nil), [], "a window with no number gets no reset warning")
expectEqual(resetAlertBodies(unused5h: 60, source: .staleCache), [], "a cached number may have been spent since")
expectEqual(resetAlertBodies(unused5h: 60, source: .statusLine), ["Claude 5h resets in 5m with 60% unused"], "a status-line reading is a measurement")

func stalledSnapshot(ageSeconds: TimeInterval, source: UsageSource) -> UsageSnapshot {
    UsageSnapshot(
        claude: ProviderUsage(provider: .claude, remainingPercent5h: 55, remainingPercent7d: 70, resetAt5h: nil, resetAt7d: nil, source: source, error: nil, plan: nil, model: nil, updatedAt: now.addingTimeInterval(-ageSeconds)),
        codex: ProviderUsage(provider: .codex, remainingPercent5h: 80, remainingPercent7d: 90, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now),
        updatedAt: now
    )
}

func stalledIDs(ageSeconds: TimeInterval, source: UsageSource, stalledAfter: TimeInterval) -> [String] {
    UsageAlertEvaluator.candidates(
        snapshot: stalledSnapshot(ageSeconds: ageSeconds, source: source),
        settings: UsageAlertSettings(
            notificationsEnabled: true,
            fiveHourThreshold: 0,
            sevenDayThreshold: 0,
            resetWarningMinutes: 0,
            stalledAfter: stalledAfter
        ),
        now: now
    ).map(\.id)
}

expectEqual(stalledIDs(ageSeconds: 5400, source: .staleCache, stalledAfter: 1800), ["claude-stalled"], "a cached reading past the threshold is reported as stalled")
expectEqual(stalledIDs(ageSeconds: 600, source: .staleCache, stalledAfter: 1800), [], "a cached reading younger than the threshold is not news")
expectEqual(stalledIDs(ageSeconds: 5400, source: .api, stalledAfter: 1800), [], "an old measurement is still a measurement")
expectEqual(stalledIDs(ageSeconds: 5400, source: .staleCache, stalledAfter: 0), [], "a zero threshold disables the stalled alert")
expectEqual(UsageAlertEvaluator.stalledThreshold(refreshInterval: 300), 1800, "a short poll interval keeps the 30 minute floor")
expectEqual(UsageAlertEvaluator.stalledThreshold(refreshInterval: 1800), 5400, "a long poll interval waits three polls")

func lowBalanceIDs(reset5h: Date?, reset7d: Date?) -> [String] {
    let snapshot = UsageSnapshot(
        claude: ProviderUsage(provider: .claude, remainingPercent5h: 5, remainingPercent7d: 4, resetAt5h: reset5h, resetAt7d: reset7d, source: .staleCache, error: nil, plan: nil, model: nil, updatedAt: now),
        codex: ProviderUsage(provider: .codex, remainingPercent5h: 80, remainingPercent7d: 90, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now),
        updatedAt: now
    )
    return UsageAlertEvaluator.candidates(
        snapshot: snapshot,
        settings: UsageAlertSettings(notificationsEnabled: true, fiveHourThreshold: 20, sevenDayThreshold: 10, resetWarningMinutes: 0),
        now: now
    ).map(\.id)
}

expectEqual(lowBalanceIDs(reset5h: now.addingTimeInterval(600), reset7d: now.addingTimeInterval(7200)), ["claude-5h-low", "claude-7d-low"], "a balance in a window that has not reset still warns")
expectEqual(lowBalanceIDs(reset5h: now.addingTimeInterval(-60), reset7d: now.addingTimeInterval(-60)), [], "a balance whose window already reset is no reason to warn")
expectEqual(lowBalanceIDs(reset5h: nil, reset7d: nil), ["claude-5h-low", "claude-7d-low"], "with no reset instant to judge by the alert stands")

let earlierSnapshot = UsageSnapshot(
    claude: ProviderUsage(provider: .claude, remainingPercent5h: 40, remainingPercent7d: 70, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now.addingTimeInterval(-3600)),
    codex: ProviderUsage(provider: .codex, remainingPercent5h: 90, remainingPercent7d: 99, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now.addingTimeInterval(-3600)),
    updatedAt: now.addingTimeInterval(-3600)
)
let trend = UsageHistoryFormatter.trendSummary(
    entries: [UsageHistoryEntry(recordedAt: now.addingTimeInterval(-3600), snapshot: earlierSnapshot)],
    current: snapshot,
    window: 24 * 60 * 60
)
expectEqual(trend, "24h trend: Claude 5h +23% 7d +10% Codex 5h +1% 7d 0%", "history trend summarizes 5h and 7d provider deltas")

let missingSevenDayBaseline = UsageSnapshot(
    claude: ProviderUsage(provider: .claude, remainingPercent5h: 40, remainingPercent7d: nil, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: now.addingTimeInterval(-3600)),
    codex: earlierSnapshot.codex,
    updatedAt: now.addingTimeInterval(-3600)
)
let missingSevenDayTrend = UsageHistoryFormatter.trendSummary(
    entries: [UsageHistoryEntry(recordedAt: now.addingTimeInterval(-3600), snapshot: missingSevenDayBaseline)],
    current: snapshot,
    window: 24 * 60 * 60
)
expectEqual(missingSevenDayTrend, "24h trend: Claude 5h +23% 7d -- Codex 5h +1% 7d 0%", "history trend shows -- when a 7d value is missing")
let csv = UsageHistoryFormatter.csvString(for: [UsageHistoryEntry(recordedAt: now, snapshot: snapshot)])
expect(csv.contains("recorded_at,provider,remaining_5h"), "history csv includes header")
expect(csv.contains("claude,63,80"), "history csv includes claude row")

expect(AppPathsProbe.claudeRateLimitPath != AppPathsProbe.codexRateLimitPath, "each provider holds its own cooldown file")

let rateLimitStoreURL = FileManager.default.temporaryDirectory
    .appendingPathComponent("tt-rate-limit-\(UUID().uuidString).json")
let rateLimitStore = RateLimitStore(url: rateLimitStoreURL)
defer { try? FileManager.default.removeItem(at: rateLimitStoreURL) }

expect(rateLimitStore.load() == nil, "rate limit store starts empty")
let futureRetry = Date().addingTimeInterval(300)
rateLimitStore.save(.init(retryAllowedAt: futureRetry, failureCount: 3, fingerprint: "abc123"))
if let persisted = rateLimitStore.load() {
    expect(abs(persisted.retryAllowedAt.timeIntervalSince(futureRetry)) < 1, "future cooldown survives a reload")
    expectEqual(persisted.failureCount, 3, "failure count survives a reload for exponential backoff")
    expectEqual(persisted.fingerprint, "abc123", "credential fingerprint survives a reload")
} else {
    expect(false, "future cooldown survives a reload")
}
rateLimitStore.save(.init(retryAllowedAt: Date().addingTimeInterval(-1), failureCount: 3))
expect(rateLimitStore.load() == nil, "expired cooldown reads as empty")
rateLimitStore.save(.init(retryAllowedAt: futureRetry, failureCount: 1))
rateLimitStore.clear()
expect(rateLimitStore.load() == nil, "cleared cooldown reads as empty")

// Backward compatibility: a legacy record with only a retry instant (written
// before the failure count was persisted) loads with a zero failure count.
let legacyISO = ISO8601DateFormatter().string(from: futureRetry)
try? Data("{\"retryAllowedAt\":\"\(legacyISO)\"}".utf8).write(to: rateLimitStoreURL)
if let legacyState = rateLimitStore.load() {
    expectEqual(legacyState.failureCount, 0, "a legacy record without a failure count loads as zero")
    expect(legacyState.fingerprint == nil, "a legacy record without a fingerprint loads as nil")
} else {
    expect(false, "a legacy record still loads")
}

expectEqual(RateLimitBackoff.cooldown(retryAfter: 300, failureCount: 0, jitter: 0), 300, "first headerless 429 waits the 300s default")
expectEqual(RateLimitBackoff.cooldown(retryAfter: 0, failureCount: 0, jitter: 0), 120, "absent Retry-After falls back to the 120s minimum")
expectEqual(RateLimitBackoff.cooldown(retryAfter: 300, failureCount: 2, jitter: 0), 1200, "repeated 429 escalates exponentially")
expectEqual(RateLimitBackoff.cooldown(retryAfter: 300, failureCount: 5, jitter: 0), 1800, "escalation is capped at 30m")
expectEqual(RateLimitBackoff.cooldown(retryAfter: 3600, failureCount: 0, jitter: 0), 3600, "an explicit longer Retry-After is honored above the cap")
let jitteredCooldown = RateLimitBackoff.cooldown(retryAfter: 300, failureCount: 0, jitter: RateLimitBackoff.jitterFraction)
expect(jitteredCooldown > 300 && jitteredCooldown <= 360, "jitter adds up to 20 percent on top of the base cooldown")

let arbiterNow = Date()
expect(!InstanceArbiter.shouldYield(current: .init(pid: 100, launchDate: arbiterNow), others: []), "a lone instance keeps running")
expect(InstanceArbiter.shouldYield(current: .init(pid: 100, launchDate: arbiterNow), others: [.init(pid: 50, launchDate: arbiterNow.addingTimeInterval(-1))]), "an earlier instance owns the slot")
expect(!InstanceArbiter.shouldYield(current: .init(pid: 100, launchDate: arbiterNow), others: [.init(pid: 200, launchDate: arbiterNow.addingTimeInterval(1))]), "a later instance yields to us")
expect(InstanceArbiter.shouldYield(current: .init(pid: 100, launchDate: arbiterNow), others: [.init(pid: 50, launchDate: arbiterNow)]), "simultaneous launch: the lower pid owns the slot")
expect(!InstanceArbiter.shouldYield(current: .init(pid: 50, launchDate: arbiterNow), others: [.init(pid: 100, launchDate: arbiterNow)]), "simultaneous launch: we survive as the lower pid")

let settingsSuiteName = "tt-settings-\(UUID().uuidString)"
if let migrationDefaults = UserDefaults(suiteName: settingsSuiteName) {
    defer { migrationDefaults.removeSuite(named: settingsSuiteName) }
    migrationDefaults.set(30.0, forKey: "refreshInterval")
    expectEqual(Settings(defaults: migrationDefaults).refreshInterval, 60, "legacy sub-60 refresh interval migrates to the 60s floor")
    migrationDefaults.set(300.0, forKey: "refreshInterval")
    expectEqual(Settings(defaults: migrationDefaults).refreshInterval, 300, "valid refresh interval is left unchanged")
} else {
    expect(false, "settings migration suite is available")
}

// MARK: Usage forecast
func forecastEntry(_ secondsAgo: TimeInterval, claude5h: Int?, claude7d: Int? = 100) -> UsageHistoryEntry {
    let at = now.addingTimeInterval(-secondsAgo)
    let claudeUsage = ProviderUsage(provider: .claude, remainingPercent5h: claude5h, remainingPercent7d: claude7d, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: at)
    let codexUsage = ProviderUsage(provider: .codex, remainingPercent5h: 100, remainingPercent7d: 100, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: at)
    return UsageHistoryEntry(recordedAt: at, snapshot: UsageSnapshot(claude: claudeUsage, codex: codexUsage, updatedAt: at))
}

let steadyEntries = [forecastEntry(3600, claude5h: 60), forecastEntry(1800, claude5h: 50), forecastEntry(0, claude5h: 40)]
let steadyForecast = UsageForecaster.forecast(entries: steadyEntries, provider: .claude, window: .fiveHour, resetAt: now.addingTimeInterval(10800), now: now)
expect(steadyForecast != nil, "forecast is produced for a steady decline")
expectEqual(Int(steadyForecast?.burnPerHour ?? 0), 20, "burn rate is 20%/h")
expectEqual(Int(steadyForecast?.secondsToEmpty ?? 0), 7200, "5h window empties in 2h at the observed rate")
expectEqual(steadyForecast?.willEmptyBeforeReset, true, "empties before a 3h reset")

let earlyResetForecast = UsageForecaster.forecast(entries: steadyEntries, provider: .claude, window: .fiveHour, resetAt: now.addingTimeInterval(3600), now: now)
expectEqual(earlyResetForecast?.willEmptyBeforeReset, false, "does not empty before an earlier reset")

let resetEntries = [forecastEntry(3600, claude5h: 30), forecastEntry(1800, claude5h: 80), forecastEntry(900, claude5h: 70), forecastEntry(0, claude5h: 60)]
let resetForecast = UsageForecaster.forecast(entries: resetEntries, provider: .claude, window: .fiveHour, resetAt: nil, now: now)
expectEqual(Int(resetForecast?.secondsToEmpty ?? 0), 5400, "forecast uses only the post-reset segment")
expectEqual(Int(resetForecast?.burnPerHour ?? 0), 40, "post-reset burn rate is 40%/h")

expect(UsageForecaster.forecast(entries: [forecastEntry(3600, claude5h: 40), forecastEntry(0, claude5h: 50)], provider: .claude, window: .fiveHour, resetAt: nil, now: now) == nil, "no forecast while replenishing")
expect(UsageForecaster.forecast(entries: [forecastEntry(300, claude5h: 60), forecastEntry(0, claude5h: 50)], provider: .claude, window: .fiveHour, resetAt: nil, now: now) == nil, "no forecast under the minimum span")
expect(UsageForecaster.forecast(entries: [forecastEntry(10800, claude5h: 60), forecastEntry(7200, claude5h: 40)], provider: .claude, window: .fiveHour, resetAt: nil, now: now) == nil, "no forecast when the newest sample is stale")

expectEqual(UsageForecaster.durationText(7800), "2h 10m", "duration formats hours and minutes")
expectEqual(UsageForecaster.durationText(2700), "45m", "duration formats minutes")
expectEqual(UsageForecaster.durationText(30), "<1m", "sub-minute duration collapses to <1m")
expectEqual(UsageForecaster.durationText(86400), "1d 0h", "one-day duration switches to days")
expectEqual(UsageForecaster.durationText(273000), "3d 3h", "duration formats days and hours")

let forecastLocalizer = Localizer(language: .english)
expectEqual(
    UsageForecastText.menuLine(forecast: steadyForecast, window: .fiveHour, localizer: forecastLocalizer),
    "Projected depletion: ~2h 0m · empties before reset",
    "5h forecast menu line format is unchanged"
)
expectEqual(
    UsageForecastText.menuLine(forecast: steadyForecast, window: .sevenDay, localizer: forecastLocalizer),
    "Projected depletion: ~2h 0m (7d) · empties before reset",
    "7d fallback forecast line carries the window marker"
)
expect(UsageForecastText.menuLine(forecast: nil, window: .sevenDay, localizer: forecastLocalizer) == nil, "no forecast menu line without a forecast")

let forecastAlertInput = ForecastAlertInput(provider: .claude, window: .fiveHour, forecast: steadyForecast!, resetAt: now.addingTimeInterval(10800))
expectEqual(
    UsageForecastAlert.candidates(inputs: [forecastAlertInput], enabled: true).map(\.id),
    ["claude-5h-empty-before-reset-\(UsageAlertEvaluator.resetKey(now.addingTimeInterval(10800)))"],
    "forecast alert fires when depletion precedes reset"
)

// Alert ids name the window by its reset rounded to the minute. Providers report
// one reset a fraction of a second apart from poll to poll — Claude's came back as
// 04:59:59 and 05:00:00 in turn — and a new id each time sent the alert again.
let wholeMinute = Date(timeIntervalSince1970: 1_800_000_000)
expectEqual(UsageAlertEvaluator.resetKey(wholeMinute), 1_800_000_000, "a whole-minute reset keys to itself")
expectEqual(UsageAlertEvaluator.resetKey(wholeMinute.addingTimeInterval(-0.4)), 1_800_000_000, "a reset reported just early keys to the same minute")
expectEqual(UsageAlertEvaluator.resetKey(wholeMinute.addingTimeInterval(0.4)), 1_800_000_000, "a reset reported just late keys to the same minute")
expectEqual(UsageAlertEvaluator.resetKey(wholeMinute.addingTimeInterval(29)), 1_800_000_000, "under half a minute rounds down")
expectEqual(UsageAlertEvaluator.resetKey(wholeMinute.addingTimeInterval(30)), 1_800_000_060, "half a minute rounds up")
expectEqual(UsageAlertEvaluator.resetKey(wholeMinute.addingTimeInterval(-1.6)), 1_800_000_000, "Codex's 58/59-second readings key alike")
expect(UsageAlertEvaluator.resetKey(wholeMinute.addingTimeInterval(5 * 3600)) != UsageAlertEvaluator.resetKey(wholeMinute), "the next window instance keeps its own key")

let jitterNow = wholeMinute.addingTimeInterval(-300)
let jitterSettings = UsageAlertSettings(notificationsEnabled: true, fiveHourThreshold: 20, sevenDayThreshold: 10, resetWarningMinutes: 10)
func jitterResetIDs(_ reset: Date) -> [String] {
    let usage = ProviderUsage(provider: .claude, remainingPercent5h: 60, remainingPercent7d: 90, resetAt5h: reset, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: jitterNow)
    return UsageAlertEvaluator.candidates(
        snapshot: UsageSnapshot(claude: usage, codex: .unavailable(.codex, error: "Disabled"), updatedAt: jitterNow),
        settings: jitterSettings,
        now: jitterNow
    ).map(\.id)
}
let earlyReset = wholeMinute.addingTimeInterval(-0.2)
let lateReset = wholeMinute.addingTimeInterval(0.3)
expectEqual(jitterResetIDs(earlyReset), ["claude-5h-reset-1800000000"], "the reset warning names the minute")
expectEqual(jitterResetIDs(lateReset), jitterResetIDs(earlyReset), "a reset that flips across a second keeps its warning id")
func jitterDepletionIDs(_ reset: Date) -> [String] {
    UsageForecastAlert.candidates(
        inputs: [ForecastAlertInput(provider: .claude, window: .fiveHour, forecast: UsageForecast(burnPerHour: 30, secondsToEmpty: 60, emptyAt: jitterNow.addingTimeInterval(60), willEmptyBeforeReset: true), resetAt: reset)],
        enabled: true
    ).map(\.id)
}
expectEqual(jitterDepletionIDs(lateReset), jitterDepletionIDs(earlyReset), "a reset that flips across a second keeps its depletion id")
func jitterFreedIDs(_ reset: Date) -> [String] {
    let before = ProviderUsage(provider: .claude, remainingPercent5h: 5, remainingPercent7d: 90, resetAt5h: reset, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: wholeMinute.addingTimeInterval(-600))
    let after = ProviderUsage(provider: .claude, remainingPercent5h: 100, remainingPercent7d: 90, resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil, model: nil, updatedAt: wholeMinute.addingTimeInterval(60))
    return UsageAlertEvaluator.freedUpCandidates(
        previous: [.claude: before],
        current: UsageSnapshot(claude: after, codex: .unavailable(.codex, error: "Disabled"), updatedAt: after.updatedAt),
        settings: jitterSettings
    ).map(\.id)
}
expectEqual(jitterFreedIDs(lateReset), jitterFreedIDs(earlyReset), "a reset that flips across a second keeps its freed id")
expectEqual(jitterFreedIDs(earlyReset), ["claude-5h-freed-1800000000"], "the freed alert names the minute too")
expectEqual(UsageForecastAlert.candidates(inputs: [forecastAlertInput], enabled: false).count, 0, "forecast alert suppressed when disabled")
let safeForecastInput = ForecastAlertInput(provider: .claude, window: .fiveHour, forecast: earlyResetForecast!, resetAt: now.addingTimeInterval(3600))
expectEqual(UsageForecastAlert.candidates(inputs: [safeForecastInput], enabled: true).count, 0, "no forecast alert when the reset comes first")

// Alert bodies follow the language setting like their titles. The ids do not:
// duplicate suppression is keyed on them, so switching language must not re-fire.
func alertBodies(_ language: AppLanguage) -> [String] {
    let bodyLocalizer = Localizer(language: language)
    let stalled = UsageAlertEvaluator.candidates(
        snapshot: stalledSnapshot(ageSeconds: 5400, source: .staleCache),
        settings: UsageAlertSettings(notificationsEnabled: true, fiveHourThreshold: 0, sevenDayThreshold: 0, resetWarningMinutes: 0, stalledAfter: 1800),
        now: now,
        localizer: bodyLocalizer
    )
    let lowAndReset = UsageAlertEvaluator.candidates(
        snapshot: alertSnapshot,
        settings: UsageAlertSettings(notificationsEnabled: true, fiveHourThreshold: 20, sevenDayThreshold: 10, resetWarningMinutes: 10),
        now: now,
        localizer: bodyLocalizer
    )
    let depletion = UsageForecastAlert.candidates(inputs: [forecastAlertInput], enabled: true, localizer: bodyLocalizer)
    return (stalled + lowAndReset + depletion).map(\.body)
}

expectEqual(
    alertBodies(.english),
    [
        "Claude last measured 1h30m ago",
        "Claude 5h 19% <= 20%",
        "Claude 7d 9% <= 10%",
        "Claude 5h: ~2h 0m → 0% (before reset)"
    ],
    "English alert bodies are unchanged"
)
expectEqual(
    alertBodies(.korean),
    [
        "Claude 마지막 측정 1h30m 전",
        "Claude 5h 19% <= 20%",
        "Claude 7d 9% <= 10%",
        "Claude 5h: ~2h 0m → 0% (리셋 전)"
    ],
    "Korean alert bodies are translated; the symbol-only low-balance bodies stay as they are"
)
expectEqual(
    UsageAlertEvaluator.candidates(
        snapshot: alertSnapshot,
        settings: UsageAlertSettings(notificationsEnabled: true, fiveHourThreshold: 20, sevenDayThreshold: 10, resetWarningMinutes: 10),
        now: now,
        localizer: Localizer(language: .korean)
    ).map(\.id),
    alertCandidates.map(\.id),
    "alert ids do not depend on the language"
)
expectEqual(
    UsageForecastAlert.candidates(inputs: [forecastAlertInput], enabled: true, localizer: Localizer(language: .korean)).map(\.id),
    UsageForecastAlert.candidates(inputs: [forecastAlertInput], enabled: true).map(\.id),
    "forecast alert ids do not depend on the language"
)

// MARK: Sparkline
expectEqual(SparklineText.render([0, 25, 50, 75, 100]), "▁▃▅▇█", "sparkline maps values across the eight blocks")
expectEqual(SparklineText.render([42]), "", "a single point renders nothing")
expectEqual(SparklineText.render([]), "", "an empty series renders nothing")
let sparkEntries = [forecastEntry(3600, claude5h: 100), forecastEntry(1800, claude5h: 50), forecastEntry(0, claude5h: 0)]
expectEqual(SparklineSeries.build(entries: sparkEntries, provider: .claude, window: .fiveHour), [100, 50, 0], "sparkline series extracts remaining values in time order")
expectEqual(SparklineText.render(SparklineSeries.build(entries: sparkEntries, provider: .claude, window: .fiveHour)), "█▅▁", "end-to-end sparkline render")
let manySparkEntries = (0..<40).map { forecastEntry(TimeInterval(40 - $0) * 60, claude5h: 100) }
expectEqual(SparklineSeries.build(entries: manySparkEntries, provider: .claude, window: .fiveHour, maxPoints: 20).count, 20, "sparkline series is downsampled to maxPoints")

// MARK: Pause controller
let pauseNow = Date()
expect(PauseController.isPaused(until: pauseNow.addingTimeInterval(3600), now: pauseNow), "paused while the resume instant is in the future")
expect(!PauseController.isPaused(until: pauseNow.addingTimeInterval(-10), now: pauseNow), "not paused once the resume instant has passed")
expect(!PauseController.isPaused(until: nil, now: pauseNow), "not paused when unset")
expectEqual(Int(PauseController.remaining(until: pauseNow.addingTimeInterval(1800), now: pauseNow)), 1800, "remaining reports seconds until resume")
expectEqual(Int(PauseController.remaining(until: nil, now: pauseNow)), 0, "remaining is zero when not paused")
expect(PauseController.isIndefinite(until: .distantFuture, now: pauseNow), "a distant-future pause is indefinite")
expect(!PauseController.isIndefinite(until: pauseNow.addingTimeInterval(3600), now: pauseNow), "a timed pause is not indefinite")

// MARK: Codex window mapping
let weeklyOnlyObject: [String: Any] = [
    "plan_type": "plus",
    "rate_limit": [
        "primary_window": [
            "used_percent": 4.0,
            "limit_window_seconds": 604_800.0,
            "reset_at": 1_785_331_564.0
        ]
    ]
]
let weeklyOnlyUsage = CodexUsageParser.parse(object: weeklyOnlyObject, updatedAt: now)
expect(weeklyOnlyUsage != nil, "a weekly-only payload parses")
expectEqual(weeklyOnlyUsage?.remainingPercent5h, nil, "a weekly primary window leaves the 5h lane empty")
expectEqual(weeklyOnlyUsage?.remainingPercent7d, 96, "a weekly primary window fills the 7d lane")
expectEqual(weeklyOnlyUsage?.resetAt5h, nil, "no 5h reset when the 5h window is absent")
expectEqual(weeklyOnlyUsage?.resetAt7d, Date(timeIntervalSince1970: 1_785_331_564), "the weekly reset lands in the 7d lane")
expectEqual(weeklyOnlyUsage?.plan, "plus", "the plan is preserved")

let legacyObject: [String: Any] = [
    "plan_type": "prolite",
    "rate_limit": [
        "primary_window": ["used_percent": 24.2, "reset_at": 1_770_000_000.0],
        "secondary_window": ["used_percent": 98.0, "reset_at": 1_770_500_000.0]
    ]
]
let legacyUsage = CodexUsageParser.parse(object: legacyObject, updatedAt: now)
expectEqual(legacyUsage?.remainingPercent5h, 76, "windows without a length keep positional lanes (primary → 5h)")
expectEqual(legacyUsage?.remainingPercent7d, 2, "windows without a length keep positional lanes (secondary → 7d)")

let swappedWindows = CodexWindowMapper.map(
    primary: CodexRateWindow(usedPercent: 20, resetAt: nil, windowSeconds: 604_800),
    secondary: CodexRateWindow(usedPercent: 10, resetAt: nil, windowSeconds: 18_000)
)
expectEqual(swappedWindows.fiveHour?.usedPercent, 10, "an 18000s window maps to the 5h lane regardless of position")
expectEqual(swappedWindows.sevenDay?.usedPercent, 20, "a 604800s window maps to the 7d lane regardless of position")

let collidingWindows = CodexWindowMapper.map(
    primary: CodexRateWindow(usedPercent: 20, resetAt: nil, windowSeconds: 604_800),
    secondary: CodexRateWindow(usedPercent: 30, resetAt: nil, windowSeconds: 604_800)
)
expectEqual(collidingWindows.sevenDay?.usedPercent, 20, "the first window wins a lane collision")
expect(collidingWindows.fiveHour == nil, "a colliding window is dropped rather than mislabeled")

expect(CodexUsageParser.parse(object: [:]) == nil, "a payload without rate_limit does not parse")

// Which credential store is live, and how long since anything wrote to it. The old
// diagnostic only asked whether the fallback file existed, which reads false on a
// normal macOS install and hid the question that actually matters when usage stops
// loading: is anything still refreshing this token?
let credentialFileURL = FileManager.default.temporaryDirectory
    .appendingPathComponent("tt-credentials-\(UUID().uuidString).json")
try? Data("{}".utf8).write(to: credentialFileURL)
defer { try? FileManager.default.removeItem(at: credentialFileURL) }

let keychainWrittenAt = Date().addingTimeInterval(-48 * 3600)
let fromKeychain = ClaudeCredentialSource.detect(
    credentialsFileURL: credentialFileURL,
    keychainModifiedAt: keychainWrittenAt
)
expectEqual(fromKeychain.kind, .keychain, "the keychain wins over the fallback file when both are present")
expectEqual(fromKeychain.lastWrittenAt, keychainWrittenAt, "the keychain's write time is reported")

let fromFile = ClaudeCredentialSource.detect(credentialsFileURL: credentialFileURL, keychainModifiedAt: nil)
expectEqual(fromFile.kind, .credentialsFile, "without a keychain entry the fallback file is the source")
expect(fromFile.lastWrittenAt != nil, "the fallback file reports its write time")

let noSource = ClaudeCredentialSource.detect(
    credentialsFileURL: credentialFileURL.appendingPathExtension("missing"),
    keychainModifiedAt: nil
)
expectEqual(noSource.kind, .none, "no keychain entry and no file means no source")
expect(noSource.age() == nil, "a source that was never written has no age")

// The age is the signal: past roughly the 8h access-token life, nothing is
// refreshing the store any more.
let agedNow = keychainWrittenAt.addingTimeInterval(48 * 3600)
expectEqual(fromKeychain.age(now: agedNow), 48 * 3600, "age measures from the last write")

// A store nothing has written for longer than a token lives is the whole answer to
// "I signed in and it still says sign in", so the menu says how stale it is. Only
// for a lapsed Claude token: any other failure is not explained by the store's age.
func credentialIssue(_ provider: Provider, error: String, source: UsageSource) -> UsageIssue {
    UsageIssueFormatter.issue(
        for: ProviderUsage(
            provider: provider,
            remainingPercent5h: source == .staleCache ? 42 : nil,
            remainingPercent7d: nil,
            resetAt5h: nil,
            resetAt7d: nil,
            source: source,
            error: error,
            plan: nil,
            model: nil,
            updatedAt: Date()
        )
    )
}

let staleStore = ClaudeCredentialSource(kind: .keychain, lastWrittenAt: Date(timeIntervalSince1970: 0))
let expiredIssue = credentialIssue(.claude, error: "HTTP 401", source: .unavailable)
expectEqual(
    CredentialStoreAdvice.staleLine(
        provider: .claude,
        issue: expiredIssue,
        source: staleStore,
        now: Date(timeIntervalSince1970: 48 * 3600)
    ),
    "Credentials not refreshed in 2d 0h",
    "a lapsed token over an abandoned store reports how long it has been abandoned"
)
expect(
    CredentialStoreAdvice.staleLine(
        provider: .claude,
        issue: expiredIssue,
        source: staleStore,
        now: Date(timeIntervalSince1970: CredentialStoreAdvice.staleAfter - 60)
    ) == nil,
    "a store younger than the token life explains nothing the recovery does not already say"
)
expect(
    CredentialStoreAdvice.staleLine(
        provider: .claude,
        issue: credentialIssue(.claude, error: "HTTP 429", source: .unavailable),
        source: staleStore,
        now: Date(timeIntervalSince1970: 48 * 3600)
    ) == nil,
    "a rate limit is not a credential problem, however old the store is"
)
expect(
    CredentialStoreAdvice.staleLine(
        provider: .codex,
        issue: credentialIssue(.codex, error: "HTTP 401", source: .unavailable),
        source: staleStore,
        now: Date(timeIntervalSince1970: 48 * 3600)
    ) == nil,
    "the Claude store says nothing about Codex"
)
// The state this is actually seen in: the last good reading is being carried, so
// the visible status is "using cached data" and only the error names the expiry.
expect(
    CredentialStoreAdvice.staleLine(
        provider: .claude,
        issue: credentialIssue(.claude, error: "credentials expired", source: .staleCache),
        source: staleStore,
        now: Date(timeIntervalSince1970: 48 * 3600)
    ) != nil,
    "a lapsed token behind a carried reading still reports the stale store"
)
expect(
    CredentialStoreAdvice.staleLine(
        provider: .claude,
        issue: expiredIssue,
        source: ClaudeCredentialSource(kind: .none, lastWrittenAt: nil),
        now: Date(timeIntervalSince1970: 48 * 3600)
    ) == nil,
    "a store that was never written has no age to report"
)

// Credential expiry: the app reads tokens the provider CLIs refresh, so it has to
// recognize a lapsed one locally instead of spending a request to be told 401.
expect(CredentialExpiry.isExpired(nil) == false, "an unknown expiry is not treated as expired")
expect(CredentialExpiry.isExpired(Date().addingTimeInterval(3600)) == false, "a token valid for an hour is usable")
expect(CredentialExpiry.isExpired(Date().addingTimeInterval(-1)) == true, "a lapsed token is expired")
expect(
    CredentialExpiry.isExpired(Date().addingTimeInterval(CredentialExpiry.skew / 2)) == true,
    "a token inside the skew window is expired early rather than lapsing mid-request"
)

// {"alg":"none"} . {"exp":1770000000}
let sampleJWT = "eyJhbGciOiJub25lIn0.eyJleHAiOjE3NzAwMDAwMDB9.sig"
expectEqual(
    CredentialExpiry.jwtExpiry(sampleJWT),
    Date(timeIntervalSince1970: 1_770_000_000),
    "the exp claim is read out of a JWT access token"
)
expect(CredentialExpiry.jwtExpiry("not-a-jwt") == nil, "a non-JWT token has no readable expiry")
expectEqual(
    CredentialExpiry.epochMillisecondsDate(1_770_000_000_000),
    Date(timeIntervalSince1970: 1_770_000_000),
    "Claude Code's millisecond expiresAt is read as a date"
)
expect(CredentialExpiry.epochMillisecondsDate(nil) == nil, "an absent expiresAt has no date")

expectEqual(TokenFingerprint.of(["a"]), TokenFingerprint.of(["a"]), "the same token fingerprints the same")
expect(TokenFingerprint.of(["a"]) != TokenFingerprint.of(["b"]), "signing in again changes the fingerprint")
expectEqual(TokenFingerprint.of(["a", "b"]), TokenFingerprint.of(["b", "a"]), "candidate order does not change the fingerprint")

// Pace: one measurement against the line of using the window evenly to its reset.
let paceNow = Date(timeIntervalSince1970: 1_800_000_000)
func pace(_ remaining: Int?, resetIn: TimeInterval?, window: ForecastWindow = .fiveHour) -> UsagePaceReading? {
    UsagePaceEvaluator.reading(
        remainingPercent: remaining,
        resetAt: resetIn.map { paceNow.addingTimeInterval($0) },
        window: window,
        measuredAt: paceNow
    )
}
expectEqual(pace(30, resetIn: 9000)?.pace, UsagePace.ahead, "half the window gone with 70% used is ahead of pace")
expectEqual(pace(50, resetIn: 9000)?.pace, UsagePace.onTrack, "half gone with half used is on track")
expectEqual(pace(41, resetIn: 9000)?.pace, UsagePace.onTrack, "nine points under the line is within the tolerance")
expectEqual(pace(39, resetIn: 9000)?.pace, UsagePace.ahead, "eleven points under the line is ahead")
expectEqual(pace(75, resetIn: 9000)?.pace, UsagePace.behind, "half gone with a quarter used is behind pace")
expectEqual(pace(50, resetIn: 9000)?.expectedRemainingPercent, 50, "the even line sits at the share of the window still to run")
expectEqual(pace(80, resetIn: 302_400, window: .sevenDay)?.pace, UsagePace.behind, "the 7d window is measured against its own length")
expect(pace(50, resetIn: nil) == nil, "without a reset there is no line to compare against")
expect(pace(50, resetIn: -60) == nil, "a window past its reset has no pace")
expect(pace(50, resetIn: 20_000) == nil, "a reset further off than the window is long is not placed")
expect(pace(nil, resetIn: 9000) == nil, "without a balance there is no pace")

let pacedUsage = ProviderUsage(
    provider: .claude, remainingPercent5h: 30, remainingPercent7d: 75,
    resetAt5h: paceNow.addingTimeInterval(9000), resetAt7d: paceNow.addingTimeInterval(302_400),
    source: .api, error: nil, plan: nil, model: nil, updatedAt: paceNow
)
expectEqual(
    UsagePaceText.menuLine(for: pacedUsage, localizer: Localizer(language: .korean)),
    "페이스: 5h 빠름 · 7d 여유",
    "the pace line names each window in Korean"
)
expectEqual(
    UsagePaceText.menuLine(for: pacedUsage, localizer: Localizer(language: .english)),
    "Pace: 5h ahead · 7d under",
    "the pace line names each window in English"
)
var cachedPacedUsage = pacedUsage
cachedPacedUsage.source = .staleCache
expect(UsagePaceEvaluator.reading(for: cachedPacedUsage, window: .fiveHour) == nil, "a cached reading gets no pace")
expect(UsagePaceText.menuLine(for: cachedPacedUsage, localizer: Localizer(language: .english)) == nil, "nor a pace line")
var bridgedPacedUsage = pacedUsage
bridgedPacedUsage.source = .statusLine
expectEqual(UsagePaceEvaluator.reading(for: bridgedPacedUsage, window: .fiveHour)?.pace, UsagePace.ahead, "a status-line measurement is paced at its own time")

// Freed up: a window that had run low, measured again after its reset.
let freedNow = Date(timeIntervalSince1970: 1_800_000_000)
let freedSettings = UsageAlertSettings(notificationsEnabled: true, fiveHourThreshold: 20, sevenDayThreshold: 10, resetWarningMinutes: 0)
func claudeReading(_ remaining5h: Int?, reset5h: Date?, source: UsageSource = .api, at time: Date) -> ProviderUsage {
    ProviderUsage(provider: .claude, remainingPercent5h: remaining5h, remainingPercent7d: 80, resetAt5h: reset5h, resetAt7d: nil, source: source, error: nil, plan: nil, model: nil, updatedAt: time)
}
func claudeOnlySnapshot(_ claude: ProviderUsage) -> UsageSnapshot {
    UsageSnapshot(claude: claude, codex: .unavailable(.codex, error: "Disabled"), updatedAt: claude.updatedAt)
}
func freedIDs(before: ProviderUsage?, after: ProviderUsage, settings: UsageAlertSettings = freedSettings) -> String {
    let previous: [Provider: ProviderUsage] = before.map { [.claude: $0] } ?? [:]
    return UsageAlertEvaluator.freedUpCandidates(previous: previous, current: claudeOnlySnapshot(after), settings: settings)
        .map(\.id).joined(separator: ",")
}
let passedReset = freedNow.addingTimeInterval(-60)
let lowBeforeReset = claudeReading(8, reset5h: passedReset, at: freedNow.addingTimeInterval(-600))
let refilled = claudeReading(100, reset5h: nil, at: freedNow)
expectEqual(freedIDs(before: lowBeforeReset, after: refilled), "claude-5h-freed-\(Int(passedReset.timeIntervalSince1970))", "a low window measured again after its reset is reported freed")
expectEqual(freedIDs(before: claudeReading(50, reset5h: passedReset, at: freedNow.addingTimeInterval(-600)), after: refilled), "", "a window that never ran low is no news when it resets")
expectEqual(freedIDs(before: claudeReading(8, reset5h: freedNow.addingTimeInterval(600), at: freedNow.addingTimeInterval(-600)), after: claudeReading(30, reset5h: freedNow.addingTimeInterval(600), at: freedNow)), "", "a balance cannot refill before its reset has passed")
expectEqual(freedIDs(before: lowBeforeReset, after: claudeReading(100, reset5h: nil, source: .staleCache, at: freedNow)), "", "only a measurement can say the window refilled")
expectEqual(freedIDs(before: lowBeforeReset, after: claudeReading(15, reset5h: nil, at: freedNow)), "", "still under the threshold after the reset is not freed")
expectEqual(freedIDs(before: nil, after: refilled), "", "with no earlier reading there is nothing to compare")
expectEqual(
    freedIDs(before: lowBeforeReset, after: refilled, settings: UsageAlertSettings(notificationsEnabled: true, fiveHourThreshold: 0, sevenDayThreshold: 10, resetWarningMinutes: 0)),
    "",
    "a zero threshold turns the freed alert off with the low alert"
)
expectEqual(
    UsageAlertEvaluator.freedUpCandidates(previous: [.claude: lowBeforeReset], current: claudeOnlySnapshot(refilled), settings: freedSettings, localizer: Localizer(language: .korean)).first?.body,
    "Claude 5h 리셋됨 — 잔량 100%",
    "the freed alert reads in Korean"
)

let seededReadings = UsageAlertEvaluator.lastReadings([:], updatedWith: claudeOnlySnapshot(claudeReading(8, reset5h: passedReset, source: .staleCache, at: freedNow)))
expectEqual(seededReadings[.claude]?.remainingPercent5h, 8, "a cached reading seeds an empty map")
expect(seededReadings[.codex] == nil, "a provider with no numbers is not recorded")
let keptThroughOutage = UsageAlertEvaluator.lastReadings([.claude: lowBeforeReset], updatedWith: claudeOnlySnapshot(claudeReading(8, reset5h: passedReset, source: .staleCache, at: freedNow)))
expectEqual(keptThroughOutage[.claude], lowBeforeReset, "a cached reading does not replace a measurement")
expectEqual(UsageAlertEvaluator.lastReadings([.claude: lowBeforeReset], updatedWith: claudeOnlySnapshot(refilled))[.claude], refilled, "a measurement replaces the last reading")

// Update check: what GitHub names as the latest release, and whether it is newer.
expectEqual(UpdateCheck.latestVersion(fromReleaseJSON: ["tag_name": "v1.1.17", "draft": false, "prerelease": false]), "1.1.17", "a release tag reads without its v")
expect(UpdateCheck.latestVersion(fromReleaseJSON: ["tag_name": "v1.2.0", "prerelease": true]) == nil, "a prerelease is not offered")
expect(UpdateCheck.latestVersion(fromReleaseJSON: ["tag_name": "v1.2.0", "draft": true]) == nil, "a draft is not offered")
expect(UpdateCheck.latestVersion(fromReleaseJSON: ["tag_name": "v1.2.0-beta"]) == nil, "a tag that is not plain numbers is not offered")
expect(UpdateCheck.latestVersion(fromReleaseJSON: ["message": "Not Found"]) == nil, "an error body has no version")
expect(UpdateCheck.isNewer("1.1.17", than: "1.1.16"), "a later patch is newer")
expect(UpdateCheck.isNewer("1.2", than: "1.1.16"), "a later minor is newer whatever the patch")
expect(UpdateCheck.isNewer("1.1.10", than: "1.1.9"), "versions compare as numbers, not text")
expect(!UpdateCheck.isNewer("1.1.16", than: "1.1.16"), "the same version is not newer")
expect(!UpdateCheck.isNewer("1.1.15", than: "1.1.16"), "an older version is not newer")
expect(!UpdateCheck.isNewer("1.1.17", than: "unknown"), "a build that cannot name its version is never prompted")
let updateAttempt = Date(timeIntervalSince1970: 1_800_000_000)
expectEqual(UpdateCheck.nextCheck(after: updateAttempt, succeeded: true), updateAttempt.addingTimeInterval(86_400), "a good check waits a day")
expectEqual(UpdateCheck.nextCheck(after: updateAttempt, succeeded: false), updateAttempt.addingTimeInterval(3600), "a failed check is retried within the hour")
expect(UpdateCheck.isDue(nextCheckAt: nil, now: updateAttempt), "the first check is due at once")
expect(!UpdateCheck.isDue(nextCheckAt: updateAttempt.addingTimeInterval(60), now: updateAttempt), "a check is not due before its time")
expect(UpdateCheck.isDue(nextCheckAt: updateAttempt, now: updateAttempt), "a check is due at its time")

print("TokenTrackerSmokeTests passed")
