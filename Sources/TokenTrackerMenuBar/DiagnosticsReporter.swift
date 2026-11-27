import Foundation
import TokenTrackerCore

@MainActor
struct DiagnosticsReporter {
    let settings: Settings
    let historyStore: UsageHistoryStore
    let snapshot: UsageSnapshot?
    let lastSuccessfulRefreshAt: Date?
    let runningInstanceCount: Int
    let claudeCLIRefreshAttempt: ClaudeCLIRefreshAttempt?
    let costStates: [Provider: CostState]

    func diagnosticsText() -> String {
        var lines: [String] = []
        lines.append("Tokki Diagnostics")
        lines.append("Generated: \(isoString(Date()))")
        lines.append("App version: \(appVersion) (\(appBuild))")
        lines.append("Bundle id: \(Bundle.main.bundleIdentifier ?? "unknown")")
        lines.append("macOS: \(ProcessInfo.processInfo.operatingSystemVersionString)")
        lines.append("Architecture: \(machineArchitecture())")
        lines.append("Display mode: \(settings.displayMode.rawValue)")
        lines.append("Provider labels: \(settings.providerLabelStyle.rawValue)")
        lines.append("Refresh interval: \(Int(settings.refreshInterval))s")
        lines.append("Claude enabled: \(settings.claudeEnabled)")
        lines.append("Codex enabled: \(settings.codexEnabled)")
        lines.append("Language: \(settings.language.rawValue)")
        lines.append("Notifications enabled: \(settings.notificationsEnabled)")
        lines.append("5h alert threshold: \(settings.fiveHourAlertThreshold)%")
        lines.append("7d alert threshold: \(settings.sevenDayAlertThreshold)%")
        lines.append("Reset alert window: \(settings.resetAlertMinutes)m")
        lines.append("History retention: \(settings.historyRetentionDays)d")
        lines.append("History entries: \(historyStore.load().count)")
        lines.append("History trend: \(historyTrendText(language: .english))")
        lines.append("Last successful update: \(lastSuccessfulRefreshAt.map(isoString) ?? "none")")
        lines.append("Running instances: \(runningInstanceCount)")
        if settings.refreshInterval < 60 {
            lines.append("Refresh warning: \(Localizer(language: .english).text(.refreshIntervalWarning))")
        }
        // Not just "does the fallback file exist" — on a normal macOS install it does
        // not, and the answer that matters is which store is live and whether anything
        // is still writing to it. The Claude access token lives ~8h and only the CLI
        // refreshes this store, so an age well past that is the whole explanation for
        // a token that keeps lapsing.
        let claudeSource = ClaudeCredentialSource.detect(credentialsFileURL: Self.claudeCredentialsURL)
        lines.append("Claude credential source: \(claudeSource.kind.rawValue)")
        lines.append("Claude credential last written: \(claudeSource.lastWrittenAt.map(isoString) ?? "unknown")")
        lines.append("Claude credential age: \(claudeSource.age().map(UsageForecaster.durationText) ?? "unknown")")
        lines.append("Claude credentials file exists: \(fileExists(at: Self.claudeCredentialsURL))")
        // Whether the app may start Claude Code to renew a lapsed token, which CLI it
        // would start, and how the last try went.
        lines.append("Claude CLI renewal: \(settings.renewClaudeTokenWithCLI ? "on" : "off")")
        lines.append("Claude CLI: \(ClaudeCLIRefresh.locate() ?? "not found")")
        lines.append("Claude CLI last renewal: \(claudeCLIRefreshAttempt.map { "\(isoString($0.attemptedAt)) \($0.outcome.rawValue)" } ?? "none")")
        // Whether the bridge is installed at all, and how old its measurement is.
        // "File present but measured 9h ago" is the whole diagnosis for a user who
        // set it up and then stopped running the CLI.
        let statusLineStore = ClaudeStatusLineStore()
        lines.append("Claude status line bridge: \(statusLineStore.url.path)")
        lines.append("Claude status line file exists: \(statusLineStore.fileExists)")
        if let record = statusLineStore.read() {
            lines.append("Claude status line measured: \(isoString(record.measuredAt))")
            lines.append("Claude status line measurement age: \(UsageForecaster.durationText(record.age()))")
            lines.append("Claude status line last written: \(isoString(record.writtenAt))")
        } else {
            lines.append("Claude status line record: none")
        }
        lines.append("Codex auth file exists: \(fileExists(at: Self.codexAuthURL))")
        // Codex renews its store only while it is used, so this age is what says a
        // lapsed Codex token was simply left alone.
        let codexSource = ClaudeCredentialSource.detect(credentialsFileURL: Self.codexAuthURL, keychainModifiedAt: nil)
        lines.append("Codex auth last written: \(codexSource.lastWrittenAt.map(isoString) ?? "unknown")")
        lines.append("Codex auth age: \(codexSource.age().map(UsageForecaster.durationText) ?? "unknown")")
        for provider in Provider.allCases {
            lines.append(contentsOf: CostDiagnostics.lines(provider: provider, state: costStates[provider] ?? CostState()))
        }

        if let snapshot {
            lines.append("Snapshot updated: \(isoString(snapshot.updatedAt))")
            lines.append(contentsOf: diagnosticsLines(for: snapshot.claude))
            lines.append(contentsOf: diagnosticsLines(for: snapshot.codex))
        } else {
            lines.append("Snapshot: none")
        }

        return lines.joined(separator: "\n")
    }

