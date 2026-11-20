namespace TokenTracker.Windows.Core;

/// Which usage window a forecast is computed against.
public enum ForecastWindow
{
    FiveHour,
    SevenDay
}

public static class ForecastWindowExtensions
{
    /// Language-neutral window marker used in menu lines and alert bodies.
    public static string ShortLabel(this ForecastWindow window) =>
        window == ForecastWindow.FiveHour ? "5h" : "7d";

    /// How long the window runs from opening to reset.
    public static TimeSpan Duration(this ForecastWindow window) =>
        window == ForecastWindow.FiveHour ? TimeSpan.FromHours(5) : TimeSpan.FromDays(7);
}

/// A depletion forecast for one provider/window: how fast remaining budget is
/// being consumed and when, at that pace, it reaches zero. Derived purely from
/// locally stored history — it makes no network call. Mirrors the Swift
/// <c>UsageForecast</c> in <c>Sources/TokenTrackerCore/UsageForecast.swift</c>.
public sealed record UsageForecast(
    double BurnPerHour,
    double SecondsToEmpty,
    DateTimeOffset EmptyAt,
    bool WillEmptyBeforeReset);

public static class UsageForecaster
{
    /// Minimum observed span before a forecast is produced, so a couple of
    /// near-simultaneous samples can't extrapolate to a wild ETA.
    public static readonly TimeSpan MinimumSpan = TimeSpan.FromMinutes(10);

    /// Newest sample must be at most this old, so a window that stopped
    /// reporting (or a long pause) doesn't keep projecting from stale data.
    public static readonly TimeSpan MaximumSampleAge = TimeSpan.FromMinutes(30);

    /// Project when the given provider/window runs out at its recent burn rate.
    /// Returns <c>null</c> when there isn't enough signal: fewer than two
    /// post-reset samples, a span under <see cref="MinimumSpan"/>, a newest
    /// sample older than <see cref="MaximumSampleAge"/>, or a
    /// flat/replenishing window.
    public static UsageForecast? Forecast(
        IReadOnlyList<UsageHistoryEntry> entries,
        Provider provider,
        ForecastWindow window,
        DateTimeOffset? resetAt,
        DateTimeOffset? now = null)
    {
        var current = now ?? DateTimeOffset.Now;

        var points = entries
            .Select(entry => (T: entry.RecordedAt, R: Remaining(entry.Snapshot.Usage(provider), window)))
            .Where(point => point.R is not null)
            .Select(point => (point.T, R: point.R!.Value))
            .OrderBy(point => point.T)
            .ToList();

        if (points.Count < 2)
        {
            return null;
        }

        // Trim to the current window instance: drop everything up to and
        // including the last upward jump (a reset refilling the budget),
        // otherwise a reset reads as negative consumption.
        var startIndex = 0;
        for (var i = 1; i < points.Count; i++)
        {
            if (points[i].R > points[i - 1].R)
            {
                startIndex = i;
            }
        }

        var segment = points.Skip(startIndex).ToList();
        if (segment.Count < 2)
        {
            return null;
        }

        var first = segment[0];
        var last = segment[^1];

        var elapsed = (last.T - first.T).TotalSeconds;
        if (elapsed < MinimumSpan.TotalSeconds)
        {
            return null;
        }

        if ((current - last.T).TotalSeconds > MaximumSampleAge.TotalSeconds)
        {
            return null;
        }

        double drop = first.R - last.R;
        if (drop <= 0)
        {
            return null; // steady or replenishing → no forecast
        }

        var burnPerHour = drop / (elapsed / 3600.0);
        if (burnPerHour <= 0)
        {
            return null;
        }

        var secondsToEmpty = last.R / burnPerHour * 3600.0;
        var emptyAt = current.AddSeconds(secondsToEmpty);
        var willEmptyBeforeReset = resetAt.HasValue && emptyAt < resetAt.Value;

        return new UsageForecast(burnPerHour, secondsToEmpty, emptyAt, willEmptyBeforeReset);
    }

    /// Compact, language-neutral duration like <c>3d 4h</c> / <c>2h 10m</c> /
    /// <c>45m</c> / <c>&lt;1m</c>.
    public static string DurationText(double seconds)
    {
        var totalMinutes = (int)(seconds / 60);
        if (totalMinutes <= 0)
        {
            return "<1m";
        }

        if (totalMinutes < 60)
        {
            return $"{totalMinutes}m";
        }

        var hours = totalMinutes / 60;
        if (hours < 24)
        {
            return $"{hours}h {totalMinutes % 60}m";
        }

        return $"{hours / 24}d {hours % 24}h";
    }

    private static int? Remaining(ProviderUsage usage, ForecastWindow window) =>
        window == ForecastWindow.FiveHour ? usage.RemainingPercent5h : usage.RemainingPercent7d;
}

public static class UsageForecastText
{
    /// The per-provider menu line, or <c>null</c> when there is no forecast to
    /// show. A 7d forecast is marked with the window label; 5h is the unmarked
    /// norm.
    public static string? MenuLine(UsageForecast? forecast, ForecastWindow window, Localizer localizer)
    {
        if (forecast is null)
        {
            return null;
        }

        var line = $"{localizer.Text(L10nKey.ForecastLabel)}: ~{UsageForecaster.DurationText(forecast.SecondsToEmpty)}";
        if (window == ForecastWindow.SevenDay)
        {
            line += $" ({window.ShortLabel()})";
        }

        if (forecast.WillEmptyBeforeReset)
        {
            line += $" · {localizer.Text(L10nKey.ForecastBeforeReset)}";
        }

        return line;
    }
}

/// One provider/window forecast fed to the predictive-alert evaluator.
public sealed record ForecastAlertInput(
    Provider Provider,
    ForecastWindow Window,
    UsageForecast Forecast,
    DateTimeOffset? ResetAt);

public static class UsageForecastAlert
{
    /// Emit one alert per input whose budget is projected to empty before its
    /// reset. <paramref name="enabled"/> is the caller's combined gate
    /// (notifications on and depletion alert on); the id includes the reset
    /// instant so it dedupes per window instance, matching the reset-proximity
    /// alert convention.
    public static IReadOnlyList<UsageAlertCandidate> Candidates(
        IReadOnlyList<ForecastAlertInput> inputs,
        bool enabled,
        Localizer? localizer = null)
    {
        if (!enabled)
        {
            return Array.Empty<UsageAlertCandidate>();
        }

        localizer ??= new Localizer(AppLanguage.English);
        var result = new List<UsageAlertCandidate>();
        foreach (var input in inputs)
        {
            if (!input.Forecast.WillEmptyBeforeReset || input.ResetAt is null)
            {
                continue;
            }

            var windowLabel = input.Window.ShortLabel();
            var resetId = UsageAlertEvaluator.ResetKey(input.ResetAt.Value);
            var name = input.Provider == Provider.Claude ? "Claude" : "Codex";
            result.Add(new UsageAlertCandidate(
                $"{input.Provider.ToId()}-{windowLabel}-empty-before-reset-{resetId}",
                localizer.Text(L10nKey.DepletionAlertTitle),
                localizer.Text(L10nKey.DepletionAlertBody)
                    .Replace("{provider}", name, StringComparison.Ordinal)
                    .Replace("{window}", windowLabel, StringComparison.Ordinal)
                    .Replace("{time}", UsageForecaster.DurationText(input.Forecast.SecondsToEmpty), StringComparison.Ordinal)));
        }

        return result;
    }
}
