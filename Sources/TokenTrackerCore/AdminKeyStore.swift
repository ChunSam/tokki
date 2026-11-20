import Foundation
#if canImport(Security)
import Security
import LocalAuthentication
#endif

/// Where an organization's Admin API key is kept. An Admin key can manage the
/// whole organization, so never a plain file: the keychain here, DPAPI on Windows.
/// Mirrors windows/TokenTracker.Windows.Core/AdminKeyStore.cs.
public protocol AdminKeyStoring: Sendable {
    func key(for provider: Provider) -> String?
    @discardableResult
    func save(_ key: String, for provider: Provider) -> Bool
    func delete(for provider: Provider)
}

extension Provider {
    /// The keychain account, or Windows file entry, that holds this provider's
    /// Admin key. Named for the organization the key belongs to, not the CLI.
    var adminKeyAccount: String {
        switch self {
        case .claude: "anthropic-admin"
        case .codex: "openai-admin"
        }
    }
}

public enum AdminKeyText {
    /// `…ab12`: all of a key that settings and diagnostics may show. A key too short
    /// to be real shows nothing, so the four characters are never most of it.
    public static func suffix(_ key: String) -> String {
        key.count > 8 ? "…" + String(key.suffix(4)) : "…"
    }

    /// Whose console the key comes from.
    public static func organization(_ provider: Provider) -> String {
        switch provider {
        case .claude: "Anthropic"
        case .codex: "OpenAI"
        }
    }

    /// How an Admin key starts. An ordinary API key (`sk-ant-api…`, `sk-proj-…`)
    /// cannot read cost, and pasting one is the likely mistake.
    public static func prefix(_ provider: Provider) -> String {
        switch provider {
        case .claude: "sk-ant-admin"
        case .codex: "sk-admin-"
        }
    }

    public static func looksLikeAdminKey(_ key: String, for provider: Provider) -> Bool {
        key.trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix(prefix(provider))
    }
}

#if canImport(Security)
/// One generic password per provider, owned by this app, readable only while the
/// Mac is unlocked and never synced off it.
public struct KeychainAdminKeyStore: AdminKeyStoring {
    private let service: String
    private let allowAuthenticationUI: Bool

    // Keeps the pre-Tokki name: keys saved by Token Tracker are found under it.
    public init(service: String = "Token Tracker", allowAuthenticationUI: Bool = true) {
        self.service = service
        self.allowAuthenticationUI = allowAuthenticationUI
    }

    public func key(for provider: Provider) -> String? {
        var query = baseQuery(provider)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard
            SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
            let data = result as? Data,
            let key = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
            !key.isEmpty
        else {
            return nil
        }
        return key
    }

    @discardableResult
    public func save(_ key: String, for provider: Provider) -> Bool {
        delete(for: provider)
        var item = baseQuery(provider)
        item[kSecValueData as String] = Data(key.trimmingCharacters(in: .whitespacesAndNewlines).utf8)
        item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        item[kSecAttrLabel as String] = "Token Tracker (\(provider.displayName) Admin key)"
        return SecItemAdd(item as CFDictionary, nil) == errSecSuccess
    }

    public func delete(for provider: Provider) {
        SecItemDelete(baseQuery(provider) as CFDictionary)
    }

    private func baseQuery(_ provider: Provider) -> [String: Any] {
        var query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: provider.adminKeyAccount
        ]
        if !allowAuthenticationUI {
            // Background cost refresh must not put up a keychain authorization
            // dialog after an ad-hoc-signed app update. The settings dialog omits
            // this context because opening it is an explicit user action.
            let context = LAContext()
            context.interactionNotAllowed = true
            query[kSecUseAuthenticationContext as String] = context
        }
        return query
    }
}
#endif
