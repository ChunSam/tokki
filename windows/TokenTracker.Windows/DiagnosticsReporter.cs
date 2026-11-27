using System.Reflection;
using System.Runtime.InteropServices;
using TokenTracker.Windows.Core;

namespace TokenTracker.Windows;

internal sealed class DiagnosticsReporter
{
    private readonly AppSettings settings;
    private readonly UsageHistoryStore historyStore;
    private readonly UsageSnapshot? snapshot;
    private readonly DateTimeOffset? lastSuccessfulRefreshAt;
    private readonly int runningInstanceCount;
    private readonly ClaudeCliRefreshAttempt? claudeCliRefreshAttempt;
    private readonly IReadOnlyDictionary<Provider, CostState> costStates;

    public DiagnosticsReporter(
        AppSettings settings,
        UsageHistoryStore historyStore,
        UsageSnapshot? snapshot,
        DateTimeOffset? lastSuccessfulRefreshAt,
        int runningInstanceCount,
        ClaudeCliRefreshAttempt? claudeCliRefreshAttempt,
        IReadOnlyDictionary<Provider, CostState> costStates)
    {
        this.settings = settings;
        this.historyStore = historyStore;
        this.snapshot = snapshot;
        this.lastSuccessfulRefreshAt = lastSuccessfulRefreshAt;
        this.runningInstanceCount = runningInstanceCount;
        this.claudeCliRefreshAttempt = claudeCliRefreshAttempt;
        this.costStates = costStates;
    }

    public string DiagnosticsText()
    {
        var lines = new List<string>
        {
            "Tokki Diagnostics",
            $"Generated: {IsoString(DateTimeOffset.Now)}",
            $"App version: {AppVersion}",
            $"OS: {RuntimeInformation.OSDescription}",
            $"Architecture: {RuntimeInformation.ProcessArchitecture}",
            $"Display mode: {settings.DisplayMode}",
            $"Provider labels: {settings.ProviderLabelStyle}",
            $"Refresh interval: {settings.RefreshIntervalSeconds}s",
            $"Claude enabled: {settings.ClaudeEnabled}",
            $"Codex enabled: {settings.CodexEnabled}",
            $"Language: {settings.Language}",
            $"Notifications enabled: {settings.NotificationsEnabled}",
            $"5h alert threshold: {settings.FiveHourAlertThreshold}%",
            $"7d alert threshold: {settings.SevenDayAlertThreshold}%",
            $"Reset alert window: {settings.ResetAlertMinutes}m",
            $"History retention: {settings.HistoryRetentionDays}d",
            $"History entries: {historyStore.Load().Count}",
            $"History trend: {HistoryTrendText(AppLanguage.English)}",
            $"Last successful update: {(lastSuccessfulRefreshAt is null ? "none" : IsoString(lastSuccessfulRefreshAt.Value))}",
            $"Running instances: {runningInstanceCount}"
        };

        if (settings.RefreshIntervalSeconds < 60)
        {
            lines.Add($"Refresh warning: {new Localizer(AppLanguage.English).Text(L10nKey.RefreshIntervalWarning)}");
        }

        // Not just whether the file is there: when usage stops loading, the question
        // that matters is whether anything is still writing to it. The Claude access
        // token lives ~8h, so an age well past that says the store has been abandoned
        // rather than that the token was merely unlucky. The source is reported for
        // parity with macOS, where the keychain usually wins; Windows has no keychain,
        // so the file is always the live store here.
        var claudeSource = ClaudeCredentialSource.Detect(ClaudeCredentialsPath);
        var credentialLastWritten = claudeSource.LastWrittenAt is { } written ? IsoString(written) : "unknown";
        var credentialAge = claudeSource.Age(DateTimeOffset.Now) is { } age
            ? UsageForecaster.DurationText(age.TotalSeconds)
            : "unknown";
        lines.Add($"Claude credential source: {claudeSource.Kind}");
        lines.Add($"Claude credential last written: {credentialLastWritten}");
        lines.Add($"Claude credential age: {credentialAge}");
        lines.Add($"Claude credentials file exists: {File.Exists(ClaudeCredentialsPath)}");
        // Whether the app may start Claude Code to renew a lapsed token, which CLI it
        // would start, and how the last try went.
        var renewalSetting = settings.RenewClaudeTokenWithCli ? "on" : "off";
        var lastRenewal = claudeCliRefreshAttempt is { } attempt
            ? IsoString(attempt.AttemptedAt) + " " + attempt.Outcome
            : "none";
        lines.Add($"Claude CLI renewal: {renewalSetting}");
        lines.Add($"Claude CLI: {ClaudeCliRefresh.Locate() ?? "not found"}");
        lines.Add($"Claude CLI last renewal: {lastRenewal}");
        lines.Add($"Codex auth file exists: {File.Exists(CodexAuthPath)}");
        // Codex renews its store only while it is used, so this age is what says a
        // lapsed Codex token was simply left alone.
        var codexSource = ClaudeCredentialSource.Detect(CodexAuthPath);
        var codexLastWritten = codexSource.LastWrittenAt is { } codexWritten ? IsoString(codexWritten) : "unknown";
        var codexAge = codexSource.Age(DateTimeOffset.Now) is { } codexElapsed
            ? UsageForecaster.DurationText(codexElapsed.TotalSeconds)
            : "unknown";
        lines.Add($"Codex auth last written: {codexLastWritten}");
        lines.Add($"Codex auth age: {codexAge}");
        foreach (var provider in new[] { Provider.Claude, Provider.Codex })
        {
            var costState = costStates.TryGetValue(provider, out var state) ? state : new CostState();
            lines.AddRange(CostDiagnostics.Lines(provider, costState));
        }

        // Whether the bridge is installed at all, and how old its measurement is.
        // "File present but measured 9h ago" is the whole diagnosis for a user who
        // set it up and then stopped running the CLI.
        var statusLineStore = new ClaudeStatusLineStore();
        lines.Add($"Claude status line bridge: {statusLineStore.Path}");
        lines.Add($"Claude status line file exists: {statusLineStore.FileExists}");
        var statusLineRecord = statusLineStore.Read();
        if (statusLineRecord is null)
        {
            lines.Add("Claude status line record: none");
        }
        else
        {
            var measurementAge = UsageForecaster.DurationText(
                statusLineRecord.Age(DateTimeOffset.Now).TotalSeconds);
            lines.Add($"Claude status line measured: {IsoString(statusLineRecord.MeasuredAt)}");
            lines.Add($"Claude status line measurement age: {measurementAge}");
            lines.Add($"Claude status line last written: {IsoString(statusLineRecord.WrittenAt)}");
        }

        if (snapshot is null)
        {
            lines.Add("Snapshot: none");
        }
        else
        {
            lines.Add($"Snapshot updated: {IsoString(snapshot.UpdatedAt)}");
            lines.AddRange(DiagnosticsLines(snapshot.Claude));
            lines.AddRange(DiagnosticsLines(snapshot.Codex));
        }

        return string.Join(Environment.NewLine, lines);
    }

