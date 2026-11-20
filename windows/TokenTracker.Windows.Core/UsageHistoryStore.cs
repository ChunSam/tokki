using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace TokenTracker.Windows.Core;

public sealed record UsageHistoryEntry(DateTimeOffset RecordedAt, UsageSnapshot Snapshot);

public sealed class UsageHistoryStore
{
    private static readonly JsonSerializerOptions Options = new()
    {
        WriteIndented = true,
        Converters = { new JsonStringEnumConverter() }
    };

    /// The entries as last read or written, with the file's stamp at that moment.
    private CachedEntries? cached;

    public string Path { get; }

    /// How many times the file has been read and decoded, for the smoke tests.
    public int FileReads { get; private set; }

    public UsageHistoryStore(string? path = null)
    {
        Path = path ?? AppPaths.UsageHistoryPath;
    }

    /// <summary>
    /// Decoded once per change of the file. A refresh used to read it whole four
    /// times and every right-click four more — the append, the forecast per
    /// provider, the history submenu, the trend and the alerts each on its own —
    /// and the file grows with the retention setting, so a long history held the UI
    /// thread before the menu could open. The stamp (last write time and size)
    /// still lets a change made elsewhere be seen.
    /// </summary>
    public IReadOnlyList<UsageHistoryEntry> Load()
    {
        try
        {
            var info = new FileInfo(Path);
            if (!info.Exists)
            {
                cached = null;
                return Array.Empty<UsageHistoryEntry>();
            }

            if (cached is { } hit && hit.Modified == info.LastWriteTimeUtc && hit.Size == info.Length)
            {
                return hit.Entries;
            }

            // A read that fails throws to the catch below and is not remembered: the
            // file is there, so try again next time.
            var text = File.ReadAllText(Path);
            FileReads++;
            IReadOnlyList<UsageHistoryEntry> entries;
            try
            {
                entries = JsonSerializer.Deserialize<List<UsageHistoryEntry>>(text, Options)?
                    .OrderBy(entry => entry.RecordedAt)
                    .ToArray() ?? Array.Empty<UsageHistoryEntry>();
            }
            catch (JsonException)
            {
                entries = Array.Empty<UsageHistoryEntry>();
            }

            cached = new CachedEntries(info.LastWriteTimeUtc, info.Length, entries);
            return entries;
        }
        catch
        {
            return Array.Empty<UsageHistoryEntry>();
        }
    }

    private sealed record CachedEntries(DateTime Modified, long Size, IReadOnlyList<UsageHistoryEntry> Entries);

    public void Append(UsageSnapshot snapshot, int retentionDays, DateTimeOffset? now = null)
    {
        var current = now ?? DateTimeOffset.Now;
        var entries = Load().ToList();
        var newEntry = new UsageHistoryEntry(current, snapshot);

        if (entries.Count > 0 && current - entries[^1].RecordedAt < TimeSpan.FromMinutes(1))
        {
            entries[^1] = newEntry;
        }
        else
        {
            entries.Add(newEntry);
        }

        var cutoff = current.AddDays(-Math.Max(1, retentionDays));
        Save(entries.Where(entry => entry.RecordedAt >= cutoff).ToArray());
    }

    public string CsvString() => UsageHistoryFormatter.CsvString(Load());

    private void Save(IReadOnlyList<UsageHistoryEntry> entries)
    {
        try
        {
            var directory = System.IO.Path.GetDirectoryName(Path);
            if (!string.IsNullOrWhiteSpace(directory))
            {
                Directory.CreateDirectory(directory);
            }

            var tempPath = Path + ".tmp";
            File.WriteAllText(tempPath, JsonSerializer.Serialize(entries, Options));
            File.Move(tempPath, Path, overwrite: true);
            // What was just written is what a read would return, so the next Load
            // need not decode it again.
            var info = new FileInfo(Path);
            cached = new CachedEntries(info.LastWriteTimeUtc, info.Length, entries.OrderBy(entry => entry.RecordedAt).ToArray());
        }
        catch
        {
            cached = null;
        }
    }
}

public static class UsageHistoryPolicy
{
    /// <summary>
    /// History records measurements, not re-displays of one. A stale reading is the
    /// same measurement shown again, so recording it repeats a value the provider
    /// never re-reported — flattening the sparkline, zeroing the trend delta and
    /// telling the forecast that usage has stopped moving. Harmless while the
    /// fallback only covered a network blip; misleading now that it can stand in
    /// for half a day of a lapsed access token.
    ///
    /// The reading still shows in the tray; it just does not enter the record.
    /// </summary>
    public static UsageSnapshot MeasurementsOnly(UsageSnapshot snapshot) =>
        snapshot with
        {
            Claude = MeasurementOnly(snapshot.Claude),
            Codex = MeasurementOnly(snapshot.Codex)
        };

