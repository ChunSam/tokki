import Foundation
import Darwin

/// Runs a child process with a hard deadline. Foundation has no timed equivalent
/// of `waitUntilExit`, and a process behind an OS authorization dialog can otherwise
/// keep a refresh in progress forever.
package enum ProcessDeadline {
    package static func run(
        _ process: Process,
        timeout: TimeInterval,
        terminationGrace: TimeInterval = 1
    ) throws -> Bool {
        let exited = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in exited.signal() }
        try process.run()

        guard exited.wait(timeout: .now() + timeout) == .timedOut else {
            return true
        }

        process.terminate()
        if exited.wait(timeout: .now() + terminationGrace) == .timedOut {
            kill(process.processIdentifier, SIGKILL)
            _ = exited.wait(timeout: .now() + terminationGrace)
        }
        return false
    }
}
