using System.Globalization;

namespace TokenTracker.Windows.Core;

public sealed record UsageAlertSettings
{
    public UsageAlertSettings(
        bool notificationsEnabled,
        int fiveHourThreshold,
        int sevenDayThreshold,
        int resetWarningMinutes,
        TimeSpan? stalledAfter = null)
    {
        NotificationsEnabled = notificationsEnabled;
        FiveHourThreshold = Math.Clamp(fiveHourThreshold, 0, 100);
        SevenDayThreshold = Math.Clamp(sevenDayThreshold, 0, 100);
        ResetWarningMinutes = Math.Max(0, resetWarningMinutes);
        var stalled = stalledAfter ?? TimeSpan.Zero;
        StalledAfter = stalled > TimeSpan.Zero ? stalled : TimeSpan.Zero;
    }

    public bool NotificationsEnabled { get; }
    public int FiveHourThreshold { get; }
    public int SevenDayThreshold { get; }
    public int ResetWarningMinutes { get; }

    /// How long a cached reading may stand before it is reported as stalled.
    /// Zero disables the alert, matching the other thresholds here.
    public TimeSpan StalledAfter { get; }
}

public sealed record UsageAlertCandidate(string Id, string Title, string Body);

public static class UsageAlertEvaluator
{
    public static IReadOnlyList<UsageAlertCandidate> Candidates(
        UsageSnapshot snapshot,
        UsageAlertSettings settings,
        DateTimeOffset? now = null,
        Localizer? localizer = null)
    {
        if (!settings.NotificationsEnabled)
        {
            return Array.Empty<UsageAlertCandidate>();
        }

        localizer ??= new Localizer(AppLanguage.English);
        var current = now ?? DateTimeOffset.Now;
        return new[]
            {
                snapshot.Usage(Provider.Claude),
                snapshot.Usage(Provider.Codex)
            }
            .SelectMany(usage => Candidates(usage, settings, current, localizer))
            .ToArray();
    }

    private static IEnumerable<UsageAlertCandidate> Candidates(
        ProviderUsage usage,
        UsageAlertSettings settings,
        DateTimeOffset now,
        Localizer localizer)
    {
        if (!usage.IsAvailable)
        {
            yield break;
        }

        // First, because it says how much the numbers below it are still worth.
        var stalled = StalledAlert(usage, settings, now, localizer);
        if (stalled is not null)
        {
            yield return stalled;
        }

        // The low-balance bodies are symbols only, so they read the same in every
        // language and skip the localizer.
        if (usage.RemainingPercent5h is { } fiveHourRemaining &&
            settings.FiveHourThreshold > 0 &&
            fiveHourRemaining <= settings.FiveHourThreshold &&
            !WindowHasReset(usage.ResetAt5h, now))
        {
            yield return new UsageAlertCandidate(
                $"{usage.Provider.ToId()}-5h-low",
                localizer.Text(L10nKey.FiveHourAlertTitle),
                $"{usage.DisplayName} 5h {fiveHourRemaining}% <= {settings.FiveHourThreshold}%");
        }

        if (usage.RemainingPercent7d is { } sevenDayRemaining &&
            settings.SevenDayThreshold > 0 &&
            sevenDayRemaining <= settings.SevenDayThreshold &&
            !WindowHasReset(usage.ResetAt7d, now))
        {
            yield return new UsageAlertCandidate(
                $"{usage.Provider.ToId()}-7d-low",
                localizer.Text(L10nKey.SevenDayAlertTitle),
                $"{usage.DisplayName} 7d {sevenDayRemaining}% <= {settings.SevenDayThreshold}%");
        }

        foreach (var candidate in ResetAlerts(usage, settings, now, localizer))
        {
            yield return candidate;
        }
    }

    /// A balance only ever describes the window it was measured in. Once that
    /// window's reset has passed, the number on hand belongs to a window that has
    /// already refilled, so it is no reason to warn — and a cached reading can sit
    /// on the wrong side of a reset for hours. FormatWindowReset makes the same
    /// call about a cached reading's time; this keeps the alerts in step.
    ///
    /// With no reset instant to judge by there is nothing to go on, so the alert
    /// stands.
    private static bool WindowHasReset(DateTimeOffset? resetAt, DateTimeOffset now)
    {
        if (resetAt is not { } date)
        {
            return false;
        }

        return date <= now;
    }

