import Foundation

/// Which usage window a forecast is computed against.
public enum ForecastWindow: Sendable {
    case fiveHour
    case sevenDay

    /// Language-neutral window marker used in menu lines and alert bodies.
    public var shortLabel: String {
        switch self {
        case .fiveHour: return "5h"
        case .sevenDay: return "7d"
        }
    }

    /// How long the window runs from opening to reset.
    public var duration: TimeInterval {
        switch self {
        case .fiveHour: return 5 * 3600
        case .sevenDay: return 7 * 24 * 3600
        }
    }
}

/// A depletion forecast for one provider/window: how fast remaining budget is
/// being consumed and when, at that pace, it reaches zero. Derived purely from
/// locally stored history — it makes **no** network call.
public struct UsageForecast: Equatable, Sendable {
    /// Remaining-percentage consumed per hour (always > 0 for a produced forecast).
    public let burnPerHour: Double
    public let secondsToEmpty: Double
    public let emptyAt: Date
    /// True when the projected empty instant falls before the window's reset.
    public let willEmptyBeforeReset: Bool

    public init(burnPerHour: Double, secondsToEmpty: Double, emptyAt: Date, willEmptyBeforeReset: Bool) {
        self.burnPerHour = burnPerHour
        self.secondsToEmpty = secondsToEmpty
        self.emptyAt = emptyAt
        self.willEmptyBeforeReset = willEmptyBeforeReset
    }
}

public enum UsageForecaster {
    /// Minimum observed span before a forecast is produced, so a couple of
    /// near-simultaneous samples can't extrapolate to a wild ETA.
    public static let minimumSpan: TimeInterval = 600 // 10 minutes

    /// Newest sample must be at most this old, so a window that stopped
    /// reporting (or a long pause) doesn't keep projecting from stale data.
    public static let maximumSampleAge: TimeInterval = 1800 // 30 minutes

    /// Project when the given provider/window runs out at its recent burn rate.
    /// Returns `nil` when there isn't enough signal: fewer than two post-reset
    /// samples, a span under `minimumSpan`, a newest sample older than
    /// `maximumSampleAge`, or a flat/replenishing window.
    public static func forecast(
        entries: [UsageHistoryEntry],
        provider: Provider,
        window: ForecastWindow,
        resetAt: Date?,
        now: Date = Date()
    ) -> UsageForecast? {
        let points: [(t: Date, r: Int)] = entries.compactMap { entry in
            guard let remaining = remaining(entry.snapshot.usage(for: provider), window) else {
                return nil
            }
            return (entry.recordedAt, remaining)
        }.sorted { $0.t < $1.t }

        guard points.count >= 2 else { return nil }

        // Trim to the current window instance: drop everything up to and
        // including the last upward jump (a reset refilling the budget),
        // otherwise a reset reads as negative consumption.
        var startIndex = 0
        for index in 1..<points.count where points[index].r > points[index - 1].r {
            startIndex = index
        }
        let segment = Array(points[startIndex...])
        guard segment.count >= 2, let first = segment.first, let last = segment.last else {
            return nil
        }

        let elapsed = last.t.timeIntervalSince(first.t)
        guard elapsed >= minimumSpan else { return nil }
        guard now.timeIntervalSince(last.t) <= maximumSampleAge else { return nil }

        let drop = Double(first.r - last.r)
        guard drop > 0 else { return nil } // steady or replenishing → no forecast

        let burnPerHour = drop / (elapsed / 3600)
        guard burnPerHour > 0 else { return nil }

        let secondsToEmpty = Double(last.r) / burnPerHour * 3600
        let emptyAt = now.addingTimeInterval(secondsToEmpty)
        let willEmptyBeforeReset = resetAt.map { emptyAt < $0 } ?? false

        return UsageForecast(
            burnPerHour: burnPerHour,
            secondsToEmpty: secondsToEmpty,
            emptyAt: emptyAt,
            willEmptyBeforeReset: willEmptyBeforeReset
        )
    }

    /// Compact, language-neutral duration like `3d 4h` / `2h 10m` / `45m` / `<1m`.
    public static func durationText(_ seconds: Double) -> String {
        let totalMinutes = Int(seconds / 60)
        if totalMinutes <= 0 { return "<1m" }
        if totalMinutes < 60 { return "\(totalMinutes)m" }
        let hours = totalMinutes / 60
        if hours < 24 { return "\(hours)h \(totalMinutes % 60)m" }
        return "\(hours / 24)d \(hours % 24)h"
    }

    private static func remaining(_ usage: ProviderUsage, _ window: ForecastWindow) -> Int? {
        switch window {
        case .fiveHour: return usage.remainingPercent5h
        case .sevenDay: return usage.remainingPercent7d
        }
    }
}

public enum UsageForecastText {
    /// The per-provider menu line, or `nil` when there is no forecast to show.
    /// A 7d forecast is marked with the window label; 5h is the unmarked norm.
    public static func menuLine(forecast: UsageForecast?, window: ForecastWindow, localizer: Localizer) -> String? {
        guard let forecast else { return nil }
        var line = "\(localizer.text(.forecastLabel)): ~\(UsageForecaster.durationText(forecast.secondsToEmpty))"
        if window == .sevenDay {
            line += " (\(window.shortLabel))"
        }
        if forecast.willEmptyBeforeReset {
            line += " · \(localizer.text(.forecastBeforeReset))"
        }
        return line
    }
}

/// One provider/window forecast fed to the predictive-alert evaluator.
public struct ForecastAlertInput: Sendable {
    public let provider: Provider
    public let window: ForecastWindow
    public let forecast: UsageForecast
    public let resetAt: Date?

    public init(provider: Provider, window: ForecastWindow, forecast: UsageForecast, resetAt: Date?) {
        self.provider = provider
        self.window = window
        self.forecast = forecast
        self.resetAt = resetAt
    }
}

public enum UsageForecastAlert {
    /// Emit one alert per input whose budget is projected to empty before its
    /// reset. `enabled` is the caller's combined gate (notifications on **and**
    /// depletion alert on); the id includes the reset instant so it dedupes per
    /// window instance, matching the reset-proximity alert convention.
    public static func candidates(
        inputs: [ForecastAlertInput],
        enabled: Bool,
        localizer: Localizer = Localizer(language: .english)
    ) -> [UsageAlertCandidate] {
        guard enabled else { return [] }

        return inputs.compactMap { input in
            guard input.forecast.willEmptyBeforeReset, let resetAt = input.resetAt else {
                return nil
            }
            let windowLabel = input.window.shortLabel
            let resetID = UsageAlertEvaluator.resetKey(resetAt)
            return UsageAlertCandidate(
                id: "\(input.provider.rawValue)-\(windowLabel)-empty-before-reset-\(resetID)",
                title: localizer.text(.depletionAlertTitle),
                body: localizer.text(.depletionAlertBody)
                    .replacingOccurrences(of: "{provider}", with: input.provider.displayName)
                    .replacingOccurrences(of: "{window}", with: windowLabel)
                    .replacingOccurrences(of: "{time}", with: UsageForecaster.durationText(input.forecast.secondsToEmpty))
            )
        }
    }
}
