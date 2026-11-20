import Foundation
import TokenTrackerCore

func runProcessDeadlineTests() throws {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: "/bin/sleep")
    process.arguments = ["30"]

    let started = Date()
    let completed = try ProcessDeadline.run(process, timeout: 0.05, terminationGrace: 0.5)
    expect(!completed, "a child beyond its deadline reports a timeout")
    expect(!process.isRunning, "a timed-out child is terminated")
    expect(Date().timeIntervalSince(started) < 1.5, "waiting for a child is bounded")
}
