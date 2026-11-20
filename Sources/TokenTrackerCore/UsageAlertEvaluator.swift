import Foundation

public struct UsageAlertSettings: Equatable, Sendable {
    public let notificationsEnabled: Bool
    public let fiveHourThreshold: Int
    public let sevenDayThreshold: Int
    public let resetWarningMinutes: Int
    /// How long a cached reading may stand before it is reported as stalled.
    /// Zero disables the alert, matching the other thresholds here.
    public let stalledAfter: TimeInterval

    public init(
        notificationsEnabled: Bool,
        fiveHourThreshold: Int,
        sevenDayThreshold: Int,
        resetWarningMinutes: Int,
        stalledAfter: TimeInterval = 0
    ) {
        self.notificationsEnabled = notificationsEnabled
        self.fiveHourThreshold = max(0, min(100, fiveHourThreshold))
        self.sevenDayThreshold = max(0, min(100, sevenDayThreshold))
        self.resetWarningMinutes = max(0, resetWarningMinutes)
        self.stalledAfter = max(0, stalledAfter)
    }
}

public struct UsageAlertCandidate: Equatable, Sendable {
    public let id: String
    public let title: String
    public let body: String

    public init(id: String, title: String, body: String) {
        self.id = id
        self.title = title
        self.body = body
    }
}

public enum UsageAlertEvaluator {
    public static func candidates(
        snapshot: UsageSnapshot,
        settings: UsageAlertSettings,
        now: Date = Date(),
        localizer: Localizer = Localizer(language: .english)
    ) -> [UsageAlertCandidate] {
        guard settings.notificationsEnabled else {
            return []
        }

        return Provider.allCases.flatMap { provider in
            let usage = snapshot.usage(for: provider)
            return candidates(
                for: usage,
                settings: settings,
                now: now,
                localizer: localizer
            )
        }
    }

    private static func candidates(
        for usage: ProviderUsage,
        settings: UsageAlertSettings,
        now: Date,
        localizer: Localizer
    ) -> [UsageAlertCandidate] {
        guard usage.isAvailable else {
            return []
        }

        var alerts: [UsageAlertCandidate] = []
        // First, because it says how much the numbers below it are still worth.
        if let stalled = stalledAlert(for: usage, settings: settings, now: now, localizer: localizer) {
            alerts.append(stalled)
        }

        // The low-balance bodies are symbols only, so they read the same in every
        // language and skip the localizer.
        if let remaining = usage.remainingPercent5h,
           settings.fiveHourThreshold > 0,
           remaining <= settings.fiveHourThreshold,
           !windowHasReset(usage.resetAt5h, now: now) {
            alerts.append(
                UsageAlertCandidate(
                    id: "\(usage.provider.rawValue)-5h-low",
                    title: localizer.text(.fiveHourAlertTitle),
                    body: "\(usage.provider.displayName) 5h \(remaining)% <= \(settings.fiveHourThreshold)%"
                )
            )
        }

        if let remaining = usage.remainingPercent7d,
           settings.sevenDayThreshold > 0,
           remaining <= settings.sevenDayThreshold,
           !windowHasReset(usage.resetAt7d, now: now) {
            alerts.append(
                UsageAlertCandidate(
                    id: "\(usage.provider.rawValue)-7d-low",
                    title: localizer.text(.sevenDayAlertTitle),
                    body: "\(usage.provider.displayName) 7d \(remaining)% <= \(settings.sevenDayThreshold)%"
                )
            )
        }

        alerts.append(contentsOf: resetAlerts(for: usage, settings: settings, now: now, localizer: localizer))
        return alerts
    }

    /// A balance only ever describes the window it was measured in. Once that
    /// window's reset has passed, the number on hand belongs to a window that has
    /// already refilled, so it is no reason to warn — and a cached reading can sit
    /// on the wrong side of a reset for hours. `resetCountdownSuffix` makes the
    /// same call about a cached reading's time; this keeps the alerts in step.
    ///
    /// With no reset instant to judge by there is nothing to go on, so the alert
    /// stands.
    private static func windowHasReset(_ resetAt: Date?, now: Date) -> Bool {
        guard let resetAt else { return false }
        return resetAt <= now
    }

