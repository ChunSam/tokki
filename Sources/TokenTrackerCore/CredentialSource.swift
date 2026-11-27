import Foundation
#if canImport(Security)
import Security
#endif

/// Where the Claude access token actually came from, and when it was last written.
///
/// Diagnostics used to report only whether `~/.claude/.credentials.json` exists,
/// which reads `false` on a normal macOS install — the token lives in the keychain
/// and the file is a fallback. That hid the question that matters when usage stops
/// loading: *is anything still refreshing this token?*
///
/// The access token lives ~8h and only the Claude Code CLI (or its daemon, which
/// exits when idle) writes this store; the desktop app refreshes its own auth
/// elsewhere and leaves the keychain item untouched. So a modification date hours
/// or days old is the signal that the store has been abandoned, and the whole
/// answer to "why does it keep expiring".
///
/// Reads attributes only — never the secret.
public struct ClaudeCredentialSource: Sendable, Equatable {
    public enum Kind: String, Sendable {
        case keychain
        case credentialsFile
        case none
    }

    public let kind: Kind
    public let lastWrittenAt: Date?

    public init(kind: Kind, lastWrittenAt: Date?) {
        self.kind = kind
        self.lastWrittenAt = lastWrittenAt
    }

    public static func detect(credentialsFileURL: URL) -> ClaudeCredentialSource {
        detect(credentialsFileURL: credentialsFileURL, keychainModifiedAt: keychainModificationDate())
    }

    /// Seam for tests: the keychain lookup is the one part that cannot be arranged
    /// on a build machine, and the precedence between the two stores is exactly what
    /// the old "does the file exist" line got wrong.
    public static func detect(credentialsFileURL: URL, keychainModifiedAt: Date?) -> ClaudeCredentialSource {
        if let keychainModifiedAt {
            return ClaudeCredentialSource(kind: .keychain, lastWrittenAt: keychainModifiedAt)
        }
        if let attributes = try? FileManager.default.attributesOfItem(atPath: credentialsFileURL.path) {
            return ClaudeCredentialSource(
                kind: .credentialsFile,
                lastWrittenAt: attributes[.modificationDate] as? Date
            )
        }
        return ClaudeCredentialSource(kind: .none, lastWrittenAt: nil)
    }

    /// How stale the store is, which is what tells a lapsed token apart from a
    /// merely unlucky one: past roughly the 8h access-token life, nothing is
    /// refreshing it any more.
    public func age(now: Date = Date()) -> TimeInterval? {
        lastWrittenAt.map { now.timeIntervalSince($0) }
    }

    private static func keychainModificationDate() -> Date? {
        #if canImport(Security)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: "Claude Code-credentials",
            kSecReturnAttributes as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var item: CFTypeRef?
        guard
            SecItemCopyMatching(query as CFDictionary, &item) == errSecSuccess,
            let attributes = item as? [String: Any]
        else {
            return nil
        }
        return attributes[kSecAttrModificationDate as String] as? Date
        #else
        return nil
        #endif
    }
}

/// The line the menu was missing when usage stops loading: nothing has written the
/// credential store for longer than a token lives, so the fix is not to wait but to
/// use the CLI once.
///
/// The plain "sign in again" recovery is complete advice for a token that merely
/// lapsed. It is not the whole story for a store that has been abandoned — a user
/// who signs in through the desktop app has signed in, and the store still will
/// not move. Saying how long it has been unwritten is what tells the two apart.
///
/// Codex keeps its store in `~/.codex/auth.json` and renews it only while it is
/// being used, so a Codex left alone past its token life lapses the same way. The
/// store is read through `ClaudeCredentialSource.detect` with no keychain: the type
/// describes any file store, the name only records where it started.
public enum CredentialStoreAdvice {
    /// A Claude access token lives ~8h. Past that, an expired token is not bad
    /// luck: whatever used to refresh the store has stopped.
    public static let staleAfter: TimeInterval = 8 * 3600

    /// A Codex access token lives 10 days (measured 2026-11-27: written
    /// 11-16 17:06:40, expiring 11-26 17:06:40), so a younger store that lapsed
    /// was cut short by something else and its age explains nothing.
    public static let codexStaleAfter: TimeInterval = 10 * 24 * 3600

    public static func staleAfter(for provider: Provider) -> TimeInterval {
        switch provider {
        case .claude: return staleAfter
        case .codex: return codexStaleAfter
        }
    }

    /// `nil` unless the failure is a lapsed token and `source` — that provider's
    /// own store — is old enough that nothing can still be refreshing it.
    public static func staleLine(
        provider: Provider,
        issue: UsageIssue,
        source: ClaudeCredentialSource,
        now: Date = Date(),
        localizer: Localizer = Localizer(language: .english)
    ) -> String? {
        // Read past the visible kind to the error underneath it: once the last good
        // reading is being carried across a lapsed token the status reads "using
        // cached data", and the expiry survives only in the error it carries.
        guard
            let error = issue.technicalDetail,
            UsageIssueFormatter.kind(forError: error) == .expiredCredentials,
            let age = source.age(now: now),
            age >= staleAfter(for: provider)
        else {
            return nil
        }
        return localizer.text(.credentialStale)
            .replacingOccurrences(of: "{age}", with: UsageForecaster.durationText(age))
    }
}
