import AppKit
import SwiftUI
import TokenTrackerCore

struct StatusPanelActions {
    let refreshNow: @MainActor () -> Void
    let showPreferences: @MainActor () -> Void
    let copyDiagnostics: @MainActor () -> Void
    let openClaudeCredentials: @MainActor () -> Void
    let openCodexAuth: @MainActor () -> Void
    let exportHistoryCSV: @MainActor () -> Void
    let toggleLaunchAtLogin: @MainActor () -> Void
    let pause1h: @MainActor () -> Void
    let pause3h: @MainActor () -> Void
    let pauseUntilResumed: @MainActor () -> Void
    let resumePolling: @MainActor () -> Void
    let openReleasePage: @MainActor () -> Void
    let quit: @MainActor () -> Void
}

struct StatusPanelContext {
    let localizer: Localizer
    let settings: TokenTrackerCore.Settings
    let snapshot: UsageSnapshot?
    let lastSuccessfulRefreshAt: Date?
    let forecastLines: [Provider: String]
    let pausedRemainingText: String?
    let sparklines: [Provider: String]
    let historyTrendText: String
    let claudeCredentialSource: ClaudeCredentialSource
    /// `~/.codex/auth.json`, read the same way: attributes only.
    let codexCredentialSource: ClaudeCredentialSource
    let launchAtLoginEnabled: Bool
    let launchAtLoginStatus: String
    let runningInstanceCount: Int
    /// A newer release than this build, when the daily check found one.
    let availableUpdateVersion: String?
    /// The API cost section per provider; absent where API cost is off or keyless.
    let adminCosts: [Provider: AdminCostView]
}

@MainActor
final class StatusPanelModel: ObservableObject {
    @Published var context: StatusPanelContext?
    @Published var isRefreshing = false
}

/// The popover the status item opens. Content is SwiftUI; this class only owns
/// showing and dismissing it.
///
/// The popover is `.applicationDefined` rather than `.transient` on purpose: a
/// transient popover closes on the mouse-down that lands on the status item, and
/// the button's mouse-up action then reopens it, so clicking the item could never
/// close it. Outside clicks are caught with a global monitor instead, which only
/// sees other apps' events and so never races the button.
@MainActor
final class StatusPanelController: NSObject, NSPopoverDelegate {
    let model = StatusPanelModel()
    private let popover = NSPopover()
    private weak var anchor: NSStatusBarButton?
    private var outsideClickMonitor: Any?
    private var escapeKeyMonitor: Any?
    private var resignActiveObserver: NSObjectProtocol?

    var isShown: Bool { popover.isShown }

    init(actions: StatusPanelActions) {
        super.init()
        let hosting = NSHostingController(
            rootView: StatusPanelView(model: model, actions: closingWhereNeeded(actions))
        )
        hosting.sizingOptions = .preferredContentSize
        popover.contentViewController = hosting
        popover.behavior = .applicationDefined
        popover.animates = true
        popover.delegate = self
    }

    func toggle(relativeTo button: NSStatusBarButton) {
        if popover.isShown {
            close()
            return
        }
        anchor = button
        NSApp.activate(ignoringOtherApps: true)
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
        button.highlight(true)
        installMonitors()
    }

    func close() {
        guard popover.isShown else { return }
        popover.performClose(nil)
    }

    nonisolated func popoverDidClose(_ notification: Notification) {
        MainActor.assumeIsolated {
            anchor?.highlight(false)
            removeMonitors()
        }
    }

    private func installMonitors() {
        removeMonitors()
        outsideClickMonitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
            MainActor.assumeIsolated { self?.close() }
        }
        escapeKeyMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard event.keyCode == 53 else { return event }
            MainActor.assumeIsolated { self?.close() }
            return nil
        }
        // ⌘Tab away moves focus without a click the global monitor could see.
        resignActiveObserver = NotificationCenter.default.addObserver(
            forName: NSApplication.didResignActiveNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.close() }
        }
    }

    private func removeMonitors() {
        for monitor in [outsideClickMonitor, escapeKeyMonitor].compactMap({ $0 }) {
            NSEvent.removeMonitor(monitor)
        }
        if let resignActiveObserver {
            NotificationCenter.default.removeObserver(resignActiveObserver)
        }
        outsideClickMonitor = nil
        escapeKeyMonitor = nil
        resignActiveObserver = nil
    }

    /// Anything that opens a window, a panel or Finder closes the popover first, so
    /// it is not left floating over what it opened. Refresh, pause and the login
    /// toggle keep it open: their result shows up in the popover itself.
    private func closingWhereNeeded(_ actions: StatusPanelActions) -> StatusPanelActions {
        func closing(_ action: @escaping @MainActor () -> Void) -> @MainActor () -> Void {
            { [weak self] in
                self?.close()
                // A later turn of the main actor: a modal save panel started from
                // inside the menu's own action would block the popover's close.
                Task { @MainActor in action() }
            }
        }
        return StatusPanelActions(
            refreshNow: actions.refreshNow,
            showPreferences: closing(actions.showPreferences),
            copyDiagnostics: closing(actions.copyDiagnostics),
            openClaudeCredentials: closing(actions.openClaudeCredentials),
            openCodexAuth: closing(actions.openCodexAuth),
            exportHistoryCSV: closing(actions.exportHistoryCSV),
            toggleLaunchAtLogin: actions.toggleLaunchAtLogin,
            pause1h: actions.pause1h,
            pause3h: actions.pause3h,
            pauseUntilResumed: actions.pauseUntilResumed,
            resumePolling: actions.resumePolling,
            openReleasePage: closing(actions.openReleasePage),
            quit: actions.quit
        )
    }
}