    public string HistoryTrendText(AppLanguage? language = null)
    {
        if (snapshot is null)
        {
            return new Localizer(language ?? settings.Language).Text(L10nKey.NotEnoughHistory);
        }

        return UsageHistoryFormatter.TrendSummary(
            historyStore.Load(),
            snapshot,
            localizer: new Localizer(language ?? settings.Language));
    }

    public static string ClaudeCredentialsPath => AppPaths.ClaudeCredentialsPath();

    public static string CodexAuthPath => AppPaths.CodexAuthPath();

    private static IEnumerable<string> DiagnosticsLines(ProviderUsage usage)
    {
        var issue = UsageIssueFormatter.Issue(usage, new Localizer(AppLanguage.English));
        var cost = SubscriptionCostText.Line(usage, new Localizer(AppLanguage.English));
        var costText = cost is null ? "off" : cost.Label + " " + cost.Value;
        return new[]
        {
            $"{usage.DisplayName} source: {usage.Source}",
            $"{usage.DisplayName} status: {issue.Kind}",
            $"{usage.DisplayName} 5h remaining: {DisplayFormatter.FormatPercent(usage.RemainingPercent5h)}",
            $"{usage.DisplayName} 7d remaining: {DisplayFormatter.FormatPercent(usage.RemainingPercent7d)}",
            $"{usage.DisplayName} 5h reset: {IsoStringOrDash(usage.ResetAt5h)}",
            $"{usage.DisplayName} 7d reset: {IsoStringOrDash(usage.ResetAt7d)}",
            $"{usage.DisplayName} plan: {usage.Plan ?? "--"}",
            $"{usage.DisplayName} money past plan: {costText}",
            $"{usage.DisplayName} technical error: {issue.TechnicalDetail ?? "--"}"
        };
    }

    private static string AppVersion =>
        AppVersionText.Format(Assembly.GetExecutingAssembly().GetName().Version);

    private static string IsoString(DateTimeOffset date) =>
        date.ToUniversalTime().ToString("O");

    private static string IsoStringOrDash(DateTimeOffset? date) =>
        date is null ? "--" : IsoString(date.Value);
}
