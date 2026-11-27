import AppKit
import SwiftUI
import TokenTrackerCore

struct StatusPanelView: View {
    @ObservedObject var model: StatusPanelModel
    let actions: StatusPanelActions

    var body: some View {
        // Reset countdowns and "updated … ago" are formatted against the clock here,
        // not when the context was built, so a popover left open keeps time.
        TimelineView(.periodic(from: .now, by: 30)) { _ in
            if let context = model.context {
                VStack(spacing: 0) {
                    if let paused = context.pausedRemainingText {
                        PausedBanner(text: paused, localizer: context.localizer, resume: actions.resumePolling)
                    }
                    if let version = context.availableUpdateVersion {
                        UpdateBanner(version: version, localizer: context.localizer, download: actions.openReleasePage)
                    }
                    usage(context)
                    Divider()
                    StatusPanelFooter(context: context, isRefreshing: model.isRefreshing, actions: actions)
                }
            } else {
                ProgressView()
                    .controlSize(.small)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 28)
            }
        }
        .frame(width: 320)
    }

    @ViewBuilder
    private func usage(_ context: StatusPanelContext) -> some View {
        if let snapshot = context.snapshot {
            VStack(spacing: 8) {
                ProviderCard(usage: snapshot.claude, context: context)
                ProviderCard(usage: snapshot.codex, context: context)
            }
            .padding(10)
        } else {
            VStack(spacing: 10) {
                if model.isRefreshing {
                    ProgressView().controlSize(.small)
                }
                Text(context.localizer.text(.noUsageLoaded))
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 28)
        }
    }
}

private struct PausedBanner: View {
    let text: String
    let localizer: Localizer
    let resume: @MainActor () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "pause.circle.fill")
                .foregroundStyle(.orange)
            Text("\(localizer.text(.updatesPaused)): \(text)")
                .font(.system(size: 11))
                .lineLimit(1)
            Spacer(minLength: 8)
            Button(localizer.text(.resumeNow), action: resume)
                .buttonStyle(.link)
                .font(.system(size: 11, weight: .medium))
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(Color.orange.opacity(0.12))
    }
}

/// Opens the release page rather than installing: the app is not signed for an
/// automatic update, so the download and the copy into /Applications stay manual.
private struct UpdateBanner: View {
    let version: String
    let localizer: Localizer
    let download: @MainActor () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "arrow.down.circle.fill")
                .foregroundStyle(Color.accentColor)
            Text(localizer.text(.updateAvailable).replacingOccurrences(of: "{version}", with: "v\(version)"))
                .font(.system(size: 11))
                .lineLimit(1)
            Spacer(minLength: 8)
            Button(localizer.text(.downloadUpdate), action: download)
                .buttonStyle(.link)
                .font(.system(size: 11, weight: .medium))
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(Color.accentColor.opacity(0.10))
    }
}

private struct ProviderCard: View {
    let usage: ProviderUsage
    let context: StatusPanelContext

    private var localizer: Localizer { context.localizer }

    /// API cost for this provider, when switched on with a key.
    private var adminCost: AdminCostView? { context.adminCosts[usage.provider] }

    var body: some View {
        let issue = UsageIssueFormatter.issue(for: usage, localizer: localizer)
        // Subscription limits off and API cost on: the card is the cost alone.
        let costOnly = issue.kind == .disabled && adminCost != nil
        VStack(alignment: .leading, spacing: 10) {
            header(issue, costOnly: costOnly)
            if issue.kind != .disabled {
                if usage.isAvailable {
                    windowRow(
                        .fiveHour,
                        percent: usage.remainingPercent5h,
                        resetAt: usage.resetAt5h,
                        alertThreshold: context.settings.fiveHourAlertThreshold
                    )
                    windowRow(
                        .sevenDay,
                        percent: usage.remainingPercent7d,
                        resetAt: usage.resetAt7d,
                        alertThreshold: context.settings.sevenDayAlertThreshold
                    )
                }
                if let cost = SubscriptionCostText.line(for: usage, localizer: localizer) {
                    CostRow(line: cost)
                }
            }
            if issue.kind != .disabled {
                notes(issue)
            }
            if let adminCost {
                // Last, after the limits and their notes: the organization's bill is
                // a different thing from the subscription above it. Under the limits
                // the cost's own state goes in a note; on a card of its own, the
                // header badge says it.
                AdminCostSection(
                    title: localizer.text(.costSectionTitle)
                        .replacingOccurrences(of: "{organization}", with: AdminKeyText.organization(usage.provider)),
                    view: adminCost,
                    showsStatus: !costOnly
                )
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Color.primary.opacity(0.045))
        )
        .opacity(issue.kind == .disabled && !costOnly ? 0.55 : 1)
    }

