using System.Drawing;
using System.Diagnostics;
using System.Reflection;
using System.Windows.Forms;
using Microsoft.Win32;
using TokenTracker.Windows.Core;

namespace TokenTracker.Windows;

internal sealed class TrayAppContext : ApplicationContext
{
    private const int MaxMenuTextLength = 58;
    private const int MaxMenuWidth = 420;
    private static readonly System.Net.Http.HttpClient UpdateHttpClient = new() { Timeout = TimeSpan.FromSeconds(10) };

    private readonly NotifyIcon notifyIcon = new();
    private readonly System.Windows.Forms.Timer timer = new();
    private readonly System.Windows.Forms.Timer countdownTimer = new();
    private readonly System.Windows.Forms.Timer wakeTimer = new();
    private readonly SynchronizationContext uiContext;
    private readonly SettingsStore settingsStore = new();
    private readonly CacheStore cacheStore = new();
    private readonly UsageHistoryStore historyStore = new();
    private readonly UsageClient usageClient = new(claudeCliRefresher: new ClaudeCliRefresher());
    private readonly ProviderLogoStore providerLogos = new();
    private readonly HashSet<string> deliveredAlertIds = new(StringComparer.Ordinal);
    private readonly AdminCostService costService = new();
    private IReadOnlyDictionary<Provider, CostState> costStates = new Dictionary<Provider, CostState>();
    private bool refreshingCosts;

    /// The last settings save failed and the user has been told; cleared by a good one.
    private bool settingsSaveFailed;

    /// A settings change asked for a fetch while one was running; run once more after it.
    private bool refreshCostsAgain;

    /// Each provider's last reading with numbers, for telling when a low window has
    /// refilled. Kept current while notifications are off, so turning them back on
    /// does not compare against a reading from hours ago.
    private Dictionary<Provider, ProviderUsage> lastReadings = new();
    private AppSettings settings;
    private UsageSnapshot? snapshot;
    private DateTimeOffset? lastSuccessfulRefreshAt;
    private SettingsForm? settingsForm;
    private Icon? currentIcon;
    private bool refreshing;
    private bool checkingForUpdates;
    private DateTimeOffset? nextUpdateCheckAt;
    private string? availableUpdateVersion;
    private Localizer Localizer => new(settings.Language);

    public TrayAppContext()
    {
        settings = settingsStore.Load();
        settings.LaunchAtLogin = StartupManager.IsEnabled();

        notifyIcon.Visible = true;
        notifyIcon.Text = "Tokki";
        ReplaceMenu();
        // Built when a poll lands but opened any time up to a whole refresh interval
        // later, the menu read "updated 0s ago" and its reset countdowns as of the
        // poll. The right-button press arrives just before the release that opens
        // it, so build it again there. Not on Opening: the menu has been placed by
        // then, for the size of the items it would throw away.
        notifyIcon.MouseDown += (_, e) =>
        {
            if (e.Button == MouseButtons.Right)
            {
                ReplaceMenu();
            }
        };
        SetIcon(null);

        timer.Interval = Math.Max(60, settings.RefreshIntervalSeconds) * 1000;
        timer.Tick += async (_, _) => await RefreshAsync();
        timer.Start();

        // The usage poll runs as slowly as every 5 minutes, but the tooltip's
        // countdown has minute resolution, so it has to re-render from the cached
        // snapshot on its own or it reads minutes stale. Purely local: no fetch,
        // so this keeps ticking while polling is paused.
        countdownTimer.Interval = 30 * 1000;
        countdownTimer.Tick += (_, _) => UpdateTooltip();
        SyncCountdownTimer();

        // After sleep the tray keeps showing what was measured before it until the
        // next timer tick, up to a whole refresh interval later. Refresh on resume
        // instead, once the network has had a few seconds to come back, and restart
        // the poll timer so the next poll is a full interval after this one.
        wakeTimer.Interval = 10 * 1000;
        wakeTimer.Tick += async (_, _) =>
        {
            wakeTimer.Stop();
            timer.Stop();
            timer.Start();
            await RefreshAsync();
        };
        // Building the menu above created a control, which installed the WinForms
        // context for this thread.
        uiContext = SynchronizationContext.Current ?? new WindowsFormsSynchronizationContext();
        SystemEvents.PowerModeChanged += OnPowerModeChanged;

        // Start from the snapshot cached before the last exit, so a window that ran
        // low and reset while the app was not running is still reported.
        var cached = cacheStore.Load(TimeSpan.FromHours(settings.StaleToleranceHours));
        if (cached is not null)
        {
            lastReadings = UsageAlertEvaluator.LastReadings(lastReadings, cached);
        }

        _ = RefreshAsync();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            SystemEvents.PowerModeChanged -= OnPowerModeChanged;
            timer.Dispose();
            countdownTimer.Dispose();
            wakeTimer.Dispose();
            notifyIcon.Visible = false;
            notifyIcon.ContextMenuStrip?.Dispose();
            notifyIcon.Dispose();
            providerLogos.Dispose();
            currentIcon?.Dispose();
        }