    /// The part of an alert id that names the window instance, in Unix seconds
    /// rounded to the nearest minute. Providers report the same reset a fraction
    /// of a second apart from poll to poll — Claude's whole-minute resets came
    /// back as 04:59:59 and 05:00:00 in turn — so whole seconds gave one window a
    /// new id on every flip, and the tray, which only remembers ids still active,
    /// sent the same alert again each time. Distinct instances of one window are
    /// hours apart, so rounding never merges two of them.
    public static long ResetKey(DateTimeOffset resetAt) =>
        (long)Math.Round(resetAt.ToUnixTimeMilliseconds() / 60000.0, MidpointRounding.AwayFromZero) * 60;

    /// How long a cached reading may stand before the tray is quietly out of
    /// date: three missed polls, so one failed fetch is not news, with a floor of
    /// 30 minutes — the same freshness bound the status line bridge uses, and
    /// short enough to catch a lapsed token within the hour.
    public static TimeSpan StalledThreshold(int refreshIntervalSeconds) =>
        TimeSpan.FromSeconds(Math.Max(30 * 60, refreshIntervalSeconds * 3));

    /// Collection has stalled: what is on screen is a cached measurement that has
    /// aged past StalledAfter. Nothing else alerts on this, so a lapsed token
    /// went unnoticed for as long as nobody looked — a cached percentage stays
    /// plausible while it quietly stops being current.
    ///
    /// A provider with nothing to show at all is deliberately left alone. It
    /// already reads as "--", which cannot be mistaken for a live number, and
    /// alerting there would fire on the first failed fetch for a provider that
    /// was never signed in to.
    private static UsageAlertCandidate? StalledAlert(
        ProviderUsage usage,
        UsageAlertSettings settings,
        DateTimeOffset now,
        Localizer localizer)
    {
        if (settings.StalledAfter <= TimeSpan.Zero || usage.Source != UsageSource.StaleCache)
        {
            return null;
        }

        var age = now - usage.UpdatedAt;
        if (age <= settings.StalledAfter)
        {
            return null;
        }

        var minutes = Math.Max(0, (int)age.TotalMinutes);
        var ago = minutes < 60 ? $"{minutes}m" : $"{minutes / 60}h{minutes % 60}m";
        // A stable id: one alert per outage, and the tray drops the id once the
        // provider measures again, so the next outage is reported afresh.
        return new UsageAlertCandidate(
            $"{usage.Provider.ToId()}-stalled",
            localizer.Text(L10nKey.StalledAlertTitle),
            localizer.Text(L10nKey.StalledAlertBody)
                .Replace("{provider}", usage.DisplayName, StringComparison.Ordinal)
                .Replace("{time}", ago, StringComparison.Ordinal));
    }

    /// A window that had run low has reset and can be used again. The low-balance
    /// alert said to hold off; this says when that is over, which otherwise only
    /// shows to someone who opens the menu.
    ///
    /// <paramref name="previous"/> holds each provider's last reading with numbers,
    /// whatever its source, so a reset that passes during an outage is still
    /// reported once the provider is measured again. Only a measurement can say the
    /// window refilled. The alert fires on the one poll that sees the change: by the
    /// next, the refilled reading has become <paramref name="previous"/> and the
    /// condition no longer holds.
    public static IReadOnlyList<UsageAlertCandidate> FreedUpCandidates(
        IReadOnlyDictionary<Provider, ProviderUsage> previous,
        UsageSnapshot current,
        UsageAlertSettings settings,
        Localizer? localizer = null)
    {
        if (!settings.NotificationsEnabled)
        {
            return Array.Empty<UsageAlertCandidate>();
        }

        localizer ??= new Localizer(AppLanguage.English);
        var candidates = new List<UsageAlertCandidate>();
        foreach (var provider in new[] { Provider.Claude, Provider.Codex })
        {
            var after = current.Usage(provider);
            if (!previous.TryGetValue(provider, out var before) ||
                (after.Source != UsageSource.Api && after.Source != UsageSource.StatusLine))
            {
                continue;
            }

            var windows = new[]
            {
                ("5h", settings.FiveHourThreshold, before.RemainingPercent5h, before.ResetAt5h, after.RemainingPercent5h),
                ("7d", settings.SevenDayThreshold, before.RemainingPercent7d, before.ResetAt7d, after.RemainingPercent7d)
            };
            foreach (var (label, threshold, was, resetAt, now) in windows)
            {
                if (threshold <= 0 ||
                    was is not { } wasPercent || wasPercent > threshold ||
                    resetAt is not { } resetDate || resetDate > after.UpdatedAt ||
                    now is not { } nowPercent || nowPercent <= threshold || nowPercent <= wasPercent)
                {
                    continue;
                }

                candidates.Add(new UsageAlertCandidate(
                    $"{provider.ToId()}-{label}-freed-{ResetKey(resetDate)}",
                    localizer.Text(L10nKey.FreedUpAlertTitle),
                    localizer.Text(L10nKey.FreedUpAlertBody)
                        .Replace("{provider}", after.DisplayName, StringComparison.Ordinal)
                        .Replace("{window}", label, StringComparison.Ordinal)
                        .Replace("{percent}", nowPercent.ToString(CultureInfo.InvariantCulture), StringComparison.Ordinal)));
            }
        }

        return candidates;
    }