    private func header(_ issue: UsageIssue, costOnly: Bool) -> some View {
        HStack(spacing: 6) {
            ProviderGlyph(provider: usage.provider)
            Text(usage.provider.displayName)
                .font(.system(size: 13, weight: .semibold))
            if costOnly {
                PlanCapsule(text: "API")
            } else if let plan = usage.plan, !plan.isEmpty {
                PlanCapsule(text: plan)
            }
            Spacer(minLength: 8)
            if costOnly, let adminCost {
                StatusBadge(color: adminCost.status.color, title: adminCost.statusTitle)
                    .help(adminCost.failureMessage ?? adminCost.statusTitle)
            } else {
                StatusBadge(kind: issue.kind, title: issue.title)
                    .help(issue.detail)
            }
        }
    }

    private func windowRow(_ window: ForecastWindow, percent: Int?, resetAt: Date?, alertThreshold: Int) -> some View {
        WindowRow(
            name: localizer.text(window == .fiveHour ? .fiveHourWindow : .sevenDayWindow),
            percent: percent,
            resetText: resetText(percent: percent, resetAt: resetAt),
            level: UsageLevel(remaining: percent, alertThreshold: alertThreshold),
            evenUseMark: evenUseMark(window)
        )
    }

    /// Where the bar would stand had the window been used evenly, drawn as a tick so
    /// a fill short of it reads as "spending fast" at a glance. Rides the forecast
    /// setting, as the pace line under the bars does.
    private func evenUseMark(_ window: ForecastWindow) -> Double? {
        guard context.settings.showForecast,
              let reading = UsagePaceEvaluator.reading(for: usage, window: window) else { return nil }
        return Double(reading.expectedRemainingPercent) / 100
    }

    private func resetText(percent: Int?, resetAt: Date?) -> String {
        if let resetAt, resetAt.timeIntervalSinceNow > 0 {
            return localizer.text(.resetsIn)
                .replacingOccurrences(of: "{time}", with: DisplayFormatter.formatReset(resetAt, localizer: localizer))
        }
        return DisplayFormatter.formatWindowReset(
            remainingPercent: percent,
            resetAt: resetAt,
            localizer: localizer,
            providerReported: usage.isAvailable
        )
    }

    @ViewBuilder
    private func notes(_ issue: UsageIssue) -> some View {
        let forecast = context.forecastLines[usage.provider]
        let pace = context.settings.showForecast ? UsagePaceText.menuLine(for: usage, localizer: localizer) : nil
        // Why signing in again did not stick: nothing has written the store since
        // long before this token could have lapsed.
        let staleCredentials = CredentialStoreAdvice.staleLine(
            provider: usage.provider,
            issue: issue,
            source: usage.provider == .claude ? context.claudeCredentialSource : context.codexCredentialSource,
            localizer: localizer
        )
        // With numbers on the card the status badge already says what happened; the
        // detail sentence is only worth the room when there is nothing else to show.
        let detail = usage.isAvailable ? nil : issue.detail

        if pace != nil || forecast != nil || detail != nil || issue.recovery != nil || staleCredentials != nil || issue.technicalDetail != nil {
            VStack(alignment: .leading, spacing: 5) {
                if let pace {
                    NoteRow(systemImage: "speedometer", text: pace)
                }
                if let forecast {
                    NoteRow(systemImage: "chart.line.downtrend.xyaxis", text: forecast)
                }
                if let detail {
                    NoteRow(systemImage: "info.circle", text: detail)
                }
                if let recovery = issue.recovery {
                    NoteRow(systemImage: "wrench.and.screwdriver", text: recovery)
                }
                if let staleCredentials {
                    NoteRow(systemImage: "clock.badge.exclamationmark", text: staleCredentials, tint: .orange)
                }
                if let technical = issue.technicalDetail {
                    Text("\(localizer.text(.technicalError)): \(technical)")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(.tertiary)
                        .lineLimit(2)
                        .truncationMode(.middle)
                        .textSelection(.enabled)
                        .help(technical)
                }
            }
        }
    }
}