    /// The part of an alert id that names the window instance, in Unix seconds
    /// rounded to the nearest minute. Providers report the same reset a fraction
    /// of a second apart from poll to poll — Claude's whole-minute resets came
    /// back as `04:59:59` and `05:00:00` in turn — so whole seconds gave one
    /// window a new id on every flip, and the coordinators, which only remember
    /// ids still active, sent the same alert again each time. Distinct instances
    /// of one window are hours apart, so rounding never merges two of them.
    public static func resetKey(_ resetAt: Date) -> Int {
        Int((resetAt.timeIntervalSince1970 / 60).rounded()) * 60
    }

    /// How long a cached reading may stand before the menu bar is quietly out of
    /// date: three missed polls, so one failed fetch is not news, with a floor of
    /// 30 minutes — the same freshness bound the status line bridge uses, and
    /// short enough to catch a lapsed token within the hour.
    public static func stalledThreshold(refreshInterval: TimeInterval) -> TimeInterval {
        max(30 * 60, refreshInterval * 3)
    }

    /// Collection has stalled: what is on screen is a cached measurement that has
    /// aged past `stalledAfter`. Nothing else alerts on this, so a lapsed token
    /// went unnoticed for as long as nobody looked — a cached percentage stays
    /// plausible while it quietly stops being current.
    ///
    /// A provider with nothing to show at all is deliberately left alone. It
    /// already reads as `--`, which cannot be mistaken for a live number, and
    /// alerting there would fire on the first failed fetch for a provider that
    /// was never signed in to.
    private static func stalledAlert(
        for usage: ProviderUsage,
        settings: UsageAlertSettings,
        now: Date,
        localizer: Localizer
    ) -> UsageAlertCandidate? {
        guard settings.stalledAfter > 0, usage.source == .staleCache else { return nil }
        let age = now.timeIntervalSince(usage.updatedAt)
        guard age > settings.stalledAfter else { return nil }
        let minutes = max(0, Int(age / 60))
        let ago = minutes < 60 ? "\(minutes)m" : "\(minutes / 60)h\(minutes % 60)m"
        // A stable id: one alert per outage, and the coordinator drops the id once
        // the provider measures again, so the next outage is reported afresh.
        return UsageAlertCandidate(
            id: "\(usage.provider.rawValue)-stalled",
            title: localizer.text(.stalledAlertTitle),
            body: localizer.text(.stalledAlertBody)
                .replacingOccurrences(of: "{provider}", with: usage.provider.displayName)
                .replacingOccurrences(of: "{time}", with: ago)
        )
    }

    /// A window that had run low has reset and can be used again. The low-balance
    /// alert said to hold off; this says when that is over, which otherwise only
    /// shows to someone who opens the menu.
    ///
    /// `previous` holds each provider's last reading with numbers, whatever its
    /// source, so a reset that passes during an outage is still reported once the
    /// provider is measured again. Only a measurement can say the window refilled.
    /// The alert fires on the one poll that sees the change: by the next, the
    /// refilled reading has become `previous` and the condition no longer holds.
    public static func freedUpCandidates(
        previous: [Provider: ProviderUsage],
        current: UsageSnapshot,
        settings: UsageAlertSettings,
        localizer: Localizer = Localizer(language: .english)
    ) -> [UsageAlertCandidate] {
        guard settings.notificationsEnabled else {
            return []
        }

        return Provider.allCases.flatMap { provider -> [UsageAlertCandidate] in
            let after = current.usage(for: provider)
            guard let before = previous[provider], after.source == .api || after.source == .statusLine else {
                return []
            }
            let windows: [(label: String, threshold: Int, was: Int?, resetAt: Date?, now: Int?)] = [
                ("5h", settings.fiveHourThreshold, before.remainingPercent5h, before.resetAt5h, after.remainingPercent5h),
                ("7d", settings.sevenDayThreshold, before.remainingPercent7d, before.resetAt7d, after.remainingPercent7d)
            ]
            return windows.compactMap { window in
                guard window.threshold > 0,
                      let was = window.was, was <= window.threshold,
                      let resetAt = window.resetAt, resetAt <= after.updatedAt,
                      let now = window.now, now > window.threshold, now > was else {
                    return nil
                }
                return UsageAlertCandidate(
                    id: "\(provider.rawValue)-\(window.label)-freed-\(resetKey(resetAt))",
                    title: localizer.text(.freedUpAlertTitle),
                    body: localizer.text(.freedUpAlertBody)
                        .replacingOccurrences(of: "{provider}", with: provider.displayName)
                        .replacingOccurrences(of: "{window}", with: window.label)
                        .replacingOccurrences(of: "{percent}", with: "\(now)")
                )
            }
        }
    }

