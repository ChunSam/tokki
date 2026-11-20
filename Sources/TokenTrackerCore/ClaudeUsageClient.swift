import Foundation

struct ClaudeUsageClient: Sendable {
    private static let keychainReadTimeout: TimeInterval = 10
    private let http: HTTPClient
    private let usageURL = URL(string: "https://api.anthropic.com/api/oauth/usage")!
    private let rateLimitState = RateLimitState(store: RateLimitStore(url: AppPaths.claudeRateLimit))
    private let statusLineStore: ClaudeStatusLineStore
    let cliRefresher: ClaudeCLIRefresher?
    fileprivate static let defaultRateLimitCooldown: TimeInterval = 300

    init(
        http: HTTPClient = HTTPClient(),
        statusLineStore: ClaudeStatusLineStore = ClaudeStatusLineStore(),
        cliRefresher: ClaudeCLIRefresher? = nil
    ) {
        self.http = http
        self.statusLineStore = statusLineStore
        self.cliRefresher = cliRefresher
    }

    /// The API endpoint first, because only that is a measurement taken now. The
    /// status-line bridge is the fallback for when the stored token has lapsed and
    /// Claude Code could not renew it — or was not asked to.
    func fetch(renewingExpiredToken: Bool = false) async -> ProviderUsage {
        let api = await fetchFromProviderAPI(renewingExpiredToken: renewingExpiredToken)
        guard !api.isAvailable else { return api }
        return ClaudeStatusLineBridge.usage(statusLineStore.read(), apiError: api.error) ?? api
    }

    private func fetchFromProviderAPI(renewingExpiredToken: Bool) async -> ProviderUsage {
        var candidates: [TokenCandidate]
        do {
            candidates = try readTokenCandidates()
        } catch {
            return .unavailable(.claude, error: error.localizedDescription)
        }

        // Every stored token has lapsed: have Claude Code renew the store before
        // reporting it, and read it again only if it moved.
        if renewingExpiredToken,
           candidates.allSatisfy(\.isExpired),
           let cliRefresher,
           await cliRefresher.refreshIfDue(),
           let renewed = try? readTokenCandidates() {
            candidates = renewed
        }

        // Every stored token has lapsed. Spending a request on one would return an
        // opaque 401 — or a 429 that the backoff below would mistake for a quota
        // cooldown — so report the real cause and drop any cooldown that a previous
        // expired-token round already recorded.
        let usable = candidates.filter { !$0.isExpired }
        guard !usable.isEmpty else {
            await rateLimitState.clear()
            return .unavailable(
                .claude,
                error: UsageError.expiredCredentials(
                    service: "Claude API",
                    expiredAt: candidates.compactMap(\.expiresAt).max()
                ).localizedDescription
            )
        }

        // A cooldown is only binding for the credentials that earned it; signing in
        // again has to take effect immediately rather than wait it out.
        let fingerprint = TokenFingerprint.of(usable.map(\.token))
        if let error = await rateLimitState.currentError(serviceName: "Claude API", fingerprint: fingerprint) {
            return .unavailable(.claude, error: error.localizedDescription)
        }

        do {
            let usage = try await fetchFromAPI(candidates: usable)
            await rateLimitState.clear()
            return usage
        } catch let error as UsageError {
            if let retryAfter = error.rateLimitRetryAfter {
                await rateLimitState.backOff(for: retryAfter, fingerprint: fingerprint)
                let currentError = await rateLimitState.currentError(serviceName: "Claude API", fingerprint: fingerprint)
                return .unavailable(.claude, error: currentError?.localizedDescription ?? error.localizedDescription)
            }
            if case .httpStatus(429, _, nil) = error {
                await rateLimitState.backOff(for: Self.defaultRateLimitCooldown, fingerprint: fingerprint)
                let currentError = await rateLimitState.currentError(serviceName: "Claude API", fingerprint: fingerprint)
                return .unavailable(.claude, error: currentError?.localizedDescription ?? error.localizedDescription)
            }
            return .unavailable(.claude, error: error.localizedDescription)
        } catch {
            return .unavailable(.claude, error: error.localizedDescription)
        }
    }