/// How much is left in one window, for colouring its bar. Red matches the menu bar's
/// 7d warning (10% or less); orange starts at the user's own alert threshold, so the
/// bar turns at the same point a notification would fire.
private enum UsageLevel {
    case normal
    case low
    case critical

    init(remaining: Int?, alertThreshold: Int) {
        guard let remaining else {
            self = .normal
            return
        }
        if remaining <= 10 {
            self = .critical
        } else if remaining <= alertThreshold {
            self = .low
        } else {
            self = .normal
        }
    }

    var tint: Color {
        switch self {
        case .normal: .accentColor
        case .low: .orange
        case .critical: .red
        }
    }
}

private struct WindowRow: View {
    let name: String
    let percent: Int?
    let resetText: String
    let level: UsageLevel
    let evenUseMark: Double?

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(name)
                    .font(.system(size: 12, weight: .medium))
                Text(resetText)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .help(resetText)
                Spacer(minLength: 8)
                Text(DisplayFormatter.formatPercent(percent))
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(percentColor)
            }
            if let percent {
                UsageBar(fraction: Double(percent) / 100, tint: level.tint, mark: evenUseMark)
            }
        }
    }

    private var percentColor: Color {
        guard percent != nil else { return .secondary }
        return level == .normal ? .primary : level.tint
    }
}

/// Money past the plan: extra usage against its limit, or a credit balance. Laid out
/// like a window row so the amount lines up under the percentages.
private struct CostRow: View {
    let line: SubscriptionCostLine

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(line.label)
                    .font(.system(size: 12, weight: .medium))
                Spacer(minLength: 8)
                Text(line.value)
                    .font(.system(size: 13, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(tint == .accentColor ? .primary : tint)
            }
            if let percent = line.percent {
                UsageBar(fraction: Double(percent) / 100, tint: tint)
            }
        }
    }

    /// Orange from 80% of the limit and red at it, the budget alert thresholds.
    private var tint: Color {
        guard let percent = line.percent else { return .accentColor }
        if percent >= 100 { return .red }
        if percent >= 80 { return .orange }
        return .accentColor
    }
}

/// The organization's month of API cost: the amount, the budget bar, today and the
/// largest models, and how far the data reaches. A box of its own with a titled
/// header, because laid out like a window row it read as part of the limits.
private struct AdminCostSection: View {
    let title: String
    let view: AdminCostView
    let showsStatus: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 5) {
                Image(systemName: "dollarsign.circle.fill")
                    .font(.system(size: 11))
                    .foregroundStyle(.green)
                Text(title)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.secondary)
                Spacer(minLength: 8)
                if let asOf = view.asOf {
                    Text(asOf)
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                        .lineLimit(1)
                }
            }
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(view.monthLabel)
                    .font(.system(size: 12, weight: .medium))
                if let budgetText = view.budgetText {
                    Text(budgetText)
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)
                Text(view.monthValue)
                    .font(.system(size: 15, weight: .semibold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(tint == .accentColor ? .primary : tint)
            }
            if let percent = view.budgetPercent {
                UsageBar(fraction: Double(percent) / 100, tint: tint)
            }
            if let detail = view.detail {
                Text(detail)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if showsStatus, view.status != .ok {
                NoteRow(systemImage: "exclamationmark.circle", text: view.statusTitle, tint: view.status.color == .red ? .red : .secondary)
                    .help(view.failureMessage ?? view.statusTitle)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .fill(Color.primary.opacity(0.05))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8, style: .continuous)
                .strokeBorder(Color.primary.opacity(0.08))
        )
    }

    /// Orange from 80% of the budget and red at it, the budget alert thresholds.
    private var tint: Color {
        guard let percent = view.budgetPercent else { return .accentColor }
        if percent >= 100 { return .red }
        if percent >= 80 { return .orange }
        return .accentColor
    }
}

