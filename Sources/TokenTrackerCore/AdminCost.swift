import Foundation

// Stage 2 of docs/cost-display-design.md: what an organization's Admin API key
// says it has spent this month. Collection only — nothing here reaches the
// screen yet. Mirrors windows/TokenTracker.Windows.Core/AdminCost.cs.

/// This month's spend for one provider, as its Admin API last reported it.
public struct ProviderCost: Codable, Equatable, Sendable {
    public let provider: Provider
    /// From local midnight on the 1st. `nil` while nothing is tallied.
    public var monthToDate: CostAmount?
    /// From local midnight today. `nil` while today is not tallied yet, or when the
    /// provider's days do not start at local midnight.
    public var today: CostAmount?
    /// The month's largest models (Anthropic) or line items (OpenAI), at most three.
    public let topItems: [CostLineItem]
    /// Complete up to here: Anthropic's `data_refreshed_at`. `nil` for OpenAI, whose
    /// cost shows up within minutes, and while nothing is tallied.
    public let dataRefreshedAt: Date?
    public let measuredAt: Date

    public init(
        provider: Provider,
        monthToDate: CostAmount?,
        today: CostAmount?,
        topItems: [CostLineItem],
        dataRefreshedAt: Date?,
        measuredAt: Date
    ) {
        self.provider = provider
        self.monthToDate = monthToDate
        self.today = today
        self.topItems = topItems
        self.dataRefreshedAt = dataRefreshedAt
        self.measuredAt = measuredAt
    }
}

public struct CostFetchFailure: Equatable, Sendable {
    public enum Kind: String, Sendable {
        /// 401 or 403: a wrong, revoked or non-admin key. Fixed only by a new key.
        case keyRejected
        case rateLimited
        case failed
    }

    public let kind: Kind
    /// In English, for diagnostics.
    public let message: String

    public init(kind: Kind, message: String) {
        self.kind = kind
        self.message = message
    }
}

public enum CostFetchResult: Equatable, Sendable {
    case cost(ProviderCost)
    case failure(CostFetchFailure)
}

/// What each Admin API is asked. Brackets in parameter names go percent-encoded,
/// as the providers' own SDKs send them.
public enum AdminCostRequest {
    static let anthropicEndpoint = "https://api.anthropic.com/v1/organizations/analytics/cost"
    static let openAIEndpoint = "https://api.openai.com/v1/organization/costs"

    /// Hourly buckets, for this month's and today's totals. `nil` while the range
    /// is under an hour once both ends are rounded down to the hour, as the API
    /// rounds them: the first hour of a month has nothing to ask for yet.
    public static func anthropicHourly(range: DateInterval, page: String?) -> URL? {
        guard coversAnHour(range) else { return nil }
        return url(anthropicEndpoint, [
            ("starting_at", isoString(range.start)),
            ("ending_at", isoString(range.end)),
            ("time_bucket_width", "1h"),
            ("limit", "1000")
        ], page: page)
    }

    /// One row per model across the whole range, for the split.
    public static func anthropicByModel(range: DateInterval, page: String?) -> URL? {
        guard coversAnHour(range) else { return nil }
        return url(anthropicEndpoint, [
            ("starting_at", isoString(range.start)),
            ("ending_at", isoString(range.end)),
            ("group_by%5B%5D", "model"),
            ("limit", "100")
        ], page: page)
    }

    /// Day buckets, split by line item. `limit` counts days, so a month fits in one
    /// page unless the days are cut at UTC midnight and the 1st spills into a 32nd.
    public static func openAI(range: DateInterval, page: String?) -> URL? {
        let start = Int(range.start.timeIntervalSince1970)
        let end = Int(range.end.timeIntervalSince1970)
        guard end > start else { return nil }
        return url(openAIEndpoint, [
            ("start_time", String(start)),
            ("end_time", String(end)),
            ("bucket_width", "1d"),
            ("group_by%5B%5D", "line_item"),
            ("limit", "31")
        ], page: page)
    }

    public static func headers(for provider: Provider, key: String) -> [String: String] {
        switch provider {
        case .claude:
            return [
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "analytics-usage-cost-2026-08-24",
                "Accept": "application/json"
            ]
        case .codex:
            return [
                "Authorization": "Bearer \(key)",
                "Accept": "application/json"
            ]
        }
    }

    private static func coversAnHour(_ range: DateInterval) -> Bool {
        floor(range.end.timeIntervalSince1970 / 3600) > floor(range.start.timeIntervalSince1970 / 3600)
    }

