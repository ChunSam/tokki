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

        if (claudeEnabled && !claude.IsAvailable && CanReuse(stale.Claude, now, tolerance))
        {
            claude = MarkStale(stale.Claude, claude.Error);
        }

        if (codexEnabled && !codex.IsAvailable && CanReuse(stale.Codex, now, tolerance))
        {
            codex = MarkStale(stale.Codex, codex.Error);
        }

        return new UsageSnapshot(claude, codex, current.UpdatedAt);
    }

    private static bool CanReuse(ProviderUsage usage, DateTimeOffset now, TimeSpan maxAge)
    {
        var age = now - usage.UpdatedAt;
        return usage.IsAvailable && usage.Source != UsageSource.Unavailable
            && age >= TimeSpan.Zero && age <= maxAge;
    }

    /// The snapshot as it may be shown. A balance only describes the window
    /// instance it was measured in; once that window's reset has passed it has
    /// refilled, so a carried number is not a low reading but a wrong one — and the
    /// fallback can last for hours, long enough to show a 5h window's last few
    /// percent, in the 7d warning colour even, well after both had reset. The
    /// alerts already skip such a window (WindowHasReset); this keeps the display
    /// in step. The number goes and the reset instant stays, so the menu says the
    /// window has reset rather than that it was never reported. A carried reading
    /// with no live window left is not shown at all. A window with no reset instant
    /// cannot be judged and is kept.
    ///
    /// Only for display: what Apply returns is what the cache keeps, numbers and
    /// all, because the freed-up alert seeds its "was low" reading from that file at
    /// launch. Hiding the number there too lost the alert for a window that ran low
    /// and reset while the app was restarted mid-outage. Measurements pass through
    /// untouched: the provider does not report a window whose reset has passed.
    public static UsageSnapshot WithoutLapsedWindows(UsageSnapshot snapshot, DateTimeOffset now) =>
        snapshot with
        {
            Claude = WithoutLapsedWindows(snapshot.Claude, now),
            Codex = WithoutLapsedWindows(snapshot.Codex, now)
        };

    private static ProviderUsage WithoutLapsedWindows(ProviderUsage usage, DateTimeOffset now)
    {
        if (usage.Source != UsageSource.StaleCache)
        {
            return usage;
        }

        var live = usage with
        {
            RemainingPercent5h = HasLapsed(usage.ResetAt5h, now) ? null : usage.RemainingPercent5h,
            RemainingPercent7d = HasLapsed(usage.ResetAt7d, now) ? null : usage.RemainingPercent7d
        };
        return live.IsAvailable
            ? live
            : new ProviderUsage(usage.Provider, null, null, null, null, UsageSource.Unavailable, usage.Error, null, null, now);
    }

    private static bool HasLapsed(DateTimeOffset? resetAt, DateTimeOffset now) =>
        resetAt is not null && resetAt.Value <= now;

    private static ProviderUsage MarkStale(ProviderUsage usage, string? error) =>
        usage with
        {
            Source = UsageSource.StaleCache,
            Error = error
        };
}