private extension AdminCostView.Status {
    var color: Color {
        switch self {
        case .ok: .green
        case .loading: .secondary
        case .cached, .rateLimited: .orange
        case .keyRejected, .failed: .red
        }
    }
}

private struct PlanCapsule: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 10, weight: .medium))
            .foregroundStyle(.secondary)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(Capsule().fill(Color.primary.opacity(0.08)))
    }
}

private struct UsageBar: View {
    let fraction: Double
    let tint: Color
    var mark: Double?

    var body: some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Capsule()
                    .fill(Color.primary.opacity(0.09))
                Capsule()
                    .fill(tint)
                    // A sliver stays visible at 1–2% so "almost empty" never reads as "no bar".
                    .frame(width: fraction > 0 ? max(proxy.size.width * min(fraction, 1), 4) : 0)
                if let mark {
                    Capsule()
                        .fill(Color.primary.opacity(0.55))
                        .frame(width: 2, height: 10)
                        .offset(x: min(max(proxy.size.width * mark - 1, 0), proxy.size.width - 2))
                }
            }
        }
        .frame(height: 6)
    }
}

private struct StatusBadge: View {
    let color: Color
    let title: String

    init(color: Color, title: String) {
        self.color = color
        self.title = title
    }

    init(kind: UsageIssueKind, title: String) {
        self.init(color: Self.color(for: kind), title: title)
    }

    var body: some View {
        HStack(spacing: 4) {
            Circle()
                .fill(color)
                .frame(width: 6, height: 6)
            Text(title)
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }

    private static func color(for kind: UsageIssueKind) -> Color {
        switch kind {
        case .ok: .green
        case .statusLine: .blue
        case .usingCachedData, .rateLimited: .orange
        case .disabled: .secondary
        case .missingCredentials, .expiredCredentials, .invalidResponse, .timedOut, .network, .httpStatus, .unavailable: .red
        }
    }
}

private struct NoteRow: View {
    let systemImage: String
    let text: String
    var tint: Color = .secondary

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            Image(systemName: systemImage)
                .font(.system(size: 10))
                .foregroundStyle(tint)
                .frame(width: 12)
            // Recovery hints quote commands in backticks; render them as code.
            Text(Self.attributed(text))
                .font(.system(size: 11))
                .foregroundStyle(tint == .secondary ? Color.secondary : tint)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private static func attributed(_ text: String) -> AttributedString {
        let options = AttributedString.MarkdownParsingOptions(interpretedSyntax: .inlineOnlyPreservingWhitespace)
        return (try? AttributedString(markdown: text, options: options)) ?? AttributedString(text)
    }
}

private struct ProviderGlyph: View {
    let provider: Provider

    var body: some View {
        if let image = StatusItemRenderer.providerIcon(provider) {
            Image(nsImage: image)
                .renderingMode(.template)
                .resizable()
                .aspectRatio(contentMode: .fit)
                .frame(width: 14, height: 14)
                .foregroundStyle(.secondary)
        }
    }
}

private struct StatusPanelFooter: View {
    let context: StatusPanelContext
    let isRefreshing: Bool
    let actions: StatusPanelActions

    private var localizer: Localizer { context.localizer }

