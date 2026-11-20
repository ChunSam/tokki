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

        // The cache keeps carried readings whole; only what is shown drops the
        // number of a window that has reset since (see `withoutLapsedWindows`).
        cacheStore.save(snapshot)
        return UsageSnapshotCachePolicy.withoutLapsedWindows(snapshot, at: snapshot.updatedAt)
    }

    /// The snapshot saved by the last refresh, within the stale tolerance — the
    /// readings carried before the app last quit, with any window that has reset
    /// since still holding its number, so the freed-up alert can be seeded from it.
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

        if claudeEnabled, !snapshot.claude.isAvailable, canReuse(stale.claude, at: updatedAt, maxAge: maxAge) {
            snapshot.claude = markStale(stale.claude, error: snapshot.claude.error)
        }

        if codexEnabled, !snapshot.codex.isAvailable, canReuse(stale.codex, at: updatedAt, maxAge: maxAge) {
            snapshot.codex = markStale(stale.codex, error: snapshot.codex.error)
        }

        return snapshot
    }

    private static func canReuse(_ usage: ProviderUsage, at now: Date, maxAge: TimeInterval) -> Bool {
        let age = now.timeIntervalSince(usage.updatedAt)
        return usage.isAvailable && usage.source != .unavailable && age >= 0 && age <= maxAge
    }

    /// The snapshot as it may be shown. A balance only describes the window
    /// instance it was measured in; once that window's reset has passed it has
    /// refilled, so a carried number is not a low reading but a wrong one — and the
    /// fallback can last for hours, long enough to show a 5h window's last few
    /// percent, in the 7d warning colour even, well after both had reset. The
    /// alerts already skip such a window (`windowHasReset`); this keeps the display
    /// in step. The number goes and the reset instant stays, so the menu says the
    /// window has reset rather than that it was never reported. A carried reading
    /// with no live window left is not shown at all. A window with no reset instant
    /// cannot be judged and is kept.
    ///
    /// Only for display: what `apply` returns is what the cache keeps, numbers and
    /// all, because the freed-up alert seeds its "was low" reading from that file at
    /// launch. Hiding the number there too lost the alert for a window that ran low
    /// and reset while the app was restarted mid-outage. Measurements pass through
    /// untouched: the provider does not report a window whose reset has passed.
    public static func withoutLapsedWindows(_ snapshot: UsageSnapshot, at now: Date) -> UsageSnapshot {
        var copy = snapshot
        copy.claude = withoutLapsedWindows(copy.claude, at: now)
        copy.codex = withoutLapsedWindows(copy.codex, at: now)
        return copy
    }

    private static func withoutLapsedWindows(_ usage: ProviderUsage, at now: Date) -> ProviderUsage {
        guard usage.source == .staleCache else { return usage }
        var copy = usage
        if let reset = copy.resetAt5h, reset <= now {
            copy.remainingPercent5h = nil
        }
        if let reset = copy.resetAt7d, reset <= now {
            copy.remainingPercent7d = nil
        }
        guard copy.isAvailable else {
            return ProviderUsage(
                provider: usage.provider, remainingPercent5h: nil, remainingPercent7d: nil,
                resetAt5h: nil, resetAt7d: nil, source: .unavailable, error: usage.error,
                plan: nil, model: nil, updatedAt: now
            )
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
