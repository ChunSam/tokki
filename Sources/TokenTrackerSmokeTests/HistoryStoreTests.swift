import Foundation
import TokenTrackerCore

/// The history file is decoded once per change, not once per reader: one refresh
/// used to read it whole five times. Same truth table as the Windows and Linux
/// tests.
func runHistoryStoreTests() throws {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    defer { try? FileManager.default.removeItem(at: directory) }
    let url = directory.appendingPathComponent("usage-history.json")
    let now = Date(timeIntervalSince1970: floor(Date().timeIntervalSince1970))
    func snapshot(_ remaining: Int) -> UsageSnapshot {
        func usage(_ provider: Provider) -> ProviderUsage {
            ProviderUsage(provider: provider, remainingPercent5h: remaining, remainingPercent7d: 80,
                          resetAt5h: nil, resetAt7d: nil, source: .api, error: nil, plan: nil,
                          model: nil, updatedAt: now)
        }
        return UsageSnapshot(claude: usage(.claude), codex: usage(.codex), updatedAt: now)
    }

    let store = UsageHistoryStore(url: url)
    expectEqual(store.load().count, 0, "No file is an empty history")
    store.append(snapshot(60), retentionDays: 7, now: now)
    store.append(snapshot(50), retentionDays: 7, now: now.addingTimeInterval(3600))
    for _ in 0..<5 {
        expectEqual(store.load().count, 2, "Every reader sees what was written")
    }
    expectEqual(store.fileReads, 0, "What the store wrote itself is never read back")
    expectEqual(store.load().last?.snapshot.claude.remainingPercent5h, 50, "The last append is the last entry")

    let other = UsageHistoryStore(url: url)
    expectEqual(other.load().count, 2, "Another store reads the file")
    _ = other.load()
    _ = other.csvString()
    expectEqual(other.fileReads, 1, "And decodes it once while it stays unchanged")

    // A change made elsewhere is seen: the stamp moved.
    other.append(snapshot(40), retentionDays: 7, now: now.addingTimeInterval(7200))
    expectEqual(store.load().count, 3, "A write by another store is picked up")
    expectEqual(store.fileReads, 1, "By reading the file once more")
    expectEqual(store.load().last?.snapshot.claude.remainingPercent5h, 40, "With its new entry")

    try FileManager.default.removeItem(at: url)
    expectEqual(store.load().count, 0, "A deleted file is an empty history, not the remembered one")
    try Data("not json".utf8).write(to: url)
    expectEqual(store.load().count, 0, "A corrupt file reads as empty")
    _ = store.load()
    expectEqual(store.fileReads, 2, "And is not decoded again until it changes")
    store.append(snapshot(30), retentionDays: 7, now: now.addingTimeInterval(10800))
    expectEqual(UsageHistoryStore(url: url).load().count, 1, "Appending over a corrupt file starts a fresh history")
}