    var body: some View {
        HStack(spacing: 2) {
            VStack(alignment: .leading, spacing: 1) {
                if let snapshot = context.snapshot {
                    Text("\(localizer.text(.updated)) \(relative(snapshot.updatedAt))")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
                if let lastSuccessLine {
                    Text(lastSuccessLine)
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                }
            }
            .lineLimit(1)
            Spacer(minLength: 8)
            FooterButton(
                systemImage: "arrow.clockwise",
                help: localizer.text(.refreshNow),
                shortcut: "r",
                isBusy: isRefreshing,
                action: actions.refreshNow
            )
            FooterButton(
                systemImage: "gearshape",
                help: localizer.text(.preferences).trimmingCharacters(in: CharacterSet(charactersIn: ".…")),
                shortcut: ",",
                action: actions.showPreferences
            )
            moreMenu
        }
        .padding(.leading, 12)
        .padding(.trailing, 8)
        .padding(.vertical, 8)
    }

    /// The last measurement only earns its own line when it differs from the poll
    /// time above it, which is exactly when a stale reading is on screen.
    private var lastSuccessLine: String? {
        guard let snapshot = context.snapshot else { return nil }
        guard let last = context.lastSuccessfulRefreshAt else {
            return localizer.text(.noSuccessfulUpdate)
        }
        guard abs(snapshot.updatedAt.timeIntervalSince(last)) >= 60 else { return nil }
        return "\(localizer.text(.lastSuccessfulUpdate)): \(relative(last))"
    }

    private var moreMenu: some View {
        Menu {
            if context.pausedRemainingText != nil {
                Button(localizer.text(.resumeNow), action: actions.resumePolling)
            }
            Menu(localizer.text(.pausePolling)) {
                Button(localizer.text(.pause1h), action: actions.pause1h)
                Button(localizer.text(.pause3h), action: actions.pause3h)
                Button(localizer.text(.pauseUntilResumed), action: actions.pauseUntilResumed)
            }
            Menu(localizer.text(.history)) {
                Text(context.historyTrendText)
                ForEach(Provider.allCases, id: \.self) { provider in
                    if let sparkline = context.sparklines[provider] {
                        Text(sparkline)
                    }
                }
                Text("\(localizer.text(.historyRetentionDays)): \(context.settings.historyRetentionDays)d")
                Divider()
                Button(localizer.text(.exportHistoryCSV), action: actions.exportHistoryCSV)
            }
            Menu(localizer.text(.diagnostics)) {
                Button(localizer.text(.copyDiagnostics), action: actions.copyDiagnostics)
                Divider()
                Button(localizer.text(.openClaudeCredentials), action: actions.openClaudeCredentials)
                Button(localizer.text(.openCodexAuth), action: actions.openCodexAuth)
                Divider()
                Text("\(localizer.text(.duplicateInstances)): \(context.runningInstanceCount)")
                if context.settings.refreshInterval < 300 {
                    Text(localizer.text(.refreshIntervalWarning))
                }
            }
            Divider()
            Button(action: actions.toggleLaunchAtLogin) {
                if context.launchAtLoginEnabled {
                    Label("\(localizer.text(.launchAtLogin)): \(context.launchAtLoginStatus)", systemImage: "checkmark")
                } else {
                    Text("\(localizer.text(.launchAtLogin)): \(context.launchAtLoginStatus)")
                }
            }
            Divider()
            Button(localizer.text(.quit), action: actions.quit)
                .keyboardShortcut("q")
        } label: {
            Image(systemName: "ellipsis.circle")
                .font(.system(size: 13, weight: .medium))
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .frame(width: 30, height: 24)
        .foregroundStyle(.secondary)
        .help(localizer.text(.more))
    }

    private func relative(_ date: Date) -> String {
        let seconds = max(0, Int(Date().timeIntervalSince(date)))
        if seconds < 60 { return "\(seconds)s \(localizer.text(.ago))" }
        let minutes = seconds / 60
        if minutes < 60 { return "\(minutes)m \(localizer.text(.ago))" }
        return "\(minutes / 60)h \(localizer.text(.ago))"
    }
}

private struct FooterButton: View {
    let systemImage: String
    let help: String
    let shortcut: KeyEquivalent
    var isBusy = false
    let action: @MainActor () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                if isBusy {
                    ProgressView()
                        .controlSize(.small)
                        .scaleEffect(0.7)
                } else {
                    Image(systemName: systemImage)
                        .font(.system(size: 13, weight: .medium))
                }
            }
            .frame(width: 28, height: 24)
            .contentShape(Rectangle())
        }
        .buttonStyle(FooterButtonStyle())
        .keyboardShortcut(shortcut, modifiers: .command)
        .disabled(isBusy)
        .help(help)
    }
}

/// Press feedback without view state: `@State` is a macro in this SDK and the
/// Command Line Tools toolchain `swift build` uses here ships no SwiftUI macro plugin.
private struct FooterButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .foregroundStyle(.secondary)
            .background(
                RoundedRectangle(cornerRadius: 6, style: .continuous)
                    .fill(Color.primary.opacity(configuration.isPressed ? 0.12 : 0))
            )
    }
}