    private func fetchFromAPI(candidates: [TokenCandidate]) async throws -> ProviderUsage {
        var lastError: UsageError = .missingCredentials
        for (index, candidate) in candidates.enumerated() {
            do {
                return try await fetchFromAPI(token: candidate.token, fallbackPlan: candidate.plan)
            } catch let error as UsageError where error.isAuthenticationFailure
                && candidate.source == .keychain
                && candidates.indices.contains(index + 1)
            {
                lastError = error
                continue
            }
        }
        throw lastError
    }

    private func fetchFromAPI(token: String, fallbackPlan: String?) async throws -> ProviderUsage {
        let raw = try await http.getJSON(
            url: usageURL,
            headers: [
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": "Bearer \(token)",
                "anthropic-beta": "oauth-2025-04-20",
                "User-Agent": "TokenTrackerMenuBar/1.0"
            ],
            timeout: 10,
            serviceName: "Claude API"
        )
        return try ClaudeUsageParser.parse(raw, fallbackPlan: fallbackPlan)
    }

    private func readTokenCandidates() throws -> [TokenCandidate] {
        let rawCandidates = [
            readCredentialFromKeychain().flatMap { TokenCandidate(source: .keychain, credential: $0) },
            readCredentialFromFile().flatMap { TokenCandidate(source: .credentialsFile, credential: $0) }
        ].compactMap { $0 }

        var candidates: [TokenCandidate] = []
        var seenTokens = Set<String>()
        for candidate in rawCandidates where seenTokens.insert(candidate.token).inserted {
            candidates.append(candidate)
        }

        if candidates.isEmpty {
            throw UsageError.missingCredentials
        }
        return candidates
    }

    private func readCredentialFromKeychain() -> ClaudeCredential? {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        process.arguments = ["find-generic-password", "-s", "Claude Code-credentials", "-w"]
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = Pipe()
        do {
            guard try ProcessDeadline.run(process, timeout: Self.keychainReadTimeout) else {
                // `security` can wait forever behind a locked keychain or an
                // unanswered access dialog. End this attempt so later refreshes
                // can still run; the file credential remains available below.
                return nil
            }
            guard process.terminationStatus == 0 else { return nil }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            guard
                let text = String(data: data, encoding: .utf8),
                let jsonData = text.trimmingCharacters(in: .whitespacesAndNewlines).data(using: .utf8),
                let json = try JSONSerialization.jsonObject(with: jsonData) as? [String: Any],
                let claudeOauth = json["claudeAiOauth"] as? [String: Any],
                let token = claudeOauth["accessToken"] as? String
            else {
                return nil
            }
            return ClaudeCredential(
                accessToken: token,
                plan: ClaudeUsageParser.plan(from: claudeOauth),
                expiresAt: CredentialExpiry.epochMillisecondsDate(claudeOauth["expiresAt"])
            )
        } catch {
            return nil
        }
    }

    private func readCredentialFromFile() -> ClaudeCredential? {
        guard
            let data = try? Data(contentsOf: AppPaths.claudeCredentials),
            let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let claudeOauth = json["claudeAiOauth"] as? [String: Any],
            let token = claudeOauth["accessToken"] as? String
        else {
            return nil
        }
        return ClaudeCredential(
            accessToken: token,
            plan: ClaudeUsageParser.plan(from: claudeOauth),
            expiresAt: CredentialExpiry.epochMillisecondsDate(claudeOauth["expiresAt"])
        )
    }
}

private struct ClaudeCredential {
    let accessToken: String
    let plan: String?
    let expiresAt: Date?
}

private struct TokenCandidate {
    let source: TokenSource
    let token: String
    let plan: String?
    let expiresAt: Date?

    init?(source: TokenSource, credential: ClaudeCredential) {
        guard !credential.accessToken.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            return nil
        }
        self.source = source
        self.token = credential.accessToken
        self.plan = credential.plan
        self.expiresAt = credential.expiresAt
    }

    var isExpired: Bool {
        CredentialExpiry.isExpired(expiresAt)
    }
}

private enum TokenSource {
    case keychain
    case credentialsFile
}