    private static func url(_ endpoint: String, _ query: [(String, String)], page: String?) -> URL? {
        var pairs = query
        if let page {
            pairs.append(("page", page.addingPercentEncoding(withAllowedCharacters: unreserved) ?? page))
        }
        return URL(string: endpoint + "?" + pairs.map { "\($0.0)=\($0.1)" }.joined(separator: "&"))
    }

    private static let unreserved = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")

    private static func isoString(_ date: Date) -> String {
        ISO8601DateFormatter().string(from: date)
    }
}

/// Asks one provider's Admin API for this month's cost, every page of it.
public struct AdminCostClient: Sendable {
    public let provider: Provider
    private let http: HTTPClient
    private let rateLimitState: RateLimitState
    /// More pages than a month can fill: the cursor is going round in circles.
    static let maxPages = 10
    private static let defaultRateLimitCooldown: TimeInterval = 300

    package init(provider: Provider, http: HTTPClient = HTTPClient(), rateLimitStore: RateLimitStore? = nil) {
        self.provider = provider
        self.http = http
        self.rateLimitState = RateLimitState(store: rateLimitStore ?? RateLimitStore(url: AppPaths.adminCostRateLimit(provider)))
    }

    var serviceName: String {
        switch provider {
        case .claude: "Anthropic Admin API"
        case .codex: "OpenAI Admin API"
        }
    }

    public func fetch(key: String, now: Date, timeZone: TimeZone) async -> CostFetchResult {
        let fingerprint = TokenFingerprint.of([key])
        if let error = await rateLimitState.currentError(serviceName: serviceName, fingerprint: fingerprint) {
            return .failure(CostFetchFailure(kind: .rateLimited, message: error.localizedDescription))
        }

        do {
            let cost = try await fetchCost(key: key, now: now, timeZone: timeZone)
            await rateLimitState.clear()
            return .cost(cost)
        } catch let error as UsageError {
            if let retryAfter = error.rateLimitRetryAfter ?? headerlessRateLimitCooldown(error) {
                await rateLimitState.backOff(for: retryAfter, fingerprint: fingerprint)
                let current = await rateLimitState.currentError(serviceName: serviceName, fingerprint: fingerprint)
                return .failure(CostFetchFailure(kind: .rateLimited, message: current?.localizedDescription ?? error.localizedDescription))
            }
            return .failure(CostFetchFailure(
                kind: error.isAuthenticationFailure ? .keyRejected : .failed,
                message: error.localizedDescription
            ))
        } catch {
            return .failure(CostFetchFailure(kind: .failed, message: error.localizedDescription))
        }
    }

    /// A 429 that carried no `Retry-After` still needs a cooldown, just a guessed one.
    private func headerlessRateLimitCooldown(_ error: UsageError) -> TimeInterval? {
        if case .httpStatus(429, _, nil) = error { return Self.defaultRateLimitCooldown }
        return nil
    }

    private func fetchCost(key: String, now: Date, timeZone: TimeZone) async throws -> ProviderCost {
        let range = CostPeriod.requestRange(now: now, timeZone: timeZone)
        let headers = AdminCostRequest.headers(for: provider, key: key)
        let totals: CostPage
        switch provider {
        case .claude:
            totals = try await allPages(headers: headers, parse: AnthropicCostParser.page) {
                AdminCostRequest.anthropicHourly(range: range, page: $0)
            }
        case .codex:
            totals = try await allPages(headers: headers, parse: OpenAICostParser.page) {
                AdminCostRequest.openAI(range: range, page: $0)
            }
        }
        let report = CostAggregator.report(buckets: totals.buckets, freshness: totals.freshness, now: now, timeZone: timeZone)

        // Anthropic splits by model in a query of its own, worth asking only once
        // the month has something in it.
        // OpenAI splits each model into input, output and so on; they add up
        // under the model.
        var items = totals.items.map { CostLineItem(name: CostItemName.lineItemGroup($0.name), amount: $0.amount) }
        if provider == .claude {
            items = []
            if report.monthToDate != nil {
                items = try await allPages(headers: headers, parse: AnthropicCostParser.page) {
                    AdminCostRequest.anthropicByModel(range: range, page: $0)
                }.items
            }
        }

        var refreshedAt: Date?
        if case .refreshedAt(let date) = report.freshness {
            refreshedAt = date
        }
        return ProviderCost(
            provider: provider,
            monthToDate: report.monthToDate,
            today: report.today,
            topItems: CostLineItem.top(items),
            dataRefreshedAt: refreshedAt,
            measuredAt: now
        )
    }