    /// What `freedUpCandidates` compares against on the next poll. A measurement
    /// replaces the provider's entry; a cached or missing reading leaves it alone,
    /// so an outage does not erase the low balance a reset will be measured
    /// against. A cached reading still fills an empty entry, which is how a cached
    /// snapshot seeds the map at launch.
    public static func lastReadings(
        _ previous: [Provider: ProviderUsage],
        updatedWith snapshot: UsageSnapshot
    ) -> [Provider: ProviderUsage] {
        var readings = previous
        for provider in Provider.allCases {
            let usage = snapshot.usage(for: provider)
            guard usage.isAvailable else { continue }
            if usage.source == .api || usage.source == .statusLine || readings[provider] == nil {
                readings[provider] = usage
            }
        }
        return readings
    }

    /// A reset is worth warning about only while there is something left to lose:
    /// what the window has not used by then is gone. A window at or below its
    /// low-balance threshold gets no warning — its refill is good news, which the
    /// freed-up alert reports once it lands. The number has to be a measurement,
    /// since the body states it and a cached one may have been spent since.
    private static func resetAlerts(
        for usage: ProviderUsage,
        settings: UsageAlertSettings,
        now: Date,
        localizer: Localizer
    ) -> [UsageAlertCandidate] {
        guard settings.resetWarningMinutes > 0, usage.source == .api || usage.source == .statusLine else {
            return []
        }

        let warningWindow = TimeInterval(settings.resetWarningMinutes * 60)
        let resets: [(label: String, date: Date?, unused: Int?, threshold: Int)] = [
            ("5h", usage.resetAt5h, usage.remainingPercent5h, settings.fiveHourThreshold),
            ("7d", usage.resetAt7d, usage.remainingPercent7d, settings.sevenDayThreshold)
        ]

        return resets.compactMap { reset in
            guard let date = reset.date, let unused = reset.unused, unused > reset.threshold else {
                return nil
            }
            let timeLeft = date.timeIntervalSince(now)
            guard timeLeft > 0, timeLeft <= warningWindow else {
                return nil
            }
            let minutes = max(0, Int(ceil(timeLeft / 60)))
            let resetID = resetKey(date)
            return UsageAlertCandidate(
                id: "\(usage.provider.rawValue)-\(reset.label)-reset-\(resetID)",
                title: localizer.text(.resetAlertTitle),
                body: localizer.text(.resetAlertBody)
                    .replacingOccurrences(of: "{provider}", with: usage.provider.displayName)
                    .replacingOccurrences(of: "{window}", with: reset.label)
                    .replacingOccurrences(of: "{time}", with: "\(minutes)m")
                    .replacingOccurrences(of: "{percent}", with: "\(unused)")
            )
        }
    }
}

extension UsageSnapshot {
    public func usage(for provider: Provider) -> ProviderUsage {
        switch provider {
        case .claude:
            return claude
        case .codex:
            return codex
        }
    }
}
