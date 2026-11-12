"""Pure logic for the Linux port.

Mirrors windows/TokenTracker.Windows.Core file by file where a counterpart
exists, so the same rules hold on every platform. Everything that depends on the
clock takes `now`, so the tests can drive it without waiting.
"""
