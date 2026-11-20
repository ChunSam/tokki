using System.Text.Json;
using System.Text.Json.Serialization;

namespace TokenTracker.Windows.Core;

public sealed class CacheStore
{
    // Older caches overwrote measurement timestamps, so their age is unknowable.
    private sealed record Record(int Version, UsageSnapshot Snapshot);

    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        Converters = { new JsonStringEnumConverter() }
    };

    public string Path { get; }

    public CacheStore(string? path = null)
    {
        Path = path ?? AppPaths.SnapshotCachePath;
    }

    public UsageSnapshot? Load(TimeSpan maxAge)
    {
        try
        {
            if (!File.Exists(Path))
            {
                return null;
            }

            var record = JsonSerializer.Deserialize<Record>(File.ReadAllText(Path), Options);
            if (record is not { Version: 1, Snapshot: not null }
                || DateTimeOffset.Now - record.Snapshot.UpdatedAt > maxAge)
            {
                return null;
            }

            return record.Snapshot;
        }
        catch
        {
            return null;
        }
    }

    public void Save(UsageSnapshot snapshot)
    {
        try
        {
            var directory = System.IO.Path.GetDirectoryName(Path);
            if (!string.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }

            var tempPath = Path + ".tmp";
            File.WriteAllText(tempPath, JsonSerializer.Serialize(new Record(1, snapshot), Options));
            File.Move(tempPath, Path, overwrite: true);
        }
        catch
        {
        }
    }
}

public static class UsageSnapshotCachePolicy
{
    public static UsageSnapshot Apply(
        UsageSnapshot current,
        UsageSnapshot? stale,
        bool claudeEnabled = true,
        bool codexEnabled = true,
        DateTimeOffset? updatedAt = null,
        TimeSpan? maxAge = null)
    {
        if (stale is null)
        {
            return current;
        }

        var now = updatedAt ?? DateTimeOffset.Now;
        var tolerance = maxAge ?? TimeSpan.FromHours(12);
        var claude = current.Claude;
        var codex = current.Codex;

        if (claudeEnabled && !claude.IsAvailable && Reusable(stale.Claude, now, tolerance) is { } reusableClaude)
        {
            claude = MarkStale(reusableClaude, claude.Error);
        }

        if (codexEnabled && !codex.IsAvailable && Reusable(stale.Codex, now, tolerance) is { } reusableCodex)
        {
            codex = MarkStale(reusableCodex, codex.Error);
        }

        return new UsageSnapshot(claude, codex, current.UpdatedAt);
    }

    /// What of a last good reading may still stand in, or null when nothing may.
    private static ProviderUsage? Reusable(ProviderUsage usage, DateTimeOffset now, TimeSpan maxAge)
    {
        var age = now - usage.UpdatedAt;
        if (usage.Source == UsageSource.Unavailable || age < TimeSpan.Zero || age > maxAge)
        {
            return null;
        }

        var live = WithoutLapsedWindows(usage, now);
        return live.IsAvailable ? live : null;
    }

    /// A balance only describes the window instance it was measured in. Once that
    /// window's reset has passed it has refilled, so the cached number is not a low
    /// reading but a wrong one — and the fallback can last for hours, long enough
    /// to show a 5h window's last few percent, in the 7d warning colour even, well
    /// after both had reset. The alerts already skip such a window
    /// (WindowHasReset); this keeps the display in step. The number goes and the
    /// reset instant stays, so the menu says the window has reset rather than that
    /// it was never reported. A window with no reset instant cannot be judged and
    /// is kept.
    private static ProviderUsage WithoutLapsedWindows(ProviderUsage usage, DateTimeOffset now) =>
        usage with
        {
            RemainingPercent5h = HasLapsed(usage.ResetAt5h, now) ? null : usage.RemainingPercent5h,
            RemainingPercent7d = HasLapsed(usage.ResetAt7d, now) ? null : usage.RemainingPercent7d
        };

    private static bool HasLapsed(DateTimeOffset? resetAt, DateTimeOffset now) =>
        resetAt is not null && resetAt.Value <= now;

    private static ProviderUsage MarkStale(ProviderUsage usage, string? error) =>
        usage with
        {
            Source = UsageSource.StaleCache,
            Error = error
        };
}
