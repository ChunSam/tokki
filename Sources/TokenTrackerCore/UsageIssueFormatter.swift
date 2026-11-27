import Foundation

public enum UsageIssueKind: String, Equatable, Sendable {
    case ok
    case disabled
    case rateLimited
    case missingCredentials
    case expiredCredentials
    case invalidResponse
    case timedOut
    case network
    case httpStatus
    case usingCachedData
    case statusLine
    case unavailable
}

public struct UsageIssue: Equatable, Sendable {
    public let kind: UsageIssueKind
    public let title: String
    public let detail: String
    public let recovery: String?
    public let technicalDetail: String?

    public init(kind: UsageIssueKind, title: String, detail: String, recovery: String?, technicalDetail: String?) {
        self.kind = kind
        self.title = title
        self.detail = detail
        self.recovery = recovery
        self.technicalDetail = technicalDetail
    }
}

public enum UsageIssueFormatter {
    public static func issue(for usage: ProviderUsage, localizer: Localizer = Localizer(language: .english)) -> UsageIssue {
        // Real account numbers, measured by Claude Code rather than by this app.
        // Any error the API path reported is carried through so the menu can still
        // name the fix (sign in again) beside the numbers it is showing.
        if usage.source == .statusLine {
            let technicalDetail = usage.error
            return UsageIssue(
                kind: .statusLine,
                title: localizer.text(.statusFromStatusLine),
                detail: localizer.text(.statusFromStatusLineDetail),
                recovery: recovery(for: technicalDetail, provider: usage.provider, localizer: localizer),
                technicalDetail: technicalDetail
            )
        }

        if usage.source == .staleCache {
            let technicalDetail = usage.error
            return UsageIssue(
                kind: .usingCachedData,
                title: localizer.text(.statusUsingCachedData),
                detail: localizer.text(.statusUsingCachedDataDetail),
                recovery: recovery(for: technicalDetail, provider: usage.provider, localizer: localizer)
                    ?? localizer.text(.recoveryTryAgainLater),
                technicalDetail: technicalDetail
            )
        }

        guard let error = usage.error, !error.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            if usage.isAvailable {
                return UsageIssue(
                    kind: .ok,
                    title: localizer.text(.statusOK),
                    detail: localizer.text(.statusOKDetail),
                    recovery: nil,
                    technicalDetail: nil
                )
            }
            return UsageIssue(
                kind: .unavailable,
                title: localizer.text(.statusUnavailable),
                detail: localizer.text(.statusUnavailableDetail),
                recovery: localizer.text(.recoveryRefreshLater),
                technicalDetail: nil
            )
        }

        let kind = kind(forError: error)
        return UsageIssue(
            kind: kind,
            title: title(for: kind, localizer: localizer),
            detail: detail(for: kind, localizer: localizer),
            recovery: recovery(for: error, provider: usage.provider, localizer: localizer),
            technicalDetail: error
        )
    }

    public static func kind(forError error: String) -> UsageIssueKind {
        let normalized = error.trimmingCharacters(in: .whitespacesAndNewlines)
        let lower = normalized.lowercased()

        if lower == "disabled" {
            return .disabled
        }
        if lower.contains("http 429") || lower.contains("too many requests") {
            return .rateLimited
        }
        if lower.contains("missing credentials") {
            return .missingCredentials
        }
        // 401/403 from either usage endpoint is, in practice, always a lapsed
        // access token: Tokki reads whatever the provider CLI last stored
        // and never refreshes it. Reporting it as a generic HTTP error left the
        // user with "refresh again later", which never fixes it.
        if lower.contains("credentials expired") || lower.contains("http 401") || lower.contains("http 403") {
            return .expiredCredentials
        }
        if lower.contains("invalid response") {
            return .invalidResponse
        }
        if lower.contains("timed out") {
            return .timedOut
        }
        if lower.contains("network error") {
            return .network
        }
        if lower.hasPrefix("http ") {
            return .httpStatus
        }
        return .unavailable
    }

    private static func title(for kind: UsageIssueKind, localizer: Localizer) -> String {
        switch kind {
        case .ok:
            return localizer.text(.statusOK)
        case .disabled:
            return localizer.text(.statusDisabledProvider)
        case .rateLimited:
            return localizer.text(.statusRateLimited)
        case .missingCredentials:
            return localizer.text(.statusMissingCredentials)
        case .expiredCredentials:
            return localizer.text(.statusExpiredCredentials)
        case .invalidResponse:
            return localizer.text(.statusInvalidResponse)
        case .timedOut:
            return localizer.text(.statusTimedOut)
        case .network:
            return localizer.text(.statusNetworkIssue)
        case .httpStatus:
            return localizer.text(.statusHTTPError)
        case .usingCachedData:
            return localizer.text(.statusUsingCachedData)
        case .statusLine:
            return localizer.text(.statusFromStatusLine)
        case .unavailable:
            return localizer.text(.statusUnavailable)
        }
    }

    private static func detail(for kind: UsageIssueKind, localizer: Localizer) -> String {
        switch kind {
        case .ok:
            return localizer.text(.statusOKDetail)
        case .disabled:
            return localizer.text(.statusDisabledProviderDetail)
        case .rateLimited:
            return localizer.text(.statusRateLimitedDetail)
        case .missingCredentials:
            return localizer.text(.statusMissingCredentialsDetail)
        case .expiredCredentials:
            return localizer.text(.statusExpiredCredentialsDetail)
        case .invalidResponse:
            return localizer.text(.statusInvalidResponseDetail)
        case .timedOut:
            return localizer.text(.statusTimedOutDetail)
        case .network:
            return localizer.text(.statusNetworkIssueDetail)
        case .httpStatus:
            return localizer.text(.statusHTTPErrorDetail)
        case .usingCachedData:
            return localizer.text(.statusUsingCachedDataDetail)
        case .statusLine:
            return localizer.text(.statusFromStatusLineDetail)
        case .unavailable:
            return localizer.text(.statusUnavailableDetail)
        }
    }

    private static func recovery(for error: String?, provider: Provider, localizer: Localizer) -> String? {
        guard let error else {
            return nil
        }
        switch kind(forError: error) {
        case .disabled:
            return localizer.text(.recoveryEnableProvider)
        case .rateLimited:
            return localizer.text(.recoveryWaitForCooldown)
        case .missingCredentials:
            // Same reason as below: one command, the one for this provider.
            switch provider {
            case .claude: return localizer.text(.recoverySignInClaude)
            case .codex: return localizer.text(.recoverySignInCodex)
            }
        case .expiredCredentials:
            // Name only the command the user actually has to run. Listing both
            // providers made them read past the half that did not apply to them.
            switch provider {
            case .claude: return localizer.text(.recoverySignInAgainClaude)
            case .codex: return localizer.text(.recoverySignInAgainCodex)
            }
        case .invalidResponse:
            return localizer.text(.recoveryUpdateOrTryLater)
        case .timedOut, .network:
            return localizer.text(.recoveryCheckNetwork)
        case .httpStatus, .unavailable:
            return localizer.text(.recoveryRefreshLater)
        case .ok, .usingCachedData, .statusLine:
            return nil
        }
    }
}
