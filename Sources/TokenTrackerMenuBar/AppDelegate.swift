import AppKit
import Foundation
import TokenTrackerCore
import UniformTypeIdentifiers

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let statusItemRenderer = StatusItemRenderer()
    private let settings = Settings()
    private let loginItemManager = LoginItemManager()
    private let historyStore = UsageHistoryStore()
    private lazy var usageService = UsageService(settings: settings)
    private lazy var notificationCoordinator = UsageNotificationCoordinator(settings: settings)
    private var preferencesWindowController: PreferencesWindowController?
    private lazy var statusPanel = StatusPanelController(actions: statusPanelActions)
    private var snapshot: UsageSnapshot?
    private var timer: Timer?
    private var countdownTimer: Timer?
    private var refreshTask: Task<Void, Never>?
    private var lastSuccessfulRefreshAt: Date?
    private var appearanceObserver: NSObjectProtocol?
    private var wakeObserver: NSObjectProtocol?
    private var nextUpdateCheckAt: Date?
    private var updateCheckTask: Task<Void, Never>?
    private var availableUpdateVersion: String?
    private let costService = AdminCostService()
    private var costStates: [Provider: CostState] = [:]
    private var costTask: Task<Void, Never>?
    /// A settings change asked for a fetch while one was running; run once more after it.
    private var costRefreshAgain = false
    private var localizer: Localizer {
        Localizer(language: settings.language)
    }
    private var diagnosticsReporter: DiagnosticsReporter {
        DiagnosticsReporter(
            settings: settings,
            historyStore: historyStore,
            snapshot: snapshot,
            lastSuccessfulRefreshAt: lastSuccessfulRefreshAt,
            runningInstanceCount: runningInstanceCount(),
            claudeCLIRefreshAttempt: usageService.claudeCLIRefreshLastAttempt,
            costStates: costStates
        )
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        guard !terminateIfDuplicateInstance() else { return }
        NSApp.setActivationPolicy(.accessory)
        installEditMenu()
        statusItemRenderer.setPlaceholder(mode: settings.displayMode, labelStyle: settings.providerLabelStyle)
        statusItemRenderer.setClickAction(target: self, action: #selector(toggleStatusPanel(_:)))
        configurePanel()
        notificationCoordinator.seedReadings(from: usageService.cachedSnapshot())
        // Use startRefresh (not refreshNow) on launch so a persisted pause is
        // honored across restarts — only an explicit user "Refresh Now" lifts it.
        startRefresh(showLoadingIndicator: true)
        scheduleTimer()
        scheduleCountdownTimer()
        observeAppearanceChanges()
        observeWake()
        if settings.notificationsEnabled {
            notificationCoordinator.requestAuthorization()
        }
    }

    /// A menu bar app shows no menu bar of its own, but text fields still take ⌘V,
    /// ⌘C, ⌘X, ⌘A and ⌘Z from the main menu's key equivalents. Without an Edit menu
    /// none of them did anything, so an Admin key could not be pasted into its field.
    private func installEditMenu() {
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "z")
            .keyEquivalentModifierMask = [.command, .shift]
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let editItem = NSMenuItem(title: "Edit", action: nil, keyEquivalent: "")
        editItem.submenu = edit
        let main = NSMenu()
        main.addItem(editItem)
        NSApp.mainMenu = main
    }

    func applicationWillTerminate(_ notification: Notification) {
        countdownTimer?.invalidate()
        if let appearanceObserver {
            DistributedNotificationCenter.default().removeObserver(appearanceObserver)
        }
        if let wakeObserver {
            NSWorkspace.shared.notificationCenter.removeObserver(wakeObserver)
        }
    }

    private func scheduleTimer() {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: max(60, settings.refreshInterval), repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.startRefresh(showLoadingIndicator: false)
            }
        }
    }

    /// The usage poll runs as slowly as every 5 minutes, but the menu bar countdown
    /// has minute resolution, so it has to re-render from the cached snapshot on its
    /// own or it reads minutes stale. Purely local: no fetch, so this keeps ticking
    /// while polling is paused.
    private func scheduleCountdownTimer() {
        countdownTimer?.invalidate()
        countdownTimer = nil
        guard settings.showResetCountdown else { return }
        countdownTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            Task { @MainActor in
                self?.updateStatusTitle()
            }
        }
    }

    @objc private func toggleStatusPanel(_ sender: NSStatusBarButton) {
        // Rebuild on open so history-derived lines (forecast, sparklines) are current.
        if !statusPanel.isShown {
            configurePanel()
        }
        statusPanel.toggle(relativeTo: sender)
    }

    private func refreshNow() {
        // A manual refresh is an explicit request to fetch now, so it also
        // lifts an active pause.
        if PauseController.isPaused(until: settings.pollPausedUntil) {
            settings.pollPausedUntil = nil
        }
        startRefresh(showLoadingIndicator: true)
    }

    private func startRefresh(showLoadingIndicator: Bool) {
        guard refreshTask == nil else { return }
        if PauseController.isPaused(until: settings.pollPausedUntil) {
            // Skip the network fetch while paused; keep the panel's countdown fresh.
            configurePanel()
            return
        }
        if showLoadingIndicator || snapshot == nil {
            statusItemRenderer.setLoading(mode: settings.displayMode, labelStyle: settings.providerLabelStyle)
        }
        statusPanel.model.isRefreshing = true
        refreshTask = Task { @MainActor [weak self] in
            guard let self else { return }
            defer {
                refreshTask = nil
                statusPanel.model.isRefreshing = false
            }
            let result = await usageService.refresh()
            snapshot = result
            // The newest real measurement in this snapshot, by its own timestamp.
            // A status-line reading counts — it is account data — but it was taken
            // before this poll, so reporting the poll time would overstate it.
            if let measuredAt = [result.claude, result.codex]
                .filter({ $0.source == .api || $0.source == .statusLine })
                .map(\.updatedAt)
                .max() {
                lastSuccessfulRefreshAt = measuredAt
            }
            historyStore.append(
                UsageHistoryPolicy.measurementsOnly(result),
                retentionDays: settings.historyRetentionDays
            )
            notificationCoordinator.handleNotifications(
                for: result,
                extraCandidates: forecastAlertCandidates(for: result),
                localizer: localizer
            )
            notifyCostAlerts()
            updateStatusTitle()
            configurePanel()
            checkForUpdatesIfDue()
            refreshCostsIfDue()
        }
    }

    /// Rides the refresh timer, and runs at once when the API cost settings or a
    /// key change; `CostFetchPolicy` keeps each provider to its own slower
    /// schedule, and a pause holds it like the usage poll.
    private func refreshCostsIfDue() {
        guard !PauseController.isPaused(until: settings.pollPausedUntil) else { return }
        guard costTask == nil else {
            costRefreshAgain = true
            return
        }
        let enabled: [Provider: Bool] = [.claude: settings.claudeCostEnabled, .codex: settings.codexCostEnabled]
        costTask = Task { @MainActor [weak self] in
            guard let self else { return }
            costStates = await costService.refreshIfDue(enabled: enabled)
            costTask = nil
            notifyCostAlerts()
            updateStatusTitle()
            configurePanel()
            if costRefreshAgain {
                costRefreshAgain = false
                refreshCostsIfDue()
            }
        }
    }

    /// Budget and stage-1 money alerts, each once per local month. Runs after both
    /// the usage poll (extra usage, credits) and the cost fetch (budgets); the
    /// record of what went out lives in settings, so a relaunch does not repeat it.
    private func notifyCostAlerts() {
        let result = CostAlertEvaluator.evaluate(
            snapshot: snapshot,
            costs: costStates,
            budgets: [.claude: settings.claudeMonthlyBudget, .codex: settings.codexMonthlyBudget],
            delivered: Set(settings.deliveredCostAlerts),
            notificationsEnabled: settings.notificationsEnabled,
            now: Date(),
            timeZone: .autoupdatingCurrent,
            localizer: localizer
        )
        let delivered = result.delivered.sorted()
        if delivered != settings.deliveredCostAlerts {
            settings.deliveredCostAlerts = delivered
        }
        notificationCoordinator.deliver(result.alerts)
    }

    /// The card's API cost section per provider, `nil` where it is off or keyless.
    private func adminCostViews() -> [Provider: AdminCostView] {
        var views: [Provider: AdminCostView] = [:]
        for provider in Provider.allCases {
            views[provider] = AdminCostText.view(
                state: costStates[provider] ?? CostState(),
                budget: monthlyBudget(provider),
                now: Date(),
                timeZone: .autoupdatingCurrent,
                localizer: localizer
            )
        }
        return views
    }

    private func menuBarCosts() -> [Provider: MenuBarCost] {
        CostMenuBar.entries(
            subscriptionShown: [.claude: settings.claudeEnabled, .codex: settings.codexEnabled],
            states: costStates,
            budgets: [.claude: settings.claudeMonthlyBudget, .codex: settings.codexMonthlyBudget]
        )
    }

    private func monthlyBudget(_ provider: Provider) -> Int {
        provider == .claude ? settings.claudeMonthlyBudget : settings.codexMonthlyBudget
    }

    /// Rides the refresh timer; `UpdateCheck.isDue` keeps it to once a day, or once
    /// an hour after a failed attempt. Turning the setting off drops a version
    /// already found, so the banner goes with it.
    private func checkForUpdatesIfDue() {
        guard settings.checkForUpdates else {
            availableUpdateVersion = nil
            return
        }
        let now = Date()
        guard updateCheckTask == nil,
              UpdateCheck.isDue(nextCheckAt: nextUpdateCheckAt, now: now),
              let current = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String else {
            return
        }
        updateCheckTask = Task { @MainActor [weak self] in
            let latest = await UpdateCheck.fetchLatestVersion()
            guard let self else { return }
            updateCheckTask = nil
            nextUpdateCheckAt = UpdateCheck.nextCheck(after: now, succeeded: latest != nil)
            // A failed check keeps whatever the last good one found.
            guard let latest, settings.checkForUpdates else { return }
            availableUpdateVersion = UpdateCheck.isNewer(latest, than: current) ? latest : nil
            configurePanel()
        }
    }

    private func updateStatusTitle() {
        statusItemRenderer.update(
            snapshot: snapshot,
            mode: settings.displayMode,
            labelStyle: settings.providerLabelStyle,
            showResetCountdown: settings.showResetCountdown,
            localizer: localizer,
            costs: menuBarCosts()
        )
    }

    private func observeAppearanceChanges() {
        appearanceObserver = DistributedNotificationCenter.default().addObserver(
            forName: Notification.Name("AppleInterfaceThemeChangedNotification"),
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.updateStatusTitle()
            }
        }
    }

    /// After sleep the menu bar keeps showing what was measured before it until the
    /// next timer tick, up to a whole refresh interval later. Refresh on wake
    /// instead, once the network has had a few seconds to come back, and restart
    /// the timer so the next poll is a full interval after this one.
    private func observeWake() {
        wakeObserver = NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didWakeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 10_000_000_000)
                self?.startRefresh(showLoadingIndicator: false)
                self?.scheduleTimer()
            }
        }
    }

    private var statusPanelActions: StatusPanelActions {
        StatusPanelActions(
            refreshNow: { [weak self] in self?.refreshNow() },
            showPreferences: { [weak self] in self?.showPreferences() },
            copyDiagnostics: { [weak self] in self?.copyDiagnostics() },
            openClaudeCredentials: { [weak self] in self?.openClaudeCredentials() },
            openCodexAuth: { [weak self] in self?.openCodexAuth() },
            exportHistoryCSV: { [weak self] in self?.exportHistoryCSV() },
            toggleLaunchAtLogin: { [weak self] in self?.toggleLaunchAtLogin() },
            pause1h: { [weak self] in self?.pausePolling(for: 3600) },
            pause3h: { [weak self] in self?.pausePolling(for: 3 * 3600) },
            pauseUntilResumed: { [weak self] in self?.pausePollingUntilResumed() },
            resumePolling: { [weak self] in self?.resumePolling() },
            openReleasePage: { NSWorkspace.shared.open(UpdateCheck.releasesPage) },
            quit: { NSApp.terminate(nil) }
        )
    }

    private func configurePanel() {
        let reporter = diagnosticsReporter
        statusPanel.model.context = StatusPanelContext(
            localizer: localizer,
            settings: settings,
            snapshot: snapshot,
            lastSuccessfulRefreshAt: lastSuccessfulRefreshAt,
            forecastLines: forecastLines(),
            pausedRemainingText: pauseRemainingText(),
            sparklines: sparklines(),
            historyTrendText: reporter.historyTrendText(),
            claudeCredentialSource: ClaudeCredentialSource.detect(
                credentialsFileURL: DiagnosticsReporter.claudeCredentialsURL
            ),
            codexCredentialSource: ClaudeCredentialSource.detect(
                credentialsFileURL: DiagnosticsReporter.codexAuthURL,
                keychainModifiedAt: nil
            ),
            launchAtLoginEnabled: loginItemManager.isEnabled,
            launchAtLoginStatus: loginItemManager.statusLabel(localizer: localizer),
            runningInstanceCount: reporter.runningInstanceCount,
            availableUpdateVersion: availableUpdateVersion,
            adminCosts: adminCostViews()
        )
    }

    /// Per-provider "projected depletion" menu lines, computed from stored
    /// history (no network). Empty when the forecast display is off or there is
    /// not enough signal to project.
    private func forecastLines() -> [Provider: String] {
        guard settings.showForecast, let snapshot else { return [:] }
        let entries = historyStore.load()
        let loc = localizer
        var lines: [Provider: String] = [:]
        for provider in Provider.allCases {
            let usage = snapshot.usage(for: provider)
            let window = DisplayFormatter.preferredForecastWindow(usage)
            let forecast = UsageForecaster.forecast(
                entries: entries,
                provider: provider,
                window: window,
                resetAt: window == .fiveHour ? usage.resetAt5h : usage.resetAt7d
            )
            if let line = UsageForecastText.menuLine(forecast: forecast, window: window, localizer: loc) {
                lines[provider] = line
            }
        }
        return lines
    }

    /// Per-provider remaining sparklines for the History submenu, built from
    /// stored history (no network) against each provider's preferred window.
    /// Absent when there aren't enough points.
    private func sparklines() -> [Provider: String] {
        let entries = historyStore.load()
        var lines: [Provider: String] = [:]
        for provider in Provider.allCases {
            let window = snapshot.map { DisplayFormatter.preferredForecastWindow($0.usage(for: provider)) } ?? .fiveHour
            let series = SparklineSeries.build(entries: entries, provider: provider, window: window)
            let rendered = SparklineText.render(series)
            if !rendered.isEmpty {
                lines[provider] = "\(provider.displayName) \(window.shortLabel) \(rendered)"
            }
        }
        return lines
    }

    /// Predictive "will empty before reset" alerts, gated by notifications +
    /// the depletion-alert toggle. Considers both the 5h and 7d windows.
    private func forecastAlertCandidates(for snapshot: UsageSnapshot) -> [UsageAlertCandidate] {
        guard settings.notificationsEnabled, settings.depletionAlertEnabled else { return [] }
        let entries = historyStore.load()
        var inputs: [ForecastAlertInput] = []
        for provider in Provider.allCases {
            let usage = snapshot.usage(for: provider)
            if let forecast = UsageForecaster.forecast(entries: entries, provider: provider, window: .fiveHour, resetAt: usage.resetAt5h, now: snapshot.updatedAt) {
                inputs.append(ForecastAlertInput(provider: provider, window: .fiveHour, forecast: forecast, resetAt: usage.resetAt5h))
            }
            if let forecast = UsageForecaster.forecast(entries: entries, provider: provider, window: .sevenDay, resetAt: usage.resetAt7d, now: snapshot.updatedAt) {
                inputs.append(ForecastAlertInput(provider: provider, window: .sevenDay, forecast: forecast, resetAt: usage.resetAt7d))
            }
        }
        return UsageForecastAlert.candidates(inputs: inputs, enabled: true, localizer: localizer)
    }

    /// The "Updates paused" menu text, or `nil` when not paused. Shows the
    /// localized "until I resume" for an indefinite pause, else a countdown.
    private func pauseRemainingText() -> String? {
        let until = settings.pollPausedUntil
        guard PauseController.isPaused(until: until) else { return nil }
        if PauseController.isIndefinite(until: until) {
            return localizer.text(.pauseUntilResumed)
        }
        return UsageForecaster.durationText(PauseController.remaining(until: until))
    }

    private func pausePollingUntilResumed() {
        settings.pollPausedUntil = .distantFuture
        applyPauseChange()
    }

    private func resumePolling() {
        settings.pollPausedUntil = nil
        applyPauseChange()
        refreshNow()
    }

    private func pausePolling(for seconds: TimeInterval) {
        settings.pollPausedUntil = Date().addingTimeInterval(seconds)
        applyPauseChange()
    }

    private func applyPauseChange() {
        updateStatusTitle()
        configurePanel()
    }

    private func showPreferences() {
        if preferencesWindowController == nil {
            preferencesWindowController = PreferencesWindowController(
                settings: settings,
                onGeneralChange: { [weak self] in
                    Task { @MainActor in
                        self?.applySettingsChange()
                    }
                },
                onProviderChange: { [weak self] in
                    Task { @MainActor in
                        self?.configurePanel()
                        self?.refreshNow()
                    }
                },
                onNotificationsEnabled: { [weak self] in
                    Task { @MainActor in
                        self?.notificationCoordinator.requestAuthorization()
                        self?.configurePanel()
                    }
                },
                onCostChange: { [weak self] in
                    Task { @MainActor in
                        self?.updateStatusTitle()
                        self?.configurePanel()
                        self?.refreshCostsIfDue()
                    }
                }
            )
        }
        preferencesWindowController?.show()
    }

    private func copyDiagnostics() {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(diagnosticsReporter.diagnosticsText(), forType: .string)
    }

    private func openClaudeCredentials() {
        revealInFinder(DiagnosticsReporter.claudeCredentialsURL)
    }

    private func openCodexAuth() {
        revealInFinder(DiagnosticsReporter.codexAuthURL)
    }

    private func exportHistoryCSV() {
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "tokki-history.csv"
        panel.allowedContentTypes = [.commaSeparatedText]
        guard panel.runModal() == .OK, let url = panel.url else {
            return
        }

        do {
            try historyStore.csvString().write(to: url, atomically: true, encoding: .utf8)
        } catch {
            showError(localizer.text(.error), detail: error.localizedDescription)
        }
    }

    private func toggleLaunchAtLogin() {
        do {
            try loginItemManager.setEnabled(!loginItemManager.isEnabled)
        } catch {
            showError(localizer.text(.launchFailed), detail: error.localizedDescription)
        }
        configurePanel()
    }

    private func showError(_ title: String, detail: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.alertStyle = .warning
        alert.runModal()
    }

    private func applySettingsChange() {
        scheduleTimer()
        scheduleCountdownTimer()
        checkForUpdatesIfDue()
        updateStatusTitle()
        configurePanel()
    }

    private func fileExists(at url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.path)
    }

    private func revealInFinder(_ url: URL) {
        let existingURL = fileExists(at: url) ? url : url.deletingLastPathComponent()
        NSWorkspace.shared.activateFileViewerSelecting([existingURL])
    }

    private func runningInstanceCount() -> Int {
        let bundleIdentifier = Bundle.main.bundleIdentifier ?? "local.token-tracker.menubar"
        return NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier).count
    }

    /// Quit immediately when another instance is already running so a duplicate
    /// launch does not double the polling rate against the shared per-account
    /// Claude limit (parity with the Windows named-mutex guard). Returns `true`
    /// when this instance yielded and the caller must skip the rest of launch.
    private func terminateIfDuplicateInstance() -> Bool {
        let bundleIdentifier = Bundle.main.bundleIdentifier ?? "local.token-tracker.menubar"
        let current = NSRunningApplication.current
        let others = NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier)
            .filter { $0.processIdentifier != current.processIdentifier }
            .map { InstanceArbiter.Instance(pid: $0.processIdentifier, launchDate: $0.launchDate) }
        let currentInstance = InstanceArbiter.Instance(pid: current.processIdentifier, launchDate: current.launchDate)
        guard InstanceArbiter.shouldYield(current: currentInstance, others: others) else {
            return false
        }
        NSApp.terminate(nil)
        return true
    }
}
