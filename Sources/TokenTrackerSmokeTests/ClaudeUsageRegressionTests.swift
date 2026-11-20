import Foundation
import TokenTrackerCore

func runClaudeUsageRegressionTests() throws {
    try verifyClaudeResponses()
    try verifyCacheLifetime()
    verifyLapsedCachedWindows()
}

/// A cached reading stands in for hours; a window whose reset passed in that time
/// has refilled, so its old number must not be shown. Mirrored in the Windows tests.
private func verifyLapsedCachedWindows() {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let measuredAt = now.addingTimeInterval(-3 * 3600)
    func reading(_ provider: Provider, fiveHour: Int?, reset5h: Date?, sevenDay: Int?, reset7d: Date?) -> ProviderUsage {
        ProviderUsage(provider: provider, remainingPercent5h: fiveHour, remainingPercent7d: sevenDay,
                      resetAt5h: reset5h, resetAt7d: reset7d, source: .api, error: nil, plan: nil,
                      model: nil, updatedAt: measuredAt)
    }
    // What the cache keeps, and what is shown of it.
    func carry(claude: ProviderUsage, codex: ProviderUsage? = nil) -> UsageSnapshot {
        let stale = UsageSnapshot(claude: claude, codex: codex ?? .unavailable(.codex, error: "Disabled"), updatedAt: measuredAt)
        return UsageSnapshotCachePolicy.apply(current: failure(at: now), stale: stale, codexEnabled: codex != nil, updatedAt: now)
    }
    func reuse(claude: ProviderUsage, codex: ProviderUsage? = nil) -> UsageSnapshot {
        UsageSnapshotCachePolicy.withoutLapsedWindows(carry(claude: claude, codex: codex), at: now)
    }
    let lapsed = now.addingTimeInterval(-2 * 3600)
    let live = now.addingTimeInterval(24 * 3600)

    let fiveHourLapsed = reuse(claude: reading(.claude, fiveHour: 3, reset5h: lapsed, sevenDay: 60, reset7d: live)).claude
    expectEqual(fiveHourLapsed.source, .staleCache, "A reading with one live window is still carried")
    expect(fiveHourLapsed.remainingPercent5h == nil, "A window whose reset passed loses its cached number")
    expectEqual(fiveHourLapsed.resetAt5h, lapsed, "Its reset instant stays, so the menu says it has reset")
    expectEqual(fiveHourLapsed.remainingPercent7d, 60, "A window still running keeps its cached number")
    expectEqual(fiveHourLapsed.error, "HTTP 429 from Claude API", "The carried reading keeps the current failure")
    expectEqual(DisplayFormatter.displayPercent(fiveHourLapsed), 60, "The menu bar falls back to the live window")

    // The case the review reproduced: both windows reset while the token was lapsed,
    // and the menu bar still read `AI 6%` in the 7d warning colour.
    let bothLapsed = reuse(claude: reading(.claude, fiveHour: 3, reset5h: lapsed, sevenDay: 6, reset7d: now.addingTimeInterval(-600)))
    expectEqual(bothLapsed.claude.source, .unavailable, "A reading with no live window is not carried at all")
    expectEqual(bothLapsed.claude.error, "HTTP 429 from Claude API", "It reports the current failure instead")
    expectEqual(DisplayFormatter.statusTitle(snapshot: bothLapsed, mode: .lowestRemaining, showResetCountdown: true), "AI --", "No stale low number reaches the menu bar")
    expect(!DisplayFormatter.isSevenDayWarning(bothLapsed.claude), "Nor its warning colour")

    let resetsNow = reuse(claude: reading(.claude, fiveHour: 3, reset5h: now, sevenDay: 60, reset7d: live)).claude
    expect(resetsNow.remainingPercent5h == nil, "A reset at this very instant has passed, as the alerts judge it")
    let unknownReset = reuse(claude: reading(.claude, fiveHour: 3, reset5h: nil, sevenDay: nil, reset7d: nil)).claude
    expectEqual(unknownReset.remainingPercent5h, 3, "A window with no reset instant cannot be judged and is kept")
    expectEqual(unknownReset.source, .staleCache, "So the reading is still carried")
    let allLive = reading(.claude, fiveHour: 3, reset5h: now.addingTimeInterval(600), sevenDay: 6, reset7d: live)
    let carried = reuse(claude: allLive).claude
    expectEqual(carried.remainingPercent5h, 3, "A low window that has not reset keeps its number")
    expectEqual(carried.remainingPercent7d, 6, "Both windows of it")

    let codexLapsed = reuse(
        claude: allLive,
        codex: reading(.codex, fiveHour: nil, reset5h: nil, sevenDay: 4, reset7d: lapsed)
    )
    expectEqual(codexLapsed.codex.source, .unavailable, "Codex is held to the same rule")
    expectEqual(codexLapsed.codex.error, "HTTP 429 from Codex API", "With its own failure reason")

    let measured = reading(.claude, fiveHour: 3, reset5h: lapsed, sevenDay: 60, reset7d: live)
    let measuredSnapshot = UsageSnapshot(claude: measured, codex: .unavailable(.codex, error: "Disabled"), updatedAt: measuredAt)
    expectEqual(UsageSnapshotCachePolicy.withoutLapsedWindows(measuredSnapshot, at: now), measuredSnapshot, "Only carried readings are trimmed; a measurement passes untouched")

    // The cache file keeps the carried reading whole: at launch the freed-up alert
    // seeds its "was low" reading from it, so a window that ran low and reset while
    // the app was restarted mid-outage is still reported once measured again.
    let lowThenReset = reading(.claude, fiveHour: 8, reset5h: lapsed, sevenDay: 80, reset7d: live)
    let kept = carry(claude: lowThenReset).claude
    expectEqual(kept.source, .staleCache, "The cache keeps the carried reading")
    expectEqual(kept.remainingPercent5h, 8, "With the number of a window that has since reset")
    let keptAll = carry(claude: reading(.claude, fiveHour: 3, reset5h: lapsed, sevenDay: 6, reset7d: lapsed)).claude
    expectEqual(keptAll.remainingPercent7d, 6, "Even when no window of it is live any more")
    let seeded = UsageAlertEvaluator.lastReadings([:], updatedWith: carry(claude: lowThenReset))
    let measuredAgain = ProviderUsage(provider: .claude, remainingPercent5h: 100, remainingPercent7d: 80,
                                      resetAt5h: nil, resetAt7d: live, source: .api, error: nil, plan: nil,
                                      model: nil, updatedAt: now.addingTimeInterval(1200))
    let freed = UsageAlertEvaluator.freedUpCandidates(
        previous: seeded,
        current: UsageSnapshot(claude: measuredAgain, codex: .unavailable(.codex, error: "Disabled"), updatedAt: measuredAgain.updatedAt),
        settings: UsageAlertSettings(notificationsEnabled: true, fiveHourThreshold: 20, sevenDayThreshold: 10, resetWarningMinutes: 0)
    ).map(\.id)
    expectEqual(freed, ["claude-5h-freed-\(UsageAlertEvaluator.resetKey(lapsed))"], "A restart mid-outage still reports the window freed")
}

