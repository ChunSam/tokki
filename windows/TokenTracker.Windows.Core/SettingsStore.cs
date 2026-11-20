using System.Text.Json;
using System.Text.Json.Serialization;

namespace TokenTracker.Windows.Core;

public sealed class AppSettings
{
    public DisplayMode DisplayMode { get; set; } = DisplayMode.LowestRemaining;
    public ProviderLabelStyle ProviderLabelStyle { get; set; } = ProviderLabelStyle.Abbreviation;
    public int RefreshIntervalSeconds { get; set; } = 300;
    public bool ClaudeEnabled { get; set; } = true;
    public bool CodexEnabled { get; set; } = true;
    public bool LaunchAtLogin { get; set; }
    public AppLanguage Language { get; set; } = AppLanguage.System;
    public bool NotificationsEnabled { get; set; }
    public int FiveHourAlertThreshold { get; set; } = 20;
    public int SevenDayAlertThreshold { get; set; } = 10;
    public int ResetAlertMinutes { get; set; } = 10;
    public int HistoryRetentionDays { get; set; } = 7;

    /// <summary>
    /// How long a last-known-good reading keeps standing in for a failing fetch.
    /// The Claude access token lives ~8h and is only refreshed by running Claude
    /// Code, so the gap to cover is "until the next Claude Code session", not the
    /// brief network blip the original one-hour window assumed.
    /// </summary>
    public int StaleToleranceHours { get; set; } = 12;
    public bool ShowForecast { get; set; } = true;

    /// Whether the tooltip carries the time until the shown window resets. On by
    /// default, matching the menu bar app; a settings file written before this
    /// existed has no key, so it lands here too.
    public bool ShowResetCountdown { get; set; } = true;
    public bool DepletionAlertEnabled { get; set; }

    /// Whether to ask GitHub once a day if a newer release is out. On by default;
    /// it is the only request the app makes that is not to a usage provider, so
    /// it can be switched off. A settings file written before this existed has no
    /// key, so it lands on the default.
    public bool CheckForUpdates { get; set; } = true;

    /// Whether an expired Claude token is renewed by starting Claude Code for a
    /// moment, instead of waiting for the user to run it. On by default: it does
    /// what the user would otherwise have to do by hand, through the CLI's own
    /// sign-in. A settings file written before this existed lands on the default.
    public bool RenewClaudeTokenWithCli { get; set; } = true;

    /// Whether to fetch the Anthropic organization's month of cost with its Admin
    /// key. Off by default: it needs a key the user has to create and store first.
    public bool ClaudeCostEnabled { get; set; }

    /// The same for the OpenAI organization behind Codex.
    public bool CodexCostEnabled { get; set; }

    /// The Anthropic organization's monthly budget in whole US dollars. Zero is off.
    public int ClaudeMonthlyBudget { get; set; }

    /// The same for the OpenAI organization behind Codex.
    public int CodexMonthlyBudget { get; set; }

    /// The money alerts sent this local month (CostAlertEvaluator), kept across
    /// relaunches: each says something once a month, and the cost cache would
    /// otherwise bring the same reading back at every launch.
    public List<string> DeliveredCostAlerts { get; set; } = new();

    /// When updates are paused, the instant polling resumes (persisted, so a
    /// restart during a pause stays paused). <c>null</c> means not paused.
    public DateTimeOffset? PollPausedUntil { get; set; }
}

public sealed class SettingsStore
{
    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        Converters = { new JsonStringEnumConverter() }
    };

    /// Called with the attempt number before a failed save is tried again. Lets the
    /// smoke tests free a held file at that moment instead of racing a timer.
    private readonly Action<int>? beforeRetry;

    public string Path { get; }

    public SettingsStore(string? path = null, Action<int>? beforeRetry = null)
    {
        Path = path ?? AppPaths.SettingsPath;
        this.beforeRetry = beforeRetry;
    }

    public AppSettings Load()
    {
        if (!File.Exists(Path))
        {
            return new AppSettings();
        }

        try
        {
            var settings = JsonSerializer.Deserialize<AppSettings>(File.ReadAllText(Path), Options) ?? new AppSettings();
            // A saved interval below the current 60s floor (for example the removed
            // 30s option) is no longer selectable and must never poll faster than
            // the floor, so raise it on load. Values at or above 60s are untouched.
            if (settings.RefreshIntervalSeconds < 60)
            {
                settings.RefreshIntervalSeconds = 60;
            }

            return settings;
        }
        catch
        {
            return new AppSettings();
        }
    }

    private const int SaveAttempts = 3;
    private static readonly TimeSpan SaveRetryDelay = TimeSpan.FromMilliseconds(100);

    /// <summary>
    /// Writes the settings and says whether that worked. Saves come from menu
    /// clicks, the settings window and a poll's cost alerts, and a failure used to
    /// throw straight into them: unhandled, so WinForms put up its error dialog.
    /// The usual cause passes in a moment — antivirus, the search indexer or a
    /// backup tool holding the file without delete sharing — so a failed write is
    /// tried again briefly before the caller is told, and the caller keeps the
    /// settings in memory meanwhile.
    /// </summary>
    public bool Save(AppSettings settings)
    {
        for (var attempt = 1; ; attempt++)
        {
            try
            {
                Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path)!);
                // Atomic write (temp + move) so a crash mid-save can't truncate the
                // settings file — matches UsageHistoryStore.Save.
                var tempPath = Path + ".tmp";
                File.WriteAllText(tempPath, JsonSerializer.Serialize(settings, Options));
                File.Move(tempPath, Path, overwrite: true);
                return true;
            }
            catch (Exception ex) when ((ex is IOException or UnauthorizedAccessException) && attempt < SaveAttempts)
            {
                beforeRetry?.Invoke(attempt);
                Thread.Sleep(SaveRetryDelay);
            }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException)
            {
                return false;
            }
        }
    }
}