    /// What <see cref="FreedUpCandidates"/> compares against on the next poll. A
    /// measurement replaces the provider's entry; a cached or missing reading leaves
    /// it alone, so an outage does not erase the low balance a reset will be
    /// measured against. A cached reading still fills an empty entry, which is how
    /// a cached snapshot seeds the map at launch.
    public static Dictionary<Provider, ProviderUsage> LastReadings(
        IReadOnlyDictionary<Provider, ProviderUsage> previous,
        UsageSnapshot snapshot)
    {
        var readings = new Dictionary<Provider, ProviderUsage>(previous);
        foreach (var provider in new[] { Provider.Claude, Provider.Codex })
        {
            var usage = snapshot.Usage(provider);
            if (!usage.IsAvailable)
            {
                continue;
            }

            if (usage.Source == UsageSource.Api || usage.Source == UsageSource.StatusLine || !readings.ContainsKey(provider))
            {
                readings[provider] = usage;
            }
        }

        return readings;
    }

    /// A reset is worth warning about only while there is something left to lose:
    /// what the window has not used by then is gone. A window at or below its
    /// low-balance threshold gets no warning — its refill is good news, which the
    /// freed-up alert reports once it lands. The number has to be a measurement,
    /// since the body states it and a cached one may have been spent since.
    private static IEnumerable<UsageAlertCandidate> ResetAlerts(
        ProviderUsage usage,
        UsageAlertSettings settings,
        DateTimeOffset now,
        Localizer localizer)
    {
        if (settings.ResetWarningMinutes <= 0 ||
            (usage.Source != UsageSource.Api && usage.Source != UsageSource.StatusLine))
        {
            yield break;
        }

        var warningWindow = TimeSpan.FromMinutes(settings.ResetWarningMinutes);
        var resets = new[]
        {
            ("5h", usage.ResetAt5h, usage.RemainingPercent5h, settings.FiveHourThreshold),
            ("7d", usage.ResetAt7d, usage.RemainingPercent7d, settings.SevenDayThreshold)
        };
        foreach (var (label, resetAt, unusedPercent, threshold) in resets)
        {
            if (resetAt is not { } date || unusedPercent is not { } unused || unused <= threshold)
            {
                continue;
            }

            var timeLeft = date - now;
            if (timeLeft <= TimeSpan.Zero || timeLeft > warningWindow)
            {
                continue;
            }

            var minutes = Math.Max(0, (int)Math.Ceiling(timeLeft.TotalMinutes));
            var resetId = ResetKey(date);
            yield return new UsageAlertCandidate(
                $"{usage.Provider.ToId()}-{label}-reset-{resetId}",
                localizer.Text(L10nKey.ResetAlertTitle),
                localizer.Text(L10nKey.ResetAlertBody)
                    .Replace("{provider}", usage.DisplayName, StringComparison.Ordinal)
                    .Replace("{window}", label, StringComparison.Ordinal)
                    .Replace("{time}", $"{minutes}m", StringComparison.Ordinal)
                    .Replace("{percent}", unused.ToString(CultureInfo.InvariantCulture), StringComparison.Ordinal));
        }
    }
}

public static class UsageSnapshotExtensions
{
    public static ProviderUsage Usage(this UsageSnapshot snapshot, Provider provider) => provider switch
    {
        Provider.Claude => snapshot.Claude,
        Provider.Codex => snapshot.Codex,
        _ => throw new ArgumentOutOfRangeException(nameof(provider), provider, null)
    };

    public static string ToId(this Provider provider) => provider switch
    {
        Provider.Claude => "claude",
        Provider.Codex => "codex",
        _ => provider.ToString().ToLowerInvariant()
    };
}