    // Money is not part of the record: every poll would copy the same balance.
    private static ProviderUsage MeasurementOnly(ProviderUsage usage)
    {
        var withoutMoney = usage with { ExtraUsage = null, CreditBalance = null, CreditLimitReached = null };
        return usage.Source == UsageSource.Api
            ? withoutMoney
            : withoutMoney with { RemainingPercent5h = null, RemainingPercent7d = null };
    }
}

public static class UsageHistoryFormatter
{
    public static string TrendSummary(
        IReadOnlyList<UsageHistoryEntry> entries,
        UsageSnapshot current,
        TimeSpan? window = null,
        Localizer? localizer = null)
    {
        localizer ??= new Localizer(AppLanguage.English);
        var cutoff = current.UpdatedAt - (window ?? TimeSpan.FromDays(1));
        var inWindow = entries.Where(entry => entry.RecordedAt >= cutoff).ToList();
        if (inWindow.Count == 0)
        {
            return localizer.Text(L10nKey.NotEnoughHistory);
        }

        return string.Join(
            " ",
            localizer.Text(L10nKey.HistoryTrend),
            ProviderTrend(Provider.Claude, inWindow, current),
            ProviderTrend(Provider.Codex, inWindow, current));
    }

    public static string CsvString(IReadOnlyList<UsageHistoryEntry> entries)
    {
        var header = string.Join(
            ",",
            "recorded_at",
            "provider",
            "remaining_5h",
            "remaining_7d",
            "reset_5h",
            "reset_7d",
            "source",
            "plan",
            "error");

        var rows = entries.SelectMany(entry => new[] { entry.Snapshot.Claude, entry.Snapshot.Codex }
            .Select(usage => string.Join(
                ",",
                new[]
                {
                    IsoString(entry.RecordedAt),
                    usage.Provider.ToId(),
                    OptionalInt(usage.RemainingPercent5h),
                    OptionalInt(usage.RemainingPercent7d),
                    OptionalDate(usage.ResetAt5h),
                    OptionalDate(usage.ResetAt7d),
                    usage.Source.ToString(),
                    usage.Plan ?? "",
                    usage.Error ?? ""
                }
                .Select(CsvEscape))));

        return string.Join(Environment.NewLine, new[] { header }.Concat(rows)) + Environment.NewLine;
    }

    private static string ProviderTrend(
        Provider provider,
        IReadOnlyList<UsageHistoryEntry> entries,
        UsageSnapshot current)
    {
        var currentUsage = current.Usage(provider);
        // Baseline per provider rather than one shared entry: entries recorded while
        // this provider was unavailable carry no percentages, and a shared baseline
        // landing on one of those would silently drop the delta for a provider that
        // does have older readings to compare against.
        var baselineUsage = entries
            .Select(entry => entry.Snapshot.Usage(provider))
            .FirstOrDefault(usage => usage.RemainingPercent5h is not null || usage.RemainingPercent7d is not null);
        var fiveHour = DeltaText(baselineUsage?.RemainingPercent5h, currentUsage.RemainingPercent5h);
        var sevenDay = DeltaText(baselineUsage?.RemainingPercent7d, currentUsage.RemainingPercent7d);
        return $"{currentUsage.DisplayName} 5h {fiveHour} 7d {sevenDay}";
    }

    private static string DeltaText(int? previous, int? latest)
    {
        if (previous is null || latest is null)
        {
            return "--";
        }

        var delta = latest.Value - previous.Value;
        return delta > 0 ? $"+{delta}%" : $"{delta}%";
    }

    private static string OptionalInt(int? value) =>
        value?.ToString(CultureInfo.InvariantCulture) ?? "";

    private static string OptionalDate(DateTimeOffset? date) =>
        date is null ? "" : IsoString(date.Value);

    private static string IsoString(DateTimeOffset date) =>
        date.ToUniversalTime().ToString("O", CultureInfo.InvariantCulture);

    private static string CsvEscape(string value)
    {
        if (value.Contains(',', StringComparison.Ordinal) ||
            value.Contains('"', StringComparison.Ordinal) ||
            value.Contains('\n', StringComparison.Ordinal))
        {
            return $"\"{value.Replace("\"", "\"\"", StringComparison.Ordinal)}\"";
        }

        return value;
    }

}