private func verifyClaudeResponses() throws {
    let now = Date(timeIntervalSince1970: 1_800_000_000)
    let usage = try parse(#"{"plan_type":" max ","five_hour":{"utilization":15,"resets_at":"2026-05-27T12:00:00.000Z"},"seven_day":{"utilization":100,"resets_at":"2026-05-29T12:00:00Z"}}"#, at: now)
    expectEqual(usage.remainingPercent5h, 85, "Claude five-hour remaining")
    expectEqual(usage.remainingPercent7d, 0, "Claude seven-day remaining")
    expectEqual(usage.resetAt5h, ISO8601DateFormatter().date(from: "2026-05-27T12:00:00Z"), "Fractional reset date")
    expectEqual(usage.resetAt7d, ISO8601DateFormatter().date(from: "2026-05-29T12:00:00Z"), "Whole-second reset date")
    expectEqual(usage.plan, "max", "Claude plan is trimmed")
    expectEqual(usage.updatedAt, now, "API data records its measurement time")
    expectEqual(usage.source, .api, "Valid Claude response is marked as API data")
    expect(usage.error == nil, "Valid Claude response has no error")

    let fiveHour = try parse(#"{"five_hour":{"utilization":0},"seven_day":null}"#)
    expectEqual(fiveHour.remainingPercent5h, 100, "Zero usage is a valid measurement")
    expect(fiveHour.remainingPercent7d == nil, "Claude allows a missing seven-day window")
    let sevenDay = try parse(#"{"seven_day":{"utilization":"25.4"}}"#)
    expect(sevenDay.remainingPercent5h == nil, "Claude allows a missing five-hour window")
    expectEqual(sevenDay.remainingPercent7d, 75, "Claude accepts numeric strings")
    expectEqual(sevenDay.plan, "team", "Claude uses the credential plan as a fallback")

    let invalid = [
        "{}", "[]", "null",
        #"{"five_hour":{},"seven_day":null}"#,
        #"{"five_hour":{"utilization":true}}"#,
        #"{"five_hour":{"utilization":"unknown"}}"#,
        #"{"five_hour":{"utilization":"NaN"}}"#,
        #"{"seven_day":{"utilization":"Infinity"}}"#,
        #"{"five_hour":{"utilization":[]}}"#
    ]
    for json in invalid {
        do {
            _ = try parse(json)
            expect(false, "Claude must reject missing or invalid measurements: \(json)")
        } catch {
            expectEqual(error as? UsageError, .invalidResponse, "Invalid Claude data has a specific error")
        }
    }
    for value in [Double.nan, Double.infinity] {
        do {
            _ = try ClaudeUsageParser.parse(["five_hour": ["utilization": value]])
            expect(false, "Non-finite Claude utilization is rejected")
        } catch {
            expectEqual(error as? UsageError, .invalidResponse, "Non-finite data is not treated as API success")
        }
    }
    let bounded = try parse(#"{"five_hour":{"utilization":1e100},"seven_day":{"utilization":-1e100}}"#)
    expectEqual(bounded.remainingPercent5h, 0, "Large utilization is clamped before integer conversion")
    expectEqual(bounded.remainingPercent7d, 100, "Negative utilization is clamped before integer conversion")
}

private func verifyCacheLifetime() throws {
    let now = Date(timeIntervalSince1970: floor(Date().timeIntervalSince1970))
    var cached = success(at: now)
    for hour in 1...13 {
        let pollAt = now.addingTimeInterval(Double(hour) * 3600)
        var current = failure(at: pollAt)
        current.codex = success(at: pollAt).codex
        cached = UsageSnapshotCachePolicy.apply(current: current, stale: cached, updatedAt: pollAt)
        expectEqual(cached.claude.source, hour <= 12 ? .staleCache : .unavailable, "Repeated failures cannot extend the twelve-hour lifetime")
        if hour <= 12 {
            expectEqual(cached.claude.remainingPercent5h, 63, "Unexpired cache keeps its value")
            expectEqual(cached.claude.updatedAt, now, "Repeated failures preserve the measurement time")
        } else {
            expect(!cached.claude.isAvailable, "Expired Claude data is discarded")
        }
        expectEqual(cached.claude.error, current.claude.error, "Cache preserves the latest failure reason")
        expectEqual(cached.codex, current.codex, "Fresh Codex data does not extend Claude's lifetime")
        expectEqual(cached.updatedAt, pollAt, "The snapshot records the poll time separately")
    }

    var staggered = success(at: now.addingTimeInterval(-2 * 3600))
    staggered.codex.updatedAt = now.addingTimeInterval(-1800)
    staggered.updatedAt = now
    let short = UsageSnapshotCachePolicy.apply(current: failure(at: now), stale: staggered, updatedAt: now, maxAge: 3600)
    expectEqual(short.claude.source, .unavailable, "Custom tolerance expires Claude independently")
    expectEqual(short.codex.source, .staleCache, "Custom tolerance keeps recent Codex data")
    expectEqual(short.codex.updatedAt, staggered.codex.updatedAt, "Codex measurement time is preserved")
    let long = UsageSnapshotCachePolicy.apply(current: failure(at: now), stale: staggered, updatedAt: now, maxAge: 48 * 3600)
    expectEqual(long.claude.source, .staleCache, "Longer configured tolerance is honored")

    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let url = directory.appendingPathComponent("cache.json")
    let store = CacheStore(url: url)
    let measuredAt = now.addingTimeInterval(-13 * 3600)
    let firstFailureAt = now.addingTimeInterval(-3 * 3600)
    store.save(UsageSnapshotCachePolicy.apply(
        current: failure(at: firstFailureAt), stale: success(at: measuredAt), updatedAt: firstFailureAt
    ))
    guard let loaded = store.load(maxAge: 12 * 3600) else {
        expect(false, "Recent poll snapshot survives a restart")
        return
    }
    expectEqual(loaded.claude.updatedAt, measuredAt, "Disk cache preserves the measurement time")
    let expired = UsageSnapshotCachePolicy.apply(current: failure(at: now), stale: loaded, updatedAt: now)
    expect(!expired.claude.isAvailable && !expired.codex.isAvailable, "Restart does not revive expired measurements")
    expectEqual(expired.claude.error, "HTTP 429 from Claude API", "Expired cache preserves the current failure reason")

    let encoder = JSONEncoder()
    encoder.dateEncodingStrategy = .iso8601
    try encoder.encode(success(at: now)).write(to: url)
    expect(store.load(maxAge: 12 * 3600) == nil, "Legacy caches with untrustworthy timestamps are discarded")
    store.save(success(at: now))
    expect(store.load(maxAge: 12 * 3600) != nil, "New cache is readable after discarding a legacy cache")
}

private func parse(_ json: String, at now: Date = Date()) throws -> ProviderUsage {
    let raw = try JSONSerialization.jsonObject(with: Data(json.utf8), options: .fragmentsAllowed)
    return try ClaudeUsageParser.parse(raw, fallbackPlan: "team", updatedAt: now)
}

private func success(at date: Date) -> UsageSnapshot {
    func usage(_ provider: Provider) -> ProviderUsage {
        ProviderUsage(provider: provider, remainingPercent5h: 63, remainingPercent7d: 80,
                      resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil,
                      model: nil, updatedAt: date)
    }
    return UsageSnapshot(claude: usage(.claude), codex: usage(.codex), updatedAt: date)
}

private func failure(at date: Date) -> UsageSnapshot {
    UsageSnapshot(claude: .unavailable(.claude, error: "HTTP 429 from Claude API"),
                  codex: .unavailable(.codex, error: "HTTP 429 from Codex API"), updatedAt: date)
}