    /// Every page of one query, joined. The pages share one snapshot of the data, so
    /// the first page's freshness speaks for all of them. No first URL means there
    /// is nothing to ask for yet.
    private func allPages(
        headers: [String: String],
        parse: ([String: Any]) -> CostPage?,
        url: (String?) -> URL?
    ) async throws -> CostPage {
        guard var next = url(nil) else {
            return CostPage(buckets: [], items: [], nextPage: nil, freshness: .notTallied)
        }
        var buckets: [CostBucket] = []
        var items: [CostLineItem] = []
        var freshness: CostFreshness?
        for _ in 0..<Self.maxPages {
            let raw = try await http.getJSON(url: next, headers: headers, timeout: 15, serviceName: serviceName)
            guard let object = raw as? [String: Any], let page = parse(object) else {
                throw UsageError.invalidResponse
            }
            buckets += page.buckets
            items += page.items
            freshness = freshness ?? page.freshness
            guard let cursor = page.nextPage else {
                return CostPage(buckets: buckets, items: items, nextPage: nil, freshness: freshness ?? .notTallied)
            }
            guard let following = url(cursor) else { throw UsageError.invalidResponse }
            next = following
        }
        // A partial month would read as a smaller bill, so it is no reading at all.
        throw UsageError.invalidResponse
    }
}

public enum CostCachePolicy {
    /// What still holds of an earlier reading. Money spent stays spent, so the month
    /// stands until the local month turns; today stands until the local date does.
    public static func usable(_ cost: ProviderCost, now: Date, timeZone: TimeZone) -> ProviderCost? {
        guard CostPeriod.monthStart(now: cost.measuredAt, timeZone: timeZone) == CostPeriod.monthStart(now: now, timeZone: timeZone) else {
            return nil
        }
        var kept = cost
        if CostPeriod.dayStart(now: cost.measuredAt, timeZone: timeZone) != CostPeriod.dayStart(now: now, timeZone: timeZone) {
            kept.today = nil
        }
        return kept
    }
}

