import Foundation

@MainActor
public final class UsageService {
    private let settings: Settings
    private let cacheStore: CacheStore
    private let claudeClient: ClaudeUsageClient
    private let codexClient: CodexUsageClient

    public convenience init(settings: Settings) {
        self.init(
            settings: settings,
            cacheStore: CacheStore(),
            claudeClient: ClaudeUsageClient(cliRefresher: ClaudeCLIRefresher()),
            codexClient: CodexUsageClient()
        )
    }

    /// The last time Claude Code was started to renew the token, for diagnostics.
    public var claudeCLIRefreshLastAttempt: ClaudeCLIRefreshAttempt? {
        claudeClient.cliRefresher?.lastAttempt
    }

    init(settings: Settings, cacheStore: CacheStore, claudeClient: ClaudeUsageClient, codexClient: CodexUsageClient) {
        self.settings = settings
        self.cacheStore = cacheStore
        self.claudeClient = claudeClient
        self.codexClient = codexClient
    }

    public func refresh() async -> UsageSnapshot {
        let claudeEnabled = settings.claudeEnabled
        let codexEnabled = settings.codexEnabled
        let renewClaudeToken = settings.renewClaudeTokenWithCLI
        let claudeClient = self.claudeClient
        let codexClient = self.codexClient

        async let claudeResult: ProviderUsage = claudeEnabled
            ? claudeClient.fetch(renewingExpiredToken: renewClaudeToken)
            : ProviderUsage.unavailable(.claude, error: "Disabled")
        async let codexResult: ProviderUsage = codexEnabled
            ? codexClient.fetch()
            : ProviderUsage.unavailable(.codex, error: "Disabled")

        var snapshot = UsageSnapshot(
            claude: await claudeResult,
            codex: await codexResult,
            updatedAt: Date()
        )

        snapshot = UsageSnapshotCachePolicy.apply(
            current: snapshot,
            stale: cacheStore.load(maxAge: TimeInterval(settings.staleToleranceHours) * 3600),
            claudeEnabled: claudeEnabled,
            codexEnabled: codexEnabled,
            maxAge: TimeInterval(settings.staleToleranceHours) * 3600
        )

        cacheStore.save(snapshot)
        return snapshot
    }

    /// The snapshot saved by the last refresh, within the stale tolerance — what
    /// was on screen before the app last quit.
    public func cachedSnapshot() -> UsageSnapshot? {
        cacheStore.load(maxAge: TimeInterval(settings.staleToleranceHours) * 3600)
    }
}

public enum UsageSnapshotCachePolicy {
    public static func apply(
        current: UsageSnapshot,
        stale: UsageSnapshot?,
        claudeEnabled: Bool = true,
        codexEnabled: Bool = true,
        updatedAt: Date = Date(),
        maxAge: TimeInterval = 12 * 3600
    ) -> UsageSnapshot {
        guard let stale else {
            return current
        }

        var snapshot = current

        if claudeEnabled, !snapshot.claude.isAvailable, let reusable = reusable(stale.claude, at: updatedAt, maxAge: maxAge) {
            snapshot.claude = markStale(reusable, error: snapshot.claude.error)
        }

        if codexEnabled, !snapshot.codex.isAvailable, let reusable = reusable(stale.codex, at: updatedAt, maxAge: maxAge) {
            snapshot.codex = markStale(reusable, error: snapshot.codex.error)
        }

        return snapshot
    }

    /// What of a last good reading may still stand in, or `nil` when nothing may.
    private static func reusable(_ usage: ProviderUsage, at now: Date, maxAge: TimeInterval) -> ProviderUsage? {
        let age = now.timeIntervalSince(usage.updatedAt)
        guard usage.source != .unavailable, age >= 0, age <= maxAge else { return nil }
        let live = withoutLapsedWindows(usage, at: now)
        return live.isAvailable ? live : nil
    }

    /// A balance only describes the window instance it was measured in. Once that
    /// window's reset has passed it has refilled, so the cached number is not a low
    /// reading but a wrong one — and the fallback can last for hours, long enough
    /// to show a 5h window's last few percent, in the 7d warning colour even, well
    /// after both had reset. The alerts already skip such a window
    /// (`windowHasReset`); this keeps the display in step. The number goes and the
    /// reset instant stays, so the menu says the window has reset rather than that
    /// it was never reported. A window with no reset instant cannot be judged and
    /// is kept.
    private static func withoutLapsedWindows(_ usage: ProviderUsage, at now: Date) -> ProviderUsage {
        var copy = usage
        if let reset = copy.resetAt5h, reset <= now {
            copy.remainingPercent5h = nil
        }
        if let reset = copy.resetAt7d, reset <= now {
            copy.remainingPercent7d = nil
        }
        return copy
    }

    private static func markStale(_ usage: ProviderUsage, error: String?) -> ProviderUsage {
        var copy = usage
        copy.source = .staleCache
        copy.error = error
        // Keep the measurement time; redisplaying a value does not refresh it.
        return copy
    }
}