    func historyTrendText(language: AppLanguage? = nil) -> String {
        guard let snapshot else {
            return Localizer(language: language ?? settings.language).text(.notEnoughHistory)
        }
        return UsageHistoryFormatter.trendSummary(
            entries: historyStore.load(),
            current: snapshot,
            localizer: Localizer(language: language ?? settings.language)
        )
    }

    static var claudeCredentialsURL: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".claude/.credentials.json")
    }

    static var codexAuthURL: URL {
        FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".codex/auth.json")
    }

    private func diagnosticsLines(for usage: ProviderUsage) -> [String] {
        let issue = UsageIssueFormatter.issue(for: usage, localizer: Localizer(language: .english))
        let cost = SubscriptionCostText.line(for: usage, localizer: Localizer(language: .english))
        return [
            "\(usage.provider.displayName) source: \(usage.source.rawValue)",
            "\(usage.provider.displayName) status: \(issue.kind.rawValue)",
            "\(usage.provider.displayName) 5h remaining: \(DisplayFormatter.formatPercent(usage.remainingPercent5h))",
            "\(usage.provider.displayName) 7d remaining: \(DisplayFormatter.formatPercent(usage.remainingPercent7d))",
            "\(usage.provider.displayName) 5h reset: \(isoStringOrDash(usage.resetAt5h))",
            "\(usage.provider.displayName) 7d reset: \(isoStringOrDash(usage.resetAt7d))",
            "\(usage.provider.displayName) plan: \(usage.plan ?? "--")",
            "\(usage.provider.displayName) money past plan: \(cost.map { "\($0.label) \($0.value)" } ?? "off")",
            "\(usage.provider.displayName) technical error: \(issue.technicalDetail ?? "--")"
        ]
    }

    private var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "unknown"
    }

    private var appBuild: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "unknown"
    }

    private func machineArchitecture() -> String {
        #if arch(arm64)
        return "arm64"
        #elseif arch(x86_64)
        return "x86_64"
        #else
        return "unknown"
        #endif
    }

    private func fileExists(at url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.path)
    }

    private func isoString(_ date: Date) -> String {
        ISO8601DateFormatter().string(from: date)
    }

    private func isoStringOrDash(_ date: Date?) -> String {
        guard let date else { return "--" }
        return isoString(date)
    }
}