/// The last good reading per provider, so a relaunch or a failed fetch still has
/// one. Each is tied to the key that fetched it, by fingerprint, never the key.
package struct CostCacheStore: Sendable {
    package struct Entry: Codable, Equatable, Sendable {
        package let keyFingerprint: String
        package let cost: ProviderCost

        package init(keyFingerprint: String, cost: ProviderCost) {
            self.keyFingerprint = keyFingerprint
            self.cost = cost
        }
    }

    private struct Record: Codable {
        let version: Int
        let entries: [Entry]
    }

    private let url: URL

    package init(url: URL) {
        self.url = url
    }

    init() {
        self.init(url: AppPaths.costCache)
    }

    package func load() -> [Entry] {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard
            let data = try? Data(contentsOf: url),
            let record = try? decoder.decode(Record.self, from: data),
            record.version == 1
        else {
            return []
        }
        return record.entries
    }

    package func save(_ entries: [Entry]) {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        do {
            try FileManager.default.createDirectory(
                at: url.deletingLastPathComponent(),
                withIntermediateDirectories: true,
                attributes: [.posixPermissions: 0o700]
            )
            try encoder.encode(Record(version: 1, entries: entries)).write(to: url, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
        } catch {
        }
    }
}

/// One provider's Admin API cost as the app holds it between fetches.
public struct CostState: Equatable, Sendable {
    /// Whether the user switched API cost on for this provider.
    public var enabled = false
    /// `…ab12` for the stored key; `nil` with no key.
    public var keySuffix: String?
    /// The last good reading, carried through later failures for as long as it holds.
    public var cost: ProviderCost?
    /// Why the last fetch failed; `nil` after a good one.
    public var failure: CostFetchFailure?
    public var lastAttemptAt: Date?
    public var nextFetchAt: Date?
    /// The key the reading and schedule belong to. A different key starts over.
    var keyFingerprint: String?

    public init() {}

    /// The reading is an earlier one, standing in for a fetch that failed.
    public var isCached: Bool {
        cost != nil && failure != nil
    }
}

/// Fetches each provider's month of cost on its own schedule, apart from the usage
/// poll, and keeps the last good reading through failures.
public actor AdminCostService {
    private var states: [Provider: CostState] = [:]
    private let keyStore: any AdminKeyStoring
    private let clients: [Provider: AdminCostClient]
    private let cacheStore: CostCacheStore
    private var cacheLoaded = false

    #if canImport(Security)
    public init() {
        self.init(
            keyStore: KeychainAdminKeyStore(allowAuthenticationUI: false),
            clients: [.claude: AdminCostClient(provider: .claude), .codex: AdminCostClient(provider: .codex)],
            cacheStore: CostCacheStore()
        )
    }
    #endif

    package init(keyStore: any AdminKeyStoring, clients: [Provider: AdminCostClient], cacheStore: CostCacheStore) {
        self.keyStore = keyStore
        self.clients = clients
        self.cacheStore = cacheStore
    }

    /// Fetches each switched-on provider whose turn has come, and returns where
    /// every provider stands. A key is read only for a provider that is switched
    /// on, so a user who never turns this on never touches the keychain. The zone
    /// follows the Mac's, so a change of zone moves the next fetch's boundaries.
    public func refreshIfDue(enabled: [Provider: Bool], now: Date = Date(), timeZone: TimeZone = .autoupdatingCurrent) async -> [Provider: CostState] {
        loadCacheIfNeeded(now: now, timeZone: timeZone)
        for provider in Provider.allCases {
            await refresh(provider, enabled: enabled[provider] ?? false, now: now, timeZone: timeZone)
        }
        return states
    }

    private func refresh(_ provider: Provider, enabled: Bool, now: Date, timeZone: TimeZone) async {
        var state = states[provider] ?? CostState()
        state.cost = state.cost.flatMap { CostCachePolicy.usable($0, now: now, timeZone: timeZone) }
        guard enabled else {
            // Off forgets the schedule, so switching back on fetches at once.
            var off = CostState()
            off.cost = state.cost
            off.keyFingerprint = state.keyFingerprint
            states[provider] = off
            return
        }
        state.enabled = true

        guard let key = keyStore.key(for: provider) else {
            state.keySuffix = nil
            state.failure = nil
            state.nextFetchAt = nil
            states[provider] = state
            return
        }
        let fingerprint = TokenFingerprint.of([key])
        if state.keyFingerprint != fingerprint {
            // Another key may be another organization: its reading, failures and
            // schedule were never this key's.
            state = CostState()
            state.enabled = true
            state.keyFingerprint = fingerprint
        }
        state.keySuffix = AdminKeyText.suffix(key)

        guard CostFetchPolicy.isDue(nextFetchAt: state.nextFetchAt, now: now), let client = clients[provider] else {
            states[provider] = state
            return
        }
        let result = await client.fetch(key: key, now: now, timeZone: timeZone)
        state.lastAttemptAt = now
        switch result {
        case .cost(let cost):
            state.cost = cost
            state.failure = nil
            state.nextFetchAt = CostFetchPolicy.nextFetch(after: now, succeeded: true)
        case .failure(let failure):
            state.failure = failure
            state.nextFetchAt = CostFetchPolicy.nextFetch(after: now, succeeded: false)
        }
        states[provider] = state
        saveCache()
    }

    private func loadCacheIfNeeded(now: Date, timeZone: TimeZone) {
        guard !cacheLoaded else { return }
        cacheLoaded = true
        for entry in cacheStore.load() {
            guard let cost = CostCachePolicy.usable(entry.cost, now: now, timeZone: timeZone) else { continue }
            var state = CostState()
            state.cost = cost
            state.keyFingerprint = entry.keyFingerprint
            states[cost.provider] = state
        }
    }

    private func saveCache() {
        let entries = Provider.allCases.compactMap { provider -> CostCacheStore.Entry? in
            guard let state = states[provider], let cost = state.cost, let fingerprint = state.keyFingerprint else { return nil }
            return CostCacheStore.Entry(keyFingerprint: fingerprint, cost: cost)
        }
        cacheStore.save(entries)
    }
}

public enum CostDiagnostics {
    /// What diagnostics says about one provider's API cost. Never the key: its last
    /// four characters at most.
    public static func lines(provider: Provider, state: CostState) -> [String] {
        let name = "\(provider.displayName) API cost"
        guard state.enabled else { return ["\(name): off"] }
        guard let suffix = state.keySuffix else { return ["\(name): on, no Admin key"] }

        var lines = ["\(name): on, key \(suffix)"]
        if let cost = state.cost {
            lines.append("\(name) month: \(cost.monthToDate.map(CostFormatter.text) ?? "not tallied")")
            lines.append("\(name) today: \(cost.today.map(CostFormatter.text) ?? "--")")
            if let refreshed = cost.dataRefreshedAt {
                lines.append("\(name) complete up to: \(isoString(refreshed))")
            }
            if !cost.topItems.isEmpty {
                lines.append("\(name) top: " + cost.topItems.map { "\($0.name) \(CostFormatter.text($0.amount))" }.joined(separator: ", "))
            }
            lines.append("\(name) measured: \(isoString(cost.measuredAt))")
        }
        let outcome = state.failure.map { "\($0.kind.rawValue): \($0.message)" } ?? "ok"
        lines.append("\(name) last fetch: \(state.lastAttemptAt.map { "\(isoString($0)) \(outcome)" } ?? "none")")
        return lines
    }

    private static func isoString(_ date: Date) -> String {
        ISO8601DateFormatter().string(from: date)
    }
}