        base.Dispose(disposing);
    }

    private async Task RefreshAsync()
    {
        if (refreshing)
        {
            return;
        }

        if (PauseController.IsPaused(settings.PollPausedUntil))
        {
            // Skip the network fetch while paused; keep the menu's countdown fresh.
            ReplaceMenu();
            return;
        }

        refreshing = true;
        notifyIcon.Text = TrimTooltip("Tokki: refreshing...");

        try
        {
            var claudeTask = settings.ClaudeEnabled
                ? usageClient.FetchClaudeAsync(settings.RenewClaudeTokenWithCli)
                : Task.FromResult(ProviderUsage.Unavailable(Provider.Claude, "Disabled"));
            var codexTask = settings.CodexEnabled
                ? usageClient.FetchCodexAsync()
                : Task.FromResult(ProviderUsage.Unavailable(Provider.Codex, "Disabled"));

            await Task.WhenAll(claudeTask, codexTask);
            var freshSnapshot = new UsageSnapshot(claudeTask.Result, codexTask.Result, DateTimeOffset.Now);
            var carried = UsageSnapshotCachePolicy.Apply(
                freshSnapshot,
                cacheStore.Load(TimeSpan.FromHours(settings.StaleToleranceHours)),
                settings.ClaudeEnabled,
                settings.CodexEnabled,
                maxAge: TimeSpan.FromHours(settings.StaleToleranceHours));
            // The cache keeps carried readings whole; only what is shown drops the
            // number of a window that has reset since (see WithoutLapsedWindows).
            cacheStore.Save(carried);
            snapshot = UsageSnapshotCachePolicy.WithoutLapsedWindows(carried, carried.UpdatedAt);
            // The newest real measurement in this snapshot, by its own timestamp.
            // A status-line reading counts — it is account data — but it was taken
            // before this poll, so reporting the poll time would overstate it.
            var measurements = new[] { snapshot.Claude, snapshot.Codex }
                .Where(usage => usage.Source == UsageSource.Api || usage.Source == UsageSource.StatusLine)
                .Select(usage => usage.UpdatedAt)
                .ToList();
            if (measurements.Count > 0)
            {
                lastSuccessfulRefreshAt = measurements.Max();
            }

            historyStore.Append(UsageHistoryPolicy.MeasurementsOnly(snapshot), settings.HistoryRetentionDays);
            HandleNotifications(snapshot);
            NotifyCostAlerts();
            SetIcon(snapshot);
            UpdateTooltip();
        }
        catch (Exception ex)
        {
            notifyIcon.Text = TrimTooltip($"Tokki: {ex.Message}");
        }
        finally
        {
            refreshing = false;
            ReplaceMenu();
        }

        await CheckForUpdatesIfDueAsync();
        await RefreshCostsIfDueAsync();
    }

    /// Rides the refresh timer, and runs at once when the API cost settings or a key
    /// change; CostFetchPolicy keeps each provider to its own slower schedule, and
    /// a pause holds it like the usage poll.
    private async Task RefreshCostsIfDueAsync()
    {
        if (PauseController.IsPaused(settings.PollPausedUntil))
        {
            return;
        }

        // The usage refresh lets go of its own guard before this runs, so a manual
        // refresh could start a second pass while the first is still fetching.
        if (refreshingCosts)
        {
            refreshCostsAgain = true;
            return;
        }

        refreshingCosts = true;
        try
        {
            var enabled = new Dictionary<Provider, bool>
            {
                [Provider.Claude] = settings.ClaudeCostEnabled,
                [Provider.Codex] = settings.CodexCostEnabled
            };
            // TimeZoneInfo.Local is cached for the life of the process; the day and
            // month boundaries have to follow the PC to a new zone.
            TimeZoneInfo.ClearCachedData();
            costStates = await costService.RefreshIfDueAsync(enabled, DateTimeOffset.Now, TimeZoneInfo.Local);
            NotifyCostAlerts();
            SetIcon(snapshot);
            UpdateTooltip();
            ReplaceMenu();
        }
        finally
        {
            refreshingCosts = false;
        }

        if (refreshCostsAgain)
        {
            refreshCostsAgain = false;
            await RefreshCostsIfDueAsync();
        }
    }

    /// Rides the refresh timer; UpdateCheck.IsDue keeps it to once a day, or once an
    /// hour after a failed attempt. Turning the setting off drops a version already
    /// found, so the menu item goes with it.
    private async Task CheckForUpdatesIfDueAsync()
    {
        if (!settings.CheckForUpdates)
        {
            availableUpdateVersion = null;
            return;
        }

        var now = DateTimeOffset.Now;
        var current = UpdateCheck.CurrentVersion(Assembly.GetExecutingAssembly().GetName().Version);
        if (checkingForUpdates || current is null || !UpdateCheck.IsDue(nextUpdateCheckAt, now))
        {
            return;
        }

        checkingForUpdates = true;
        try
        {
            var latest = await UpdateCheck.FetchLatestVersionAsync(UpdateHttpClient);
            nextUpdateCheckAt = UpdateCheck.NextCheck(now, latest is not null);
            // A failed check keeps whatever the last good one found.
            if (latest is null || !settings.CheckForUpdates)
            {
                return;
            }

            availableUpdateVersion = UpdateCheck.IsNewer(latest, current) ? latest : null;
            ReplaceMenu();
        }
        finally
        {
            checkingForUpdates = false;
        }
    }

    private void OnPowerModeChanged(object? sender, PowerModeChangedEventArgs e)
    {
        if (e.Mode != PowerModes.Resume)
        {
            return;
        }

        // SystemEvents may raise this off the UI thread; the timer belongs to it.
        uiContext.Post(_ =>
        {
            wakeTimer.Stop();
            wakeTimer.Start();
        }, null);
    }

    private void SetIcon(UsageSnapshot? usage)
    {
        var costs = TrayCosts();
        var nextIcon = TrayIconRenderer.Render(
            usage,
            settings.DisplayMode,
            CostMenuBar.TrayShowsDollar(SubscriptionShown(), costs),
            costs);
        notifyIcon.Icon = nextIcon;
        currentIcon?.Dispose();
        currentIcon = nextIcon;
    }

    private ContextMenuStrip BuildMenu()
    {
        var menu = new ContextMenuStrip();
        ConfigureCompactMenu(menu);

        if (availableUpdateVersion is not null)
        {
            // Opens the release page rather than installing: the app is not signed
            // for an automatic update, so the download and unzip stay manual.
            var updateText = Localizer.Text(L10nKey.UpdateAvailable).Replace("{version}", "v" + availableUpdateVersion, StringComparison.Ordinal)
                + " — " + Localizer.Text(L10nKey.DownloadUpdate);
            menu.Items.Add(updateText, null, (_, _) => OpenReleasePage());
            menu.Items.Add(new ToolStripSeparator());
        }

        if (snapshot is null)
        {
            AddDisabled(menu.Items, Localizer.Text(L10nKey.NoUsageLoaded));
        }
        else
        {
            // Read the credential store once per rebuild, not once per provider row.
            var claudeCredentialSource = ClaudeCredentialSource.Detect(AppPaths.ClaudeCredentialsPath());
            AddProvider(menu, snapshot.Claude, claudeCredentialSource, AdminCost(Provider.Claude));
            AddProvider(menu, snapshot.Codex, claudeCredentialSource, AdminCost(Provider.Codex));
            menu.Items.Add(new ToolStripSeparator());
            AddDisabled(menu.Items, $"{Localizer.Text(L10nKey.Updated)} {Relative(snapshot.UpdatedAt)} {Localizer.Text(L10nKey.Ago)}");
            AddDisabled(
                menu.Items,
                lastSuccessfulRefreshAt is null
                    ? Localizer.Text(L10nKey.NoSuccessfulUpdate)
                    : $"{Localizer.Text(L10nKey.LastSuccessfulUpdate)}: {Relative(lastSuccessfulRefreshAt.Value)} {Localizer.Text(L10nKey.Ago)}");
        }

        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(Localizer.Text(L10nKey.RefreshNow), null, async (_, _) =>
        {
            // A manual refresh is an explicit request to fetch now, so it also
            // lifts an active pause.
            if (PauseController.IsPaused(settings.PollPausedUntil))
            {
                settings.PollPausedUntil = null;
                SaveSettings();
            }

            await RefreshAsync();
        });
        menu.Items.Add(Localizer.Text(L10nKey.Preferences), null, (_, _) => ShowSettings());
        AddPauseControls(menu);
        menu.Items.Add(DiagnosticsMenu());
        menu.Items.Add(HistoryMenu());

        var launchItem = new ToolStripMenuItem(Localizer.Text(L10nKey.LaunchAtLogin))
        {
            Checked = StartupManager.IsEnabled(),
            CheckOnClick = true
        };
        launchItem.Click += (_, _) =>
        {
            StartupManager.SetEnabled(launchItem.Checked);
            settings.LaunchAtLogin = launchItem.Checked;
            SaveSettings();
        };
        menu.Items.Add(launchItem);
        menu.Items.Add(Localizer.Text(L10nKey.AlwaysShowIconSettings), null, (_, _) => OpenTaskbarIconSettings());

        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(Localizer.Text(L10nKey.Quit), null, (_, _) => ExitThread());

        return menu;
    }

    /// Replaces the menu without leaving every previous ToolStrip and its native
    /// window handles alive for the rest of the process. A menu can request a
    /// rebuild from one of its own click handlers, so defer disposal until it has
    /// closed in that case.
    private void ReplaceMenu()
    {
        var previous = notifyIcon.ContextMenuStrip;
        notifyIcon.ContextMenuStrip = BuildMenu();
        if (previous is null)
        {
            return;
        }

        if (previous.Visible)
        {
            previous.Closed += (_, _) => previous.Dispose();
        }
        else
        {
            previous.Dispose();
        }
    }

    private void AddProvider(
        ContextMenuStrip menu,
        ProviderUsage usage,
        ClaudeCredentialSource claudeCredentialSource,
        AdminCostView? cost)
    {
        var issue = UsageIssueFormatter.Issue(usage, Localizer);
        // Subscription limits off and API cost on: the submenu is the cost alone.
        var costOnly = issue.Kind == UsageIssueKind.Disabled && cost is not null;
        var header = costOnly ? CostHeaderText(usage, cost!) : ProviderHeaderText(usage);
        var provider = new ToolStripMenuItem(CompactMenuText(header), ProviderHeaderImage(usage))
        {
            ToolTipText = header
        };
        ConfigureCompactMenu(provider.DropDown);
        if (provider.Image is not null)
        {
            provider.ImageScaling = ToolStripItemImageScaling.None;
        }

        if (costOnly)
        {
            AddCostLines(provider.DropDownItems, usage.Provider, cost!, costOnly: true);
            menu.Items.Add(provider);
            return;
        }

        AddDisabled(provider.DropDownItems, $"{Localizer.Text(L10nKey.Status)}: {issue.Title}");
        AddDisabled(provider.DropDownItems, $"  {issue.Detail}");
        if (!string.IsNullOrWhiteSpace(issue.Recovery))
        {
            AddDisabled(provider.DropDownItems, $"  {Localizer.Text(L10nKey.Recovery)}: {issue.Recovery}");
        }

        // Why signing in again did not stick: nothing has written the store since long
        // before this token could have lapsed.
        var staleCredentials = CredentialStoreAdvice.StaleLine(
            usage.Provider,
            issue,
            claudeCredentialSource,
            DateTimeOffset.Now,
            Localizer);
        if (staleCredentials is not null)
        {
            AddDisabled(provider.DropDownItems, $"  {staleCredentials}");
        }

        if (settings.ShowForecast)
        {
            var window = DisplayFormatter.PreferredForecastWindow(usage);
            var resetAt = window == ForecastWindow.FiveHour ? usage.ResetAt5h : usage.ResetAt7d;
            var forecast = UsageForecaster.Forecast(historyStore.Load(), usage.Provider, window, resetAt);
            var forecastLine = UsageForecastText.MenuLine(forecast, window, Localizer);
            if (forecastLine is not null)
            {
                AddDisabled(provider.DropDownItems, $"  {forecastLine}");
            }

            var paceLine = UsagePaceText.MenuLine(usage, Localizer);
            if (paceLine is not null)
            {
                AddDisabled(provider.DropDownItems, $"  {paceLine}");
            }
        }

        var fiveHourReset = DisplayFormatter.FormatWindowReset(usage.RemainingPercent5h, usage.ResetAt5h, Localizer, usage.IsAvailable);
        var sevenDayReset = DisplayFormatter.FormatWindowReset(usage.RemainingPercent7d, usage.ResetAt7d, Localizer, usage.IsAvailable);
        AddDisabled(provider.DropDownItems, $"{Localizer.Text(L10nKey.FiveHourReset)}: {fiveHourReset}");
        AddDisabled(provider.DropDownItems, $"{Localizer.Text(L10nKey.SevenDayReset)}: {sevenDayReset}");
        if (SubscriptionCostText.Line(usage, Localizer) is { } subscriptionCost)
        {
            AddDisabled(provider.DropDownItems, $"{subscriptionCost.Label}: {subscriptionCost.Value}");
        }

        AddDisabled(provider.DropDownItems, $"{Localizer.Text(L10nKey.Source)}: {usage.Source}");

        if (!string.IsNullOrWhiteSpace(issue.TechnicalDetail))
        {
            AddDisabled(provider.DropDownItems, $"{Localizer.Text(L10nKey.TechnicalError)}: {issue.TechnicalDetail}");
        }

        if (!string.IsNullOrWhiteSpace(usage.Plan))
        {
            AddDisabled(provider.DropDownItems, $"{Localizer.Text(L10nKey.Plan)}: {usage.Plan}");
        }

        // Last, after everything about the subscription, as on the macOS card.
        if (cost is not null)
        {
            AddCostLines(provider.DropDownItems, usage.Provider, cost, costOnly: false);
        }

        menu.Items.Add(provider);
    }

    /// The organization's month of API cost, set apart under its own heading: the
    /// organization's bill is a different thing from the subscription above it. On
    /// a submenu of its own the status leads; under the limits it follows the
    /// amount, and only when not fine.
    private void AddCostLines(ToolStripItemCollection items, Provider provider, AdminCostView cost, bool costOnly)
    {
        var status = $"{Localizer.Text(L10nKey.Status)}: {cost.StatusTitle}";
        if (costOnly)
        {
            AddDisabled(items, status);
        }
        else
        {
            items.Add(new ToolStripSeparator());
        }

        var title = Localizer.Text(L10nKey.CostSectionTitle)
            .Replace("{organization}", AdminKeyText.Organization(provider), StringComparison.Ordinal);
        AddDisabled(items, cost.AsOf is null ? title : title + " · " + cost.AsOf);

        AddDisabled(items, $"  {cost.MonthLabel}: {cost.MonthValue}");
        foreach (var line in new[] { cost.BudgetText, cost.Detail })
        {
            if (line is not null)
            {
                AddDisabled(items, $"  {line}");
            }
        }

        if (!costOnly && cost.Status != AdminCostStatus.Ok)
        {
            AddDisabled(items, $"  {status}");
        }

        if (cost.FailureMessage is not null)
        {
            AddDisabled(items, $"  {Localizer.Text(L10nKey.TechnicalError)}: {cost.FailureMessage}");
        }
    }

    private string CostHeaderText(ProviderUsage usage, AdminCostView cost)
    {
        var amount = $"{cost.MonthValue} ({cost.MonthLabel})";
        return settings.ProviderLabelStyle == ProviderLabelStyle.Icon ? amount : $"{usage.DisplayName}: {amount}";
    }

    /// The submenu's API cost per provider; absent where API cost is off or keyless.
    private AdminCostView? AdminCost(Provider provider)
    {
        var state = costStates.TryGetValue(provider, out var found) ? found : new CostState();
        var budget = provider == Provider.Claude ? settings.ClaudeMonthlyBudget : settings.CodexMonthlyBudget;
        return AdminCostText.View(state, budget, DateTimeOffset.Now, TimeZoneInfo.Local, Localizer);
    }

    private IReadOnlyDictionary<Provider, MenuBarCost> TrayCosts() =>
        CostMenuBar.Entries(SubscriptionShown(), costStates, new Dictionary<Provider, int>
        {
            [Provider.Claude] = settings.ClaudeMonthlyBudget,
            [Provider.Codex] = settings.CodexMonthlyBudget
        });

    private IReadOnlyDictionary<Provider, bool> SubscriptionShown() => new Dictionary<Provider, bool>
    {
        [Provider.Claude] = settings.ClaudeEnabled,
        [Provider.Codex] = settings.CodexEnabled
    };

    private string ProviderHeaderText(ProviderUsage usage)
    {
        if (settings.ProviderLabelStyle != ProviderLabelStyle.Icon)
        {
            return DisplayFormatter.DetailLine(usage);
        }

        return $"5h {DisplayFormatter.FormatPercent(usage.RemainingPercent5h)}, 7d {DisplayFormatter.FormatPercent(usage.RemainingPercent7d)}";
    }

    private Image? ProviderHeaderImage(ProviderUsage usage) =>
        settings.ProviderLabelStyle == ProviderLabelStyle.Icon
            ? providerLogos.MenuLogo(usage.Provider)
            : null;

    private void AddPauseControls(ContextMenuStrip menu)
    {
        if (PauseController.IsPaused(settings.PollPausedUntil))
        {
            var remainingText = PauseController.IsIndefinite(settings.PollPausedUntil)
                ? Localizer.Text(L10nKey.PauseUntilResumed)
                : UsageForecaster.DurationText(PauseController.Remaining(settings.PollPausedUntil).TotalSeconds);
            AddDisabled(menu.Items, $"{Localizer.Text(L10nKey.UpdatesPaused)}: {remainingText}");
            menu.Items.Add(Localizer.Text(L10nKey.ResumeNow), null, async (_, _) =>
            {
                settings.PollPausedUntil = null;
                SaveSettings();
                await RefreshAsync();
            });
        }

        var pauseRoot = new ToolStripMenuItem(Localizer.Text(L10nKey.PausePolling));
        pauseRoot.DropDownItems.Add(Localizer.Text(L10nKey.Pause1h), null, (_, _) => PausePolling(TimeSpan.FromHours(1)));
        pauseRoot.DropDownItems.Add(Localizer.Text(L10nKey.Pause3h), null, (_, _) => PausePolling(TimeSpan.FromHours(3)));
        pauseRoot.DropDownItems.Add(Localizer.Text(L10nKey.PauseUntilResumed), null, (_, _) => PausePolling(null));
        menu.Items.Add(pauseRoot);
    }

    private void PausePolling(TimeSpan? duration)
    {
        settings.PollPausedUntil = duration is null
            ? DateTimeOffset.MaxValue
            : DateTimeOffset.Now + duration.Value;
        SaveSettings();
        ReplaceMenu();
    }

    private ToolStripMenuItem DiagnosticsMenu()
    {
        var root = new ToolStripMenuItem(Localizer.Text(L10nKey.Diagnostics));
        root.DropDownItems.Add(Localizer.Text(L10nKey.CopyDiagnostics), null, (_, _) =>
        {
            Clipboard.SetText(Diagnostics().DiagnosticsText());
            notifyIcon.ShowBalloonTip(3000, "Tokki", Localizer.Text(L10nKey.DiagnosticsCopied), ToolTipIcon.Info);
        });
        root.DropDownItems.Add(new ToolStripSeparator());
        root.DropDownItems.Add(Localizer.Text(L10nKey.OpenClaudeCredentials), null, (_, _) => RevealInExplorer(DiagnosticsReporter.ClaudeCredentialsPath));
        root.DropDownItems.Add(Localizer.Text(L10nKey.OpenCodexAuth), null, (_, _) => RevealInExplorer(DiagnosticsReporter.CodexAuthPath));
        root.DropDownItems.Add(new ToolStripSeparator());
        AddDisabled(root.DropDownItems, $"{Localizer.Text(L10nKey.DuplicateInstances)}: {RunningInstanceCount()}");
        if (settings.RefreshIntervalSeconds < 300)
        {
            AddDisabled(root.DropDownItems, Localizer.Text(L10nKey.RefreshIntervalWarning));
        }

        return root;
    }

    private ToolStripMenuItem HistoryMenu()
    {
        var root = new ToolStripMenuItem(Localizer.Text(L10nKey.History));
        AddDisabled(root.DropDownItems, Diagnostics().HistoryTrendText());
        var historyEntries = historyStore.Load();
        foreach (var provider in new[] { Provider.Claude, Provider.Codex })
        {
            var window = snapshot is null
                ? ForecastWindow.FiveHour
                : DisplayFormatter.PreferredForecastWindow(provider == Provider.Claude ? snapshot.Claude : snapshot.Codex);
            var sparkline = SparklineText.Render(SparklineSeries.Build(historyEntries, provider, window));
            if (!string.IsNullOrEmpty(sparkline))
            {
                var name = provider == Provider.Claude ? "Claude" : "Codex";
                AddDisabled(root.DropDownItems, $"{name} {window.ShortLabel()} {sparkline}");
            }
        }

        AddDisabled(root.DropDownItems, $"{Localizer.Text(L10nKey.HistoryRetentionDays)}: {settings.HistoryRetentionDays}d");
        root.DropDownItems.Add(new ToolStripSeparator());
        root.DropDownItems.Add(Localizer.Text(L10nKey.ExportHistoryCsv), null, (_, _) => ExportHistoryCsv());
        return root;
    }

    private DiagnosticsReporter Diagnostics() =>
        new(settings, historyStore, snapshot, lastSuccessfulRefreshAt, RunningInstanceCount(), usageClient.ClaudeCliRefreshLastAttempt, costStates);

    private void ShowSettings()
    {
        if (settingsForm is null || settingsForm.IsDisposed)
        {
            settingsForm = new SettingsForm(
                settings,
                async () =>
                {
                    SaveSettings();
                    ReplaceMenu();
                    await RefreshAsync();
                },
                () =>
                {
                    SaveSettings();
                    SetIcon(snapshot);
                    UpdateTooltip();
                    SyncCountdownTimer();
                    _ = CheckForUpdatesIfDueAsync();
                    ReplaceMenu();
                },
                () =>
                {
                    SaveSettings();
                    ReplaceMenu();
                },
                async () =>
                {
                    SaveSettings();
                    SetIcon(snapshot);
                    UpdateTooltip();
                    ReplaceMenu();
                    await RefreshCostsIfDueAsync();
                });
            settingsForm.FormClosed += (_, _) => settingsForm = null;
        }

        settingsForm.Show();
        settingsForm.Activate();
    }

    private void ExportHistoryCsv()
    {
        using var dialog = new SaveFileDialog
        {
            FileName = "tokki-history.csv",
            Filter = "CSV files (*.csv)|*.csv|All files (*.*)|*.*",
            DefaultExt = "csv",
            AddExtension = true
        };

        if (dialog.ShowDialog() != DialogResult.OK)
        {
            return;
        }

        try
        {
            File.WriteAllText(dialog.FileName, historyStore.CsvString());
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Tokki", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    private void HandleNotifications(UsageSnapshot usage)
    {
        var previousReadings = lastReadings;
        lastReadings = UsageAlertEvaluator.LastReadings(previousReadings, usage);

        if (!settings.NotificationsEnabled)
        {
            deliveredAlertIds.Clear();
            return;
        }

        var alertSettings = new UsageAlertSettings(
            settings.NotificationsEnabled,
            settings.FiveHourAlertThreshold,
            settings.SevenDayAlertThreshold,
            settings.ResetAlertMinutes,
            UsageAlertEvaluator.StalledThreshold(settings.RefreshIntervalSeconds));
        var candidates = UsageAlertEvaluator.Candidates(usage, alertSettings, localizer: Localizer).ToList();
        candidates.AddRange(UsageAlertEvaluator.FreedUpCandidates(previousReadings, usage, alertSettings, Localizer));
        candidates.AddRange(ForecastCandidates(usage));
        var activeIds = candidates.Select(candidate => candidate.Id).ToHashSet(StringComparer.Ordinal);
        deliveredAlertIds.IntersectWith(activeIds);

        foreach (var candidate in candidates.Where(candidate => deliveredAlertIds.Add(candidate.Id)))
        {
            notifyIcon.ShowBalloonTip(5000, candidate.Title, candidate.Body, ToolTipIcon.Warning);
        }
    }

    /// Budget and stage-1 money alerts, each once per local month. Runs after both
    /// the usage poll (extra usage, credits) and the cost fetch (budgets); the
    /// record of what went out lives in settings, so a relaunch does not repeat it.
    private void NotifyCostAlerts()
    {
        var result = CostAlertEvaluator.Evaluate(
            snapshot,
            costStates,
            new Dictionary<Provider, int>
            {
                [Provider.Claude] = settings.ClaudeMonthlyBudget,
                [Provider.Codex] = settings.CodexMonthlyBudget
            },
            settings.DeliveredCostAlerts,
            settings.NotificationsEnabled,
            DateTimeOffset.Now,
            TimeZoneInfo.Local,
            Localizer);
        foreach (var alert in result.Alerts)
        {
            notifyIcon.ShowBalloonTip(5000, alert.Title, alert.Body, ToolTipIcon.Warning);
        }

        // Saved after the alerts, not before: a tray icon shows one balloon at a
        // time, and a save that fails says so once. Before them, that notice was
        // the balloon the alerts replaced.
        var delivered = result.Delivered.OrderBy(id => id, StringComparer.Ordinal).ToList();
        if (!delivered.SequenceEqual(settings.DeliveredCostAlerts))
        {
            settings.DeliveredCostAlerts = delivered;
            SaveSettings();
        }
    }

    private IReadOnlyList<UsageAlertCandidate> ForecastCandidates(UsageSnapshot usage)
    {
        if (!settings.DepletionAlertEnabled)
        {
            return Array.Empty<UsageAlertCandidate>();
        }

        var entries = historyStore.Load();
        var inputs = new List<ForecastAlertInput>();
        foreach (var provider in new[] { Provider.Claude, Provider.Codex })
        {
            var providerUsage = usage.Usage(provider);
            var fiveHour = UsageForecaster.Forecast(entries, provider, ForecastWindow.FiveHour, providerUsage.ResetAt5h, usage.UpdatedAt);
            if (fiveHour is not null)
            {
                inputs.Add(new ForecastAlertInput(provider, ForecastWindow.FiveHour, fiveHour, providerUsage.ResetAt5h));
            }

            var sevenDay = UsageForecaster.Forecast(entries, provider, ForecastWindow.SevenDay, providerUsage.ResetAt7d, usage.UpdatedAt);
            if (sevenDay is not null)
            {
                inputs.Add(new ForecastAlertInput(provider, ForecastWindow.SevenDay, sevenDay, providerUsage.ResetAt7d));
            }
        }

        return UsageForecastAlert.Candidates(inputs, enabled: true, Localizer);
    }

    private static void AddDisabled(ToolStripItemCollection items, string text, Image? image = null) =>
        items.Add(DisabledItem(text, image));

    private static void ConfigureCompactMenu(ToolStrip menu)
    {
        menu.ImageScalingSize = new Size(16, 16);
        menu.MaximumSize = new Size(MaxMenuWidth, 0);
        menu.ShowItemToolTips = true;
    }

    private static ToolStripMenuItem DisabledItem(string text, Image? image = null)
    {
        var displayText = CompactMenuText(text);
        var item = new ToolStripMenuItem(displayText, image)
        {
            Enabled = false
        };

        if (image is not null)
        {
            item.ImageScaling = ToolStripItemImageScaling.None;
        }

        if (!string.Equals(displayText, text, StringComparison.Ordinal))
        {
            item.AutoToolTip = false;
            item.ToolTipText = text;
        }

        return item;
    }

    private static string CompactMenuText(string text)
    {
        var singleLine = text
            .Replace("\r\n", " ")
            .Replace('\n', ' ')
            .Replace('\r', ' ');

        if (singleLine.Length <= MaxMenuTextLength)
        {
            return singleLine;
        }

        return singleLine[..(MaxMenuTextLength - 3)].TrimEnd() + "...";
    }

    private void SaveSettings()
    {
        timer.Interval = Math.Max(60, settings.RefreshIntervalSeconds) * 1000;
        if (settingsStore.Save(settings))
        {
            settingsSaveFailed = false;
            return;
        }

        // Once per run of failures: each later change tries again, and a balloon on
        // every one of them would bury the tray.
        if (!settingsSaveFailed)
        {
            settingsSaveFailed = true;
            notifyIcon.ShowBalloonTip(5000, "Tokki", Localizer.Text(L10nKey.SettingsSaveFailed), ToolTipIcon.Warning);
        }
    }

    private void OpenTaskbarIconSettings()
    {
        try
        {
            Process.Start(new ProcessStartInfo("ms-settings:taskbar")
            {
                UseShellExecute = true
            });

            notifyIcon.ShowBalloonTip(
                5000,
                "Tokki",
                Localizer.Text(L10nKey.TaskbarSettingsTip),
                ToolTipIcon.Info);
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                $"{Localizer.Text(L10nKey.TaskbarSettingsFallback)}\n\n{ex.Message}",
                "Tokki",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
        }
    }

    private static void OpenReleasePage()
    {
        try
        {
            Process.Start(new ProcessStartInfo(UpdateCheck.ReleasesPage.AbsoluteUri)
            {
                UseShellExecute = true
            });
        }
        catch (Exception ex)
        {
            MessageBox.Show(ex.Message, "Tokki", MessageBoxButtons.OK, MessageBoxIcon.Warning);
        }
    }

    private static void RevealInExplorer(string path)
    {
        var existingPath = File.Exists(path) ? path : Path.GetDirectoryName(path);
        if (string.IsNullOrWhiteSpace(existingPath))
        {
            return;
        }

        var arguments = File.Exists(path)
            ? $"/select,\"{path}\""
            : $"\"{existingPath}\"";
        Process.Start(new ProcessStartInfo("explorer.exe", arguments)
        {
            UseShellExecute = true
        });
    }

    private static int RunningInstanceCount()
    {
        using var current = Process.GetCurrentProcess();
        var processes = Process.GetProcessesByName(current.ProcessName);
        try
        {
            return processes.Length;
        }
        finally
        {
            foreach (var process in processes)
            {
                process.Dispose();
            }
        }
    }

    private static string Relative(DateTimeOffset date)
    {
        var elapsed = DateTimeOffset.Now - date;
        if (elapsed.TotalMinutes < 1)
        {
            return $"{Math.Max(0, (int)elapsed.TotalSeconds)}s";
        }

        if (elapsed.TotalHours < 1)
        {
            return $"{(int)elapsed.TotalMinutes}m";
        }

        return $"{(int)elapsed.TotalHours}h";
    }

    private void UpdateTooltip() =>
        notifyIcon.Text = TrimTooltip(
            DisplayFormatter.Tooltip(snapshot, settings.ProviderLabelStyle, settings.ShowResetCountdown, Localizer, TrayCosts()));

    /// The countdown ticks only while it is on screen, so the timer follows the
    /// setting rather than repainting a tooltip nobody asked for.
    private void SyncCountdownTimer() => countdownTimer.Enabled = settings.ShowResetCountdown;

    private static string TrimTooltip(string text) =>
        text.Length <= 63 ? text : text[..60] + "...";
}
