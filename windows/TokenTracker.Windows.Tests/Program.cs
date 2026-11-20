using System.Diagnostics;
using System.Globalization;
using System.Net;
using TokenTracker.Windows.Core;

static void ExpectEqual<T>(T actual, T expected, string message)
{
    if (!EqualityComparer<T>.Default.Equals(actual, expected))
    {
        throw new InvalidOperationException($"{message}. Expected {expected}, got {actual}");
    }
}

static void Expect(bool condition, string message)
{
    if (!condition)
    {
        throw new InvalidOperationException(message);
    }
}

var now = new DateTimeOffset(2026, 5, 27, 0, 0, 0, TimeSpan.Zero);

ExpectEqual(PercentMath.RemainingPercent(0), 100, "0 used leaves 100 remaining");
ExpectEqual(PercentMath.RemainingPercent(25.4), 75, "25.4 used rounds to 75 remaining");
ExpectEqual(PercentMath.RemainingPercent(100), 0, "100 used leaves 0 remaining");
ExpectEqual(PercentMath.RemainingPercent(120), 0, "remaining is clamped at 0");

var healthySevenDay = Usage(
    Provider.Claude,
    remainingPercent5h: 100,
    remainingPercent7d: 90,
    now);
ExpectEqual(DisplayFormatter.DisplayPercent(healthySevenDay), 100, "healthy 7d does not override 5h");
Expect(!DisplayFormatter.IsSevenDayWarning(healthySevenDay), "healthy 7d is not highlighted");

var thresholdSevenDay = Usage(
    Provider.Claude,
    remainingPercent5h: 100,
    remainingPercent7d: 10,
    now);
ExpectEqual(DisplayFormatter.DisplayPercent(thresholdSevenDay), 10, "7d threshold overrides 5h");
Expect(DisplayFormatter.IsSevenDayWarning(thresholdSevenDay), "7d threshold is highlighted");

var missingFiveHour = Usage(
    Provider.Claude,
    remainingPercent5h: null,
    remainingPercent7d: 42,
    now);
ExpectEqual(DisplayFormatter.DisplayPercent(missingFiveHour), 42, "7d is used when 5h is missing");
Expect(!DisplayFormatter.IsSevenDayWarning(missingFiveHour), "healthy 7d fallback is not highlighted when 5h is missing");

var missingBothWindows = Usage(Provider.Codex, null, null, now);
ExpectEqual(DisplayFormatter.PreferredForecastWindow(Usage(Provider.Claude, 73, null, now)), ForecastWindow.FiveHour, "5h window is preferred when it reports");
ExpectEqual(DisplayFormatter.PreferredForecastWindow(missingFiveHour), ForecastWindow.SevenDay, "forecast surfaces fall back to 7d when 5h is missing");
ExpectEqual(DisplayFormatter.PreferredForecastWindow(missingBothWindows), ForecastWindow.FiveHour, "default window is 5h when neither reports");

var englishReset = new Localizer(AppLanguage.English);
var koreanReset = new Localizer(AppLanguage.Korean);
ExpectEqual(
    DisplayFormatter.FormatWindowReset(63, DateTimeOffset.Now.AddMinutes(42).AddSeconds(30), englishReset),
    "42m",
    "a window with a reset still names the time");
ExpectEqual(
    DisplayFormatter.FormatWindowReset(100, null, englishReset),
    "none yet (opens on first use)",
    "a balance with no reset means the window has not opened yet");
ExpectEqual(
    DisplayFormatter.FormatWindowReset(100, null, koreanReset),
    "아직 없음(첫 사용 시 시작)",
    "the unopened window reads in Korean too");
ExpectEqual(
    DisplayFormatter.FormatWindowReset(null, null, englishReset),
    "--",
    "with no balance either there is nothing to claim about the window");
ExpectEqual(
    DisplayFormatter.FormatWindowReset(55, DateTimeOffset.Now.AddMinutes(-1), koreanReset),
    "지금",
    "A reset that has just passed reads in the chosen language");
ExpectEqual(
    DisplayFormatter.FormatWindowReset(null, null, englishReset, providerReported: true),
    "not reported",
    "A provider that answered without this window did not fail to read it");
ExpectEqual(
    DisplayFormatter.FormatWindowReset(null, null, koreanReset, providerReported: true),
    "제공되지 않음",
    "The unreported window reads in Korean too");
ExpectEqual(
    DisplayFormatter.FormatWindowReset(100, null, englishReset, providerReported: true),
    "none yet (opens on first use)",
    "A window with a balance is unopened, not unreported");

var snapshot = new UsageSnapshot(
    Claude: Usage(Provider.Claude, 63, 80, now),
    Codex: Usage(Provider.Codex, 91, 99, now, plan: "plus"),
    UpdatedAt: now);
ExpectEqual(DisplayFormatter.StatusTitle(snapshot, DisplayMode.Both), "Cdx 91% · Cl 63%", "both display mode");
ExpectEqual(DisplayFormatter.StatusTitle(snapshot, DisplayMode.CodexOnly), "Cdx 91%", "codex display mode");
ExpectEqual(DisplayFormatter.StatusTitle(snapshot, DisplayMode.ClaudeOnly), "Cl 63%", "claude display mode");
ExpectEqual(DisplayFormatter.StatusTitle(snapshot, DisplayMode.LowestRemaining), "AI 63%", "lowest display mode");
ExpectEqual(DisplayFormatter.Tooltip(snapshot, ProviderLabelStyle.Abbreviation), "Cdx 91%, Cl 63%", "abbreviated tooltip");
ExpectEqual(DisplayFormatter.Tooltip(snapshot, ProviderLabelStyle.Icon), "Codex 91%, Claude 63%", "named tooltip");

// The tooltip's countdown, on the only text surface a Windows tray has. Same
// machinery and the same truth table as the menu bar app's.
var countdownNow = DateTimeOffset.Now;
var englishCountdown = new Localizer(AppLanguage.English);
var fiveHourLead = new ProviderUsage(
    Provider.Claude, 63, 80, countdownNow.AddSeconds(8010), countdownNow.AddSeconds(273900),
    UsageSource.Api, null, null, null, countdownNow);
var sevenDayLead = new ProviderUsage(
    Provider.Claude, 100, 8, countdownNow.AddSeconds(8010), countdownNow.AddSeconds(273900),
    UsageSource.Api, null, null, null, countdownNow);

ExpectEqual(DisplayFormatter.FormatResetCompact(countdownNow.AddSeconds(2550), englishCountdown), "42m", "Under an hour shows minutes only");
ExpectEqual(DisplayFormatter.FormatResetCompact(countdownNow.AddSeconds(8010), englishCountdown), "2h13m", "Hours and minutes drop the inner space");
ExpectEqual(DisplayFormatter.FormatResetCompact(countdownNow.AddSeconds(273900), englishCountdown), "3d4h", "Over a day shows days and hours");
ExpectEqual(DisplayFormatter.FormatResetCompact(countdownNow.AddSeconds(-60), englishCountdown), "now", "A passed reset reads as now");
Expect(DisplayFormatter.FormatResetCompact(null, englishCountdown) is null, "No reset instant yields no countdown at all");

ExpectEqual(DisplayFormatter.DisplayWindow(fiveHourLead), ForecastWindow.FiveHour, "A healthy 7d leaves the 5h window on display");
ExpectEqual(DisplayFormatter.DisplayResetAt(fiveHourLead), fiveHourLead.ResetAt5h, "The countdown follows the displayed window");
ExpectEqual(DisplayFormatter.DisplayWindow(sevenDayLead), ForecastWindow.SevenDay, "A low 7d takes over the display");
ExpectEqual(DisplayFormatter.DisplayResetAt(sevenDayLead), sevenDayLead.ResetAt7d, "Taking over the percent takes over the countdown too");

ExpectEqual(DisplayFormatter.ResetCountdownSuffix(fiveHourLead, true, englishCountdown), " 2h13m", "A live reading still shows its countdown");
ExpectEqual(DisplayFormatter.ResetCountdownSuffix(fiveHourLead, false, englishCountdown), "", "The countdown can be turned off");
ExpectEqual(
    DisplayFormatter.ResetCountdownSuffix(fiveHourLead with { Source = UsageSource.StaleCache }, true, englishCountdown),
    "",
    "A cached reading shows its percentage but no countdown");

var countdownSnapshot = new UsageSnapshot(
    Claude: fiveHourLead,
    Codex: new ProviderUsage(
        Provider.Codex, 91, 99, countdownNow.AddSeconds(2550), null,
        UsageSource.Api, null, null, null, countdownNow),
    UpdatedAt: countdownNow);
ExpectEqual(
    DisplayFormatter.Tooltip(countdownSnapshot, ProviderLabelStyle.Abbreviation, true, englishCountdown),
    "Cdx 91% 42m, Cl 63% 2h13m",
    "The tooltip pairs each percentage with its own window's countdown");
ExpectEqual(
    DisplayFormatter.Tooltip(countdownSnapshot, ProviderLabelStyle.Abbreviation),
    "Cdx 91%, Cl 63%",
    "The tooltip is unchanged while the countdown is off");

var unavailableSnapshot = new UsageSnapshot(
    Claude: ProviderUsage.Unavailable(Provider.Claude, "Disabled"),
    Codex: ProviderUsage.Unavailable(Provider.Codex, "HTTP request failed"),
    UpdatedAt: now);
ExpectEqual(DisplayFormatter.StatusTitle(unavailableSnapshot, DisplayMode.Both), "Cdx -- · Cl --", "unavailable providers display dashes");
ExpectEqual(DisplayFormatter.Tooltip(unavailableSnapshot), "Codex --, Claude --", "unavailable tooltip displays dashes");

var codexJson = """
{
  "plan_type": "prolite",
  "rate_limit": {
    "primary_window": { "used_percent": 24.2, "reset_at": 1770000000 },
    "secondary_window": { "used_percent": 98.0, "reset_at": 1770500000 }
  }
}
""";
var codexUsage = UsageParser.ParseCodexUsage(codexJson, now);
ExpectEqual(codexUsage.RemainingPercent5h, 76, "Codex primary remaining percent");
ExpectEqual(codexUsage.RemainingPercent7d, 2, "Codex secondary remaining percent");
ExpectEqual(codexUsage.Plan, "prolite", "Codex plan");

// Since 2026-07 the API can report the weekly window as primary_window (the 5h
// window was removed); windows carrying limit_window_seconds map by length.
var codexWeeklyOnlyJson = """
{
  "plan_type": "plus",
  "rate_limit": {
    "primary_window": { "used_percent": 4, "limit_window_seconds": 604800, "reset_at": 1785331564 }
  }
}
""";
var codexWeeklyOnly = UsageParser.ParseCodexUsage(codexWeeklyOnlyJson, now);
ExpectEqual(codexWeeklyOnly.RemainingPercent5h, null, "a weekly primary window leaves the 5h lane empty");
ExpectEqual(codexWeeklyOnly.RemainingPercent7d, 96, "a weekly primary window fills the 7d lane");
ExpectEqual(codexWeeklyOnly.ResetAt5h, null, "no 5h reset when the 5h window is absent");
ExpectEqual(codexWeeklyOnly.ResetAt7d, DateTimeOffset.FromUnixTimeSeconds(1785331564), "the weekly reset lands in the 7d lane");
ExpectEqual(codexWeeklyOnly.Plan, "plus", "the plan is preserved");

var swappedWindows = CodexWindowMapper.Map(
    new CodexRateWindow(20, null, 604800),
    new CodexRateWindow(10, null, 18000));
ExpectEqual(swappedWindows.FiveHour?.UsedPercent, 10, "an 18000s window maps to the 5h lane regardless of position");
ExpectEqual(swappedWindows.SevenDay?.UsedPercent, 20, "a 604800s window maps to the 7d lane regardless of position");

var collidingWindows = CodexWindowMapper.Map(
    new CodexRateWindow(20, null, 604800),
    new CodexRateWindow(30, null, 604800));
ExpectEqual(collidingWindows.SevenDay?.UsedPercent, 20, "the first window wins a lane collision");
Expect(collidingWindows.FiveHour is null, "a colliding window is dropped rather than mislabeled");

var claudeJson = """
{
  "plan_type": "max",
  "five_hour": { "utilization": 15.0, "resets_at": "2026-05-27T12:00:00Z" },
  "seven_day": { "utilization": 100.0, "resets_at": "2026-05-29T12:00:00Z" }
}
""";
var claudeUsage = UsageParser.ParseClaudeUsage(claudeJson, now);
ExpectEqual(claudeUsage.RemainingPercent5h, 85, "Claude five hour remaining percent");
ExpectEqual(claudeUsage.RemainingPercent7d, 0, "Claude seven day remaining percent");
ExpectEqual(claudeUsage.Plan, "max", "Claude plan");

var claudeFallbackPlanUsage = UsageParser.ParseClaudeUsage("""
{
  "five_hour": { "utilization": 40.0, "resets_at": "2026-05-27T12:00:00Z" },
  "seven_day": { "utilization": 50.0, "resets_at": "2026-05-29T12:00:00Z" }
}
""", now, "team");
ExpectEqual(claudeFallbackPlanUsage.Plan, "team", "Claude fallback plan");

foreach (var invalidJson in new[]
{
    "{}", "[]", "null", """{"five_hour":{},"seven_day":null}""",
    """{"five_hour":{"utilization":true}}""",
    """{"five_hour":{"utilization":"unknown"}}""",
    """{"five_hour":{"utilization":"NaN"}}""",
    """{"seven_day":{"utilization":"Infinity"}}"""
})
{
    var rejected = false;
    try { UsageParser.ParseClaudeUsage(invalidJson); }
    catch (InvalidOperationException ex) when (ex.Message == "Invalid response") { rejected = true; }
    Expect(rejected, $"Claude rejects a response without usable measurements: {invalidJson}");
}
var weeklyClaude = UsageParser.ParseClaudeUsage("""{"seven_day":{"utilization":"25.4"}}""", now, "team");
ExpectEqual(weeklyClaude.RemainingPercent5h, (int?)null, "Claude allows a missing five-hour window");
ExpectEqual(weeklyClaude.RemainingPercent7d, 75, "Claude accepts numeric strings");
ExpectEqual(weeklyClaude.Plan, "team", "Partial Claude usage preserves the fallback plan");
var zeroClaude = UsageParser.ParseClaudeUsage("""{"five_hour":{"utilization":0},"seven_day":null}""");
ExpectEqual(zeroClaude.RemainingPercent5h, 100, "Zero usage is a valid Claude measurement");
var boundedClaude = UsageParser.ParseClaudeUsage("""{"five_hour":{"utilization":1e100},"seven_day":{"utilization":-1e100}}""");
ExpectEqual(boundedClaude.RemainingPercent5h, 0, "Large finite utilization is clamped before conversion");
ExpectEqual(boundedClaude.RemainingPercent7d, 100, "Negative utilization is clamped before conversion");

var home = Path.Combine(Path.GetTempPath(), "token-tracker-windows-tests-" + Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(Path.Combine(home, ".codex"));
Directory.CreateDirectory(Path.Combine(home, ".claude"));
File.WriteAllText(Path.Combine(home, ".codex", "auth.json"), """
{
  "tokens": {
    "access_token": "codex-token",
    "account_id": "account-id"
  }
}
""");
File.WriteAllText(Path.Combine(home, ".claude", ".credentials.json"), """
{
  "claudeAiOauth": {
    "accessToken": "claude-token",
    "subscriptionType": "max"
  }
}
""");

// The Claude fetch falls back to the status-line bridge file when the API path
// yields nothing. Point these tests at a path that does not exist, so a machine
// with the bridge installed answers them the same way a bare one does.
var noStatusLine = Path.Combine(home, "absent-statusline.json");

var credentials = new CredentialReader();
var codexAuth = credentials.ReadCodexAuth(home);
ExpectEqual(codexAuth.AccessToken, "codex-token", "Codex access token read");
ExpectEqual(codexAuth.AccountId, "account-id", "Codex account id read");
ExpectEqual(credentials.ReadClaudeAccessToken(home), "claude-token", "Claude access token read");
ExpectEqual(credentials.ReadClaudeCredential(home).Plan, "max", "Claude credential plan read");

using (var malformedHandler = new QueueHttpMessageHandler(
    new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{}") },
    new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent(claudeJson) }))
using (var malformedHttp = new HttpClient(malformedHandler))
{
    var client = new UsageClient(malformedHttp, homeDirectory: home,
        rateLimitStatePath: Path.Combine(home, "malformed-cooldown.json"),
        claudeStatusLinePath: noStatusLine);
    var malformed = await client.FetchClaudeAsync();
    ExpectEqual(malformed.Source, UsageSource.Unavailable, "Malformed HTTP 200 is not marked as an API success");
    ExpectEqual(malformed.Error, "Invalid response", "Malformed HTTP 200 reports its cause");
    var recovered = await client.FetchClaudeAsync();
    ExpectEqual(recovered.Source, UsageSource.Api, "A valid response recovers after a malformed response");
    ExpectEqual(malformedHandler.CallCount, 2, "Malformed data does not create a rate-limit cooldown");
}

var rateLimitStatePath = Path.Combine(Path.GetTempPath(), $"tt-rate-limit-{Guid.NewGuid():N}.json");
try
{
    var rateLimitedResponse = new HttpResponseMessage(HttpStatusCode.TooManyRequests);
    rateLimitedResponse.Headers.TryAddWithoutValidation("Retry-After", "300");
    var rateLimitedHandler = new QueueHttpMessageHandler(rateLimitedResponse);
    var rateLimitedClient = new UsageClient(new HttpClient(rateLimitedHandler), new CredentialReader(), home, rateLimitStatePath, claudeStatusLinePath: noStatusLine);
    var firstRateLimit = await rateLimitedClient.FetchClaudeAsync();
    ExpectEqual(firstRateLimit.Source, UsageSource.Unavailable, "Claude 429 is unavailable");
    Expect(firstRateLimit.Error?.StartsWith("HTTP 429 from Claude API; retrying after") == true, "Claude 429 error includes retry delay");
    ExpectEqual(rateLimitedHandler.CallCount, 1, "Claude 429 first call reaches HTTP");
    var skippedDuringBackoff = await rateLimitedClient.FetchClaudeAsync();
    ExpectEqual(skippedDuringBackoff.Source, UsageSource.Unavailable, "Claude backoff returns unavailable");
    Expect(skippedDuringBackoff.Error?.StartsWith("HTTP 429 from Claude API; retrying after") == true, "Claude backoff error includes retry delay");
    ExpectEqual(rateLimitedHandler.CallCount, 1, "Claude backoff skips HTTP");

    // The cooldown must survive an app restart: a brand-new client reading the
    // same state file honors the outstanding cooldown without touching HTTP.
    Expect(File.Exists(rateLimitStatePath), "Claude 429 cooldown is persisted to disk");
    Expect(
        File.ReadAllText(rateLimitStatePath).Contains("\"FailureCount\":1", StringComparison.Ordinal),
        "Claude 429 persists the failure count for exponential backoff");
    var restartHandler = new QueueHttpMessageHandler(new HttpResponseMessage(HttpStatusCode.OK) { Content = new StringContent("{}") });
    var restartedClient = new UsageClient(new HttpClient(restartHandler), new CredentialReader(), home, rateLimitStatePath, claudeStatusLinePath: noStatusLine);
    var afterRestart = await restartedClient.FetchClaudeAsync();
    ExpectEqual(afterRestart.Source, UsageSource.Unavailable, "Claude cooldown survives restart");
    Expect(afterRestart.Error?.StartsWith("HTTP 429 from Claude API; retrying after") == true, "Restarted client reports persisted cooldown");
    ExpectEqual(restartHandler.CallCount, 0, "Restarted client honors persisted cooldown without HTTP");

    // A cooldown persisted by a build that stored no fingerprint cannot be shown
    // to belong to the current token, so it is dropped rather than waited out.
    File.WriteAllText(
        rateLimitStatePath,
        "{\"RetryAllowedAt\":\"" + DateTimeOffset.Now.AddMinutes(30).ToString("O") + "\",\"FailureCount\":40}");
    var legacyHandler = new QueueHttpMessageHandler(new HttpResponseMessage(HttpStatusCode.OK)
    {
        Content = new StringContent("""
        { "five_hour": { "utilization": 5.0 }, "seven_day": { "utilization": 5.0 } }
        """)
    });
    var legacyClient = new UsageClient(new HttpClient(legacyHandler), new CredentialReader(), home, rateLimitStatePath, claudeStatusLinePath: noStatusLine);
    var afterLegacy = await legacyClient.FetchClaudeAsync();
    ExpectEqual(legacyHandler.CallCount, 1, "A fingerprint-less cooldown is dropped instead of blocking the current token");
    ExpectEqual(afterLegacy.RemainingPercent5h, 95, "Dropping an unattributable cooldown loads usage again");

    // Restore a fingerprinted cooldown for the re-login case below.
    var reBackoffResponse = new HttpResponseMessage(HttpStatusCode.TooManyRequests);
    reBackoffResponse.Headers.TryAddWithoutValidation("Retry-After", "300");
    var reBackoffClient = new UsageClient(
        new HttpClient(new QueueHttpMessageHandler(reBackoffResponse)), new CredentialReader(), home, rateLimitStatePath,
        claudeStatusLinePath: noStatusLine);
    await reBackoffClient.FetchClaudeAsync();

    // Signing in again has to take effect immediately: a cooldown earned by the
    // previous token must not keep blocking refreshes for the new one.
    File.WriteAllText(Path.Combine(home, ".claude", ".credentials.json"), """
    {
      "claudeAiOauth": {
        "accessToken": "claude-token-after-relogin",
        "subscriptionType": "max"
      }
    }
    """);
    var reloginHandler = new QueueHttpMessageHandler(new HttpResponseMessage(HttpStatusCode.OK)
    {
        Content = new StringContent("""
        { "five_hour": { "utilization": 10.0 }, "seven_day": { "utilization": 20.0 } }
        """)
    });
    var reloginClient = new UsageClient(new HttpClient(reloginHandler), new CredentialReader(), home, rateLimitStatePath, claudeStatusLinePath: noStatusLine);
    var afterRelogin = await reloginClient.FetchClaudeAsync();
    ExpectEqual(reloginHandler.CallCount, 1, "New credentials retry immediately instead of waiting out the old cooldown");
    ExpectEqual(afterRelogin.RemainingPercent5h, 90, "New credentials load usage again");

    // An expired token is reported as such without spending a request on it.
    var expiredAtMilliseconds = DateTimeOffset.UtcNow.AddHours(-1).ToUnixTimeMilliseconds();
    File.WriteAllText(
        Path.Combine(home, ".claude", ".credentials.json"),
        "{\"claudeAiOauth\":{\"accessToken\":\"claude-token-expired\",\"subscriptionType\":\"max\"," +
        "\"expiresAt\":" + expiredAtMilliseconds + "}}");
    var expiredHandler = new QueueHttpMessageHandler(new HttpResponseMessage(HttpStatusCode.Unauthorized));
    var expiredClient = new UsageClient(new HttpClient(expiredHandler), new CredentialReader(), home, rateLimitStatePath, claudeStatusLinePath: noStatusLine);
    var expiredResult = await expiredClient.FetchClaudeAsync();
    ExpectEqual(expiredHandler.CallCount, 0, "An expired token is not spent on a request");
    Expect(
        expiredResult.Error?.StartsWith("Credentials expired for Claude API") == true,
        "An expired token reports the real cause instead of an HTTP code");
    ExpectEqual(
        UsageIssueFormatter.Kind(expiredResult.Error!),
        UsageIssueKind.ExpiredCredentials,
        "An expired token is classified as needing a new sign-in");
}
finally
{
    if (File.Exists(rateLimitStatePath))
    {
        File.Delete(rateLimitStatePath);
    }
    Directory.Delete(home, recursive: true);
}

Expect(
    AppPaths.ClaudeRateLimitStatePath != AppPaths.CodexRateLimitStatePath,
    "Each provider holds its own cooldown file");

var codexHome = Path.Combine(Path.GetTempPath(), "token-tracker-codex-tests-" + Guid.NewGuid().ToString("N"));
var codexStatePath = Path.Combine(Path.GetTempPath(), $"tt-codex-rate-limit-{Guid.NewGuid():N}.json");
Directory.CreateDirectory(Path.Combine(codexHome, ".codex"));
File.WriteAllText(Path.Combine(codexHome, ".codex", "auth.json"), """
{
  "tokens": { "access_token": "codex-token", "account_id": "account-id" }
}
""");
try
{
    var codexLimited = new HttpResponseMessage(HttpStatusCode.TooManyRequests);
    codexLimited.Headers.TryAddWithoutValidation("Retry-After", "300");
    var codexHandler = new QueueHttpMessageHandler(codexLimited);
    var codexClient = new UsageClient(
        new HttpClient(codexHandler), new CredentialReader(), codexHome, null, codexStatePath);

    var firstCodex = await codexClient.FetchCodexAsync();
    Expect(firstCodex.Error?.StartsWith("HTTP 429 from Codex API; retrying after") == true, "Codex 429 reports the retry delay");
    ExpectEqual(codexHandler.CallCount, 1, "Codex 429 first call reaches HTTP");

    // The point of the fix: the next poll must not walk straight back into the limit.
    var secondCodex = await codexClient.FetchCodexAsync();
    ExpectEqual(codexHandler.CallCount, 1, "Codex honors its cooldown instead of re-firing every refresh");
    Expect(secondCodex.Error?.StartsWith("HTTP 429 from Codex API; retrying after") == true, "Codex backoff reports the retry delay");
    Expect(File.Exists(codexStatePath), "Codex cooldown is persisted to its own file");
}
finally
{
    if (File.Exists(codexStatePath)) File.Delete(codexStatePath);
    Directory.Delete(codexHome, recursive: true);
}

// History records measurements, not re-displays of one: a cached reading repeats a
// value the provider never re-reported, which would flatten the sparkline, zero the
// trend delta and tell the forecast that usage stopped moving.
var cachedReading = Usage(Provider.Claude, 63, 80, now) with { Source = UsageSource.StaleCache };
var recorded = UsageHistoryPolicy.MeasurementsOnly(
    new UsageSnapshot(cachedReading, Usage(Provider.Codex, 91, 99, now), now));
Expect(recorded.Claude.RemainingPercent5h is null, "A cached reading contributes no 5h measurement");
Expect(recorded.Claude.RemainingPercent7d is null, "A cached reading contributes no 7d measurement");
ExpectEqual(recorded.Codex.RemainingPercent5h, 91, "A live reading is recorded unchanged");
ExpectEqual(recorded.Claude.Source, UsageSource.StaleCache, "Clearing the value keeps the reason it was cleared");

// The oldest entry in the window recorded no Claude measurement. A shared baseline
// would land on it and drop Claude's delta; per-provider baselines skip past it.
var trendGapEntry = new UsageHistoryEntry(
    now.AddHours(-2),
    UsageHistoryPolicy.MeasurementsOnly(
        new UsageSnapshot(cachedReading, Usage(Provider.Codex, 91, 99, now), now.AddHours(-2))));
var trendBaselineEntry = new UsageHistoryEntry(
    now.AddHours(-1),
    new UsageSnapshot(Usage(Provider.Claude, 90, 95, now), Usage(Provider.Codex, 91, 99, now), now.AddHours(-1)));
var perProviderTrend = UsageHistoryFormatter.TrendSummary(
    new[] { trendGapEntry, trendBaselineEntry },
    new UsageSnapshot(Usage(Provider.Claude, 63, 80, now), Usage(Provider.Codex, 91, 99, now), now));
Expect(
    perProviderTrend.Contains("Claude 5h -27", StringComparison.Ordinal),
    "Claude's delta survives an older entry that recorded no Claude measurement");

var staleSnapshot = new UsageSnapshot(
    Claude: Usage(Provider.Claude, 63, 80, now),
    Codex: Usage(Provider.Codex, 91, 99, now),
    UpdatedAt: now);
var freshFailure = new UsageSnapshot(
    Claude: ProviderUsage.Unavailable(Provider.Claude, "HTTP 429 from Claude API"),
    Codex: Usage(Provider.Codex, 88, 97, now),
    UpdatedAt: now);
var staleApplied = UsageSnapshotCachePolicy.Apply(freshFailure, staleSnapshot, updatedAt: now.AddMinutes(1));
ExpectEqual(staleApplied.Claude.Source, UsageSource.StaleCache, "Claude stale cache source");
ExpectEqual(staleApplied.Claude.RemainingPercent5h, 63, "Claude stale cache percent");
ExpectEqual(staleApplied.Claude.Error, "HTTP 429 from Claude API", "Claude stale cache preserves fresh error");
ExpectEqual(staleApplied.Codex.Source, UsageSource.Api, "Fresh Codex remains API source");
ExpectEqual(staleApplied.Claude.UpdatedAt, staleSnapshot.Claude.UpdatedAt, "Cached data retains its measurement time");
var repeatedlyCached = staleSnapshot;
for (var hour = 1; hour <= 13; hour++)
{
    var pollAt = now.AddHours(hour);
    var failedPoll = freshFailure with { Codex = Usage(Provider.Codex, 88, 97, pollAt), UpdatedAt = pollAt };
    repeatedlyCached = UsageSnapshotCachePolicy.Apply(failedPoll, repeatedlyCached, updatedAt: pollAt);
    ExpectEqual(repeatedlyCached.Claude.Source, hour <= 12 ? UsageSource.StaleCache : UsageSource.Unavailable,
        "Repeated failures cannot extend the twelve-hour lifetime");
    if (hour <= 12)
        ExpectEqual(repeatedlyCached.Claude.UpdatedAt, now, "Repeated failures preserve the original measurement time");
    ExpectEqual(repeatedlyCached.Codex, failedPoll.Codex, "Fresh Codex data does not extend Claude's lifetime");
}
var staggered = new UsageSnapshot(
    Usage(Provider.Claude, 63, 80, now.AddHours(-2)),
    Usage(Provider.Codex, 91, 99, now.AddMinutes(-30)), now);
var bothFailed = freshFailure with { Codex = ProviderUsage.Unavailable(Provider.Codex, "offline") };
var shortTolerance = UsageSnapshotCachePolicy.Apply(bothFailed, staggered, updatedAt: now, maxAge: TimeSpan.FromHours(1));
ExpectEqual(shortTolerance.Claude.Source, UsageSource.Unavailable, "Custom tolerance expires Claude independently");
ExpectEqual(shortTolerance.Codex.Source, UsageSource.StaleCache, "Custom tolerance keeps recent Codex data");
var longTolerance = UsageSnapshotCachePolicy.Apply(bothFailed, staggered, updatedAt: now, maxAge: TimeSpan.FromHours(48));
ExpectEqual(longTolerance.Claude.Source, UsageSource.StaleCache, "Longer configured tolerance is honored");

// A cached reading stands in for hours; a window whose reset passed in that time
// has refilled, so its old number must not be shown. Mirrors the macOS tests.
var lapsedNow = new DateTimeOffset(2027, 1, 15, 8, 0, 0, TimeSpan.Zero);
var lapsedMeasuredAt = lapsedNow.AddHours(-3);
var lapsedFailure = new UsageSnapshot(
    ProviderUsage.Unavailable(Provider.Claude, "HTTP 429 from Claude API"),
    ProviderUsage.Unavailable(Provider.Codex, "HTTP 429 from Codex API"),
    lapsedNow);
ProviderUsage CachedReading(Provider provider, int? fiveHour, DateTimeOffset? reset5h, int? sevenDay, DateTimeOffset? reset7d) =>
    new(provider, fiveHour, sevenDay, reset5h, reset7d, UsageSource.Api, null, null, null, lapsedMeasuredAt);
// What the cache keeps, and what is shown of it.
UsageSnapshot CarryCached(ProviderUsage claude, ProviderUsage? codex = null) =>
    UsageSnapshotCachePolicy.Apply(
        lapsedFailure,
        new UsageSnapshot(claude, codex ?? ProviderUsage.Unavailable(Provider.Codex, "Disabled"), lapsedMeasuredAt),
        codexEnabled: codex is not null,
        updatedAt: lapsedNow);
UsageSnapshot ReuseCached(ProviderUsage claude, ProviderUsage? codex = null) =>
    UsageSnapshotCachePolicy.WithoutLapsedWindows(CarryCached(claude, codex), lapsedNow);
var lapsedReset = lapsedNow.AddHours(-2);
var liveReset = lapsedNow.AddHours(24);

var fiveHourLapsed = ReuseCached(CachedReading(Provider.Claude, 3, lapsedReset, 60, liveReset)).Claude;
ExpectEqual(fiveHourLapsed.Source, UsageSource.StaleCache, "A reading with one live window is still carried");
Expect(fiveHourLapsed.RemainingPercent5h is null, "A window whose reset passed loses its cached number");
ExpectEqual(fiveHourLapsed.ResetAt5h, (DateTimeOffset?)lapsedReset, "Its reset instant stays, so the menu says it has reset");
ExpectEqual(fiveHourLapsed.RemainingPercent7d, (int?)60, "A window still running keeps its cached number");
ExpectEqual(fiveHourLapsed.Error, "HTTP 429 from Claude API", "The carried reading keeps the current failure");
ExpectEqual(DisplayFormatter.DisplayPercent(fiveHourLapsed), (int?)60, "The tray falls back to the live window");

// The case the review reproduced: both windows reset while the token was lapsed,
// and the menu bar still read "AI 6%" in the 7d warning colour.
var bothLapsed = ReuseCached(CachedReading(Provider.Claude, 3, lapsedReset, 6, lapsedNow.AddMinutes(-10)));
ExpectEqual(bothLapsed.Claude.Source, UsageSource.Unavailable, "A reading with no live window is not carried at all");
ExpectEqual(bothLapsed.Claude.Error, "HTTP 429 from Claude API", "It reports the current failure instead");
ExpectEqual(DisplayFormatter.StatusTitle(bothLapsed, DisplayMode.LowestRemaining), "AI --", "No stale low number reaches the tray");
Expect(!DisplayFormatter.IsSevenDayWarning(bothLapsed.Claude), "Nor its warning colour");

var resetsNow = ReuseCached(CachedReading(Provider.Claude, 3, lapsedNow, 60, liveReset)).Claude;
Expect(resetsNow.RemainingPercent5h is null, "A reset at this very instant has passed, as the alerts judge it");
var unknownReset = ReuseCached(CachedReading(Provider.Claude, 3, null, null, null)).Claude;
ExpectEqual(unknownReset.RemainingPercent5h, (int?)3, "A window with no reset instant cannot be judged and is kept");
ExpectEqual(unknownReset.Source, UsageSource.StaleCache, "So the reading is still carried");
var allLive = CachedReading(Provider.Claude, 3, lapsedNow.AddMinutes(10), 6, liveReset);
var carriedLive = ReuseCached(allLive).Claude;
ExpectEqual(carriedLive.RemainingPercent5h, (int?)3, "A low window that has not reset keeps its number");
ExpectEqual(carriedLive.RemainingPercent7d, (int?)6, "Both windows of it");

var codexLapsed = ReuseCached(allLive, CachedReading(Provider.Codex, null, null, 4, lapsedReset));
ExpectEqual(codexLapsed.Codex.Source, UsageSource.Unavailable, "Codex is held to the same rule");
ExpectEqual(codexLapsed.Codex.Error, "HTTP 429 from Codex API", "With its own failure reason");

var measuredLapsedSnapshot = new UsageSnapshot(
    CachedReading(Provider.Claude, 3, lapsedReset, 60, liveReset),
    ProviderUsage.Unavailable(Provider.Codex, "Disabled"),
    lapsedMeasuredAt);
var untouchedMeasurement = UsageSnapshotCachePolicy.WithoutLapsedWindows(measuredLapsedSnapshot, lapsedNow);
ExpectEqual(untouchedMeasurement.Claude, measuredLapsedSnapshot.Claude, "Only carried readings are trimmed; a measurement passes untouched");

// The cache file keeps the carried reading whole: at launch the freed-up alert
// seeds its "was low" reading from it, so a window that ran low and reset while
// the app was restarted mid-outage is still reported once measured again.
var lowThenReset = CachedReading(Provider.Claude, 8, lapsedReset, 80, liveReset);
var keptLowWindow = CarryCached(lowThenReset).Claude;
ExpectEqual(keptLowWindow.Source, UsageSource.StaleCache, "The cache keeps the carried reading");
ExpectEqual(keptLowWindow.RemainingPercent5h, (int?)8, "With the number of a window that has since reset");
var keptNoLiveWindow = CarryCached(CachedReading(Provider.Claude, 3, lapsedReset, 6, lapsedReset)).Claude;
ExpectEqual(keptNoLiveWindow.RemainingPercent7d, (int?)6, "Even when no window of it is live any more");
var lapsedSeed = UsageAlertEvaluator.LastReadings(new Dictionary<Provider, ProviderUsage>(), CarryCached(lowThenReset));
var measuredAfterRestart = new ProviderUsage(
    Provider.Claude, 100, 80, null, liveReset, UsageSource.Api, null, null, null, lapsedNow.AddMinutes(20));
var freedAfterRestart = UsageAlertEvaluator.FreedUpCandidates(
    lapsedSeed,
    new UsageSnapshot(measuredAfterRestart, ProviderUsage.Unavailable(Provider.Codex, "Disabled"), measuredAfterRestart.UpdatedAt),
    new UsageAlertSettings(true, 20, 10, 0));
ExpectEqual(
    string.Join(",", freedAfterRestart.Select(candidate => candidate.Id)),
    "claude-5h-freed-" + UsageAlertEvaluator.ResetKey(lapsedReset),
    "A restart mid-outage still reports the window freed");
var cachedIssue = UsageIssueFormatter.Issue(staleApplied.Claude);
ExpectEqual(cachedIssue.Kind, UsageIssueKind.UsingCachedData, "Stale cache issue is classified");
ExpectEqual(cachedIssue.TechnicalDetail, "HTTP 429 from Claude API", "Stale cache keeps technical detail");
ExpectEqual(UsageIssueFormatter.Kind("Disabled"), UsageIssueKind.Disabled, "Disabled issue is classified");
ExpectEqual(UsageIssueFormatter.Kind("HTTP 429 from Claude API; retrying after 5m"), UsageIssueKind.RateLimited, "429 issue is classified");
ExpectEqual(UsageIssueFormatter.Kind("Missing credentials"), UsageIssueKind.MissingCredentials, "Missing credentials issue is classified");
ExpectEqual(UsageIssueFormatter.Kind("Credentials expired for Codex API"), UsageIssueKind.ExpiredCredentials, "Expired token issue is classified");
ExpectEqual(UsageIssueFormatter.Kind("HTTP 401 from Claude API"), UsageIssueKind.ExpiredCredentials, "401 is reported as an expired sign-in, not a bare HTTP error");
ExpectEqual(UsageIssueFormatter.Kind("HTTP 403 from Codex API"), UsageIssueKind.ExpiredCredentials, "403 is reported as an expired sign-in");
ExpectEqual(UsageIssueFormatter.Kind("HTTP 500 from Claude API"), UsageIssueKind.HttpStatus, "Other HTTP codes stay generic HTTP errors");
var codexRecovery = UsageIssueFormatter.Issue(ProviderUsage.Unavailable(Provider.Codex, "Credentials expired for Codex API")).Recovery;
Expect(codexRecovery?.Contains("codex login") == true, "Codex recovery names the Codex sign-in command");
Expect(codexRecovery?.Contains("/login") == false, "Codex recovery does not make the user read past Claude's command");
var claudeRecovery = UsageIssueFormatter.Issue(ProviderUsage.Unavailable(Provider.Claude, "HTTP 401 from Claude API")).Recovery;
Expect(claudeRecovery?.Contains("/login") == true, "Claude recovery names the Claude sign-in command");
Expect(claudeRecovery?.Contains("codex login") == false, "Claude recovery does not mention Codex");

// Credential expiry: the app reads tokens the provider CLIs refresh, so it has to
// recognize a lapsed one locally instead of spending a request to be told 401.
Expect(!CredentialExpiry.IsExpired(null), "An unknown expiry is not treated as expired");
Expect(!CredentialExpiry.IsExpired(DateTimeOffset.Now.AddHours(1)), "A token valid for an hour is usable");
Expect(CredentialExpiry.IsExpired(DateTimeOffset.Now.AddSeconds(-1)), "A lapsed token is expired");
Expect(
    CredentialExpiry.IsExpired(DateTimeOffset.Now.Add(CredentialExpiry.Skew / 2)),
    "A token inside the skew window is expired early rather than lapsing mid-request");
// {"alg":"none"} . {"exp":1770000000}
ExpectEqual(
    CredentialExpiry.JwtExpiry("eyJhbGciOiJub25lIn0.eyJleHAiOjE3NzAwMDAwMDB9.sig"),
    DateTimeOffset.FromUnixTimeSeconds(1_770_000_000),
    "The exp claim is read out of a JWT access token");
Expect(CredentialExpiry.JwtExpiry("not-a-jwt") is null, "A non-JWT token has no readable expiry");
ExpectEqual(TokenFingerprint.Of("a"), TokenFingerprint.Of("a"), "The same token fingerprints the same");
Expect(TokenFingerprint.Of("a") != TokenFingerprint.Of("b"), "Signing in again changes the fingerprint");
ExpectEqual(UsageIssueFormatter.Kind("Timed out contacting Claude API"), UsageIssueKind.TimedOut, "Timeout issue is classified");

var disabledStale = UsageSnapshotCachePolicy.Apply(freshFailure, staleSnapshot, claudeEnabled: false, updatedAt: now.AddMinutes(1));
ExpectEqual(disabledStale.Claude.Source, UsageSource.Unavailable, "Disabled Claude does not use stale cache");

var alertSnapshot = new UsageSnapshot(
    Claude: new ProviderUsage(
        Provider.Claude,
        RemainingPercent5h: 19,
        RemainingPercent7d: 9,
        ResetAt5h: now.AddMinutes(5),
        ResetAt7d: now.AddHours(2),
        UsageSource.Api,
        Error: null,
        Plan: null,
        Model: null,
        UpdatedAt: now),
    Codex: Usage(Provider.Codex, 80, 90, now),
    UpdatedAt: now);
var alerts = UsageAlertEvaluator.Candidates(
    alertSnapshot,
    new UsageAlertSettings(true, 20, 10, 10),
    now);
ExpectEqual(
    string.Join(",", alerts.Select(alert => alert.Id)),
    "claude-5h-low,claude-7d-low",
    "A low window gets its low alert and no reset warning: the refill is good news");
ExpectEqual(
    UsageAlertEvaluator.Candidates(alertSnapshot, new UsageAlertSettings(false, 20, 10, 10), now).Count,
    0,
    "Disabled notifications emit no alerts");

// Reset warnings: only for a measured window with more left than its low threshold,
// since what it has not used by the reset is gone.
string ResetAlertBodies(int? unused5h, UsageSource source = UsageSource.Api, int threshold = 20, AppLanguage language = AppLanguage.English)
{
    var snapshot = new UsageSnapshot(
        Claude: new ProviderUsage(Provider.Claude, unused5h, 90, now.AddMinutes(5), now.AddDays(3), source, null, null, null, now),
        Codex: ProviderUsage.Unavailable(Provider.Codex, "Disabled"),
        UpdatedAt: now);
    return string.Join(" | ", UsageAlertEvaluator.Candidates(snapshot, new UsageAlertSettings(true, threshold, 10, 10), now, new Localizer(language))
        .Where(candidate => candidate.Id.Contains("-reset-", StringComparison.Ordinal))
        .Select(candidate => candidate.Body));
}
ExpectEqual(ResetAlertBodies(60), "Claude 5h resets in 5m with 60% unused", "A reset with plenty left is worth a warning");
ExpectEqual(ResetAlertBodies(60, language: AppLanguage.Korean), "Claude 5h 5m 후 리셋 — 60% 남음", "The reset warning reads in Korean");
ExpectEqual(ResetAlertBodies(20), "", "A window at its low threshold gets no reset warning");
ExpectEqual(ResetAlertBodies(21), "Claude 5h resets in 5m with 21% unused", "One point above the threshold is warned about");
ExpectEqual(ResetAlertBodies(1, threshold: 0), "Claude 5h resets in 5m with 1% unused", "With the low alert off, anything left is worth a warning");
ExpectEqual(ResetAlertBodies(0, threshold: 0), "", "Nothing left is nothing to lose");
ExpectEqual(ResetAlertBodies(null), "", "A window with no number gets no reset warning");
ExpectEqual(ResetAlertBodies(60, UsageSource.StaleCache), "", "A cached number may have been spent since");
ExpectEqual(ResetAlertBodies(60, UsageSource.StatusLine), "Claude 5h resets in 5m with 60% unused", "A status-line reading is a measurement");

string StalledIds(TimeSpan age, UsageSource source, TimeSpan stalledAfter)
{
    var snapshot = new UsageSnapshot(
        Claude: new ProviderUsage(Provider.Claude, 55, 70, null, null, source, null, null, null, now - age),
        Codex: new ProviderUsage(Provider.Codex, 80, 90, null, null, UsageSource.Api, null, null, null, now),
        UpdatedAt: now);
    var settings = new UsageAlertSettings(true, 0, 0, 0, stalledAfter);
    return string.Join(",", UsageAlertEvaluator.Candidates(snapshot, settings, now).Select(candidate => candidate.Id));
}

ExpectEqual(
    StalledIds(TimeSpan.FromMinutes(90), UsageSource.StaleCache, TimeSpan.FromMinutes(30)),
    "claude-stalled",
    "A cached reading past the threshold is reported as stalled");
ExpectEqual(
    StalledIds(TimeSpan.FromMinutes(10), UsageSource.StaleCache, TimeSpan.FromMinutes(30)),
    "",
    "A cached reading younger than the threshold is not news");
ExpectEqual(
    StalledIds(TimeSpan.FromMinutes(90), UsageSource.Api, TimeSpan.FromMinutes(30)),
    "",
    "An old measurement is still a measurement");
ExpectEqual(
    StalledIds(TimeSpan.FromMinutes(90), UsageSource.StaleCache, TimeSpan.Zero),
    "",
    "A zero threshold disables the stalled alert");
ExpectEqual(
    UsageAlertEvaluator.StalledThreshold(300),
    TimeSpan.FromMinutes(30),
    "A short poll interval keeps the 30 minute floor");
ExpectEqual(
    UsageAlertEvaluator.StalledThreshold(1800),
    TimeSpan.FromMinutes(90),
    "A long poll interval waits three polls");

string LowBalanceIds(DateTimeOffset? reset5h, DateTimeOffset? reset7d)
{
    var snapshot = new UsageSnapshot(
        Claude: new ProviderUsage(Provider.Claude, 5, 4, reset5h, reset7d, UsageSource.StaleCache, null, null, null, now),
        Codex: new ProviderUsage(Provider.Codex, 80, 90, null, null, UsageSource.Api, null, null, null, now),
        UpdatedAt: now);
    var settings = new UsageAlertSettings(true, 20, 10, 0);
    return string.Join(",", UsageAlertEvaluator.Candidates(snapshot, settings, now).Select(candidate => candidate.Id));
}

ExpectEqual(
    LowBalanceIds(now.AddMinutes(10), now.AddHours(2)),
    "claude-5h-low,claude-7d-low",
    "A balance in a window that has not reset still warns");
ExpectEqual(
    LowBalanceIds(now.AddMinutes(-1), now.AddMinutes(-1)),
    "",
    "A balance whose window already reset is no reason to warn");
ExpectEqual(
    LowBalanceIds(null, null),
    "claude-5h-low,claude-7d-low",
    "With no reset instant to judge by the alert stands");

var cachePath = Path.Combine(Path.GetTempPath(), "token-tracker-cache-" + Guid.NewGuid().ToString("N"), "usage-cache.json");
var cacheStore = new CacheStore(cachePath);
// CacheStore.Load compares UpdatedAt against the real wall clock, so save a
// snapshot stamped at the current time rather than the fixed test `now`
// (otherwise this assertion becomes a date-bomb once real time drifts past it).
cacheStore.Save(staleSnapshot with { UpdatedAt = DateTimeOffset.Now });
var loadedSnapshot = cacheStore.Load(TimeSpan.FromHours(1));
Expect(loadedSnapshot is not null, "Cache loads saved snapshot");
ExpectEqual(loadedSnapshot!.Claude.RemainingPercent5h, 63, "Cache preserves Claude percent");
var cacheNow = DateTimeOffset.Now;
var oldReading = new UsageSnapshot(
    Usage(Provider.Claude, 63, 80, cacheNow.AddHours(-13)),
    Usage(Provider.Codex, 91, 99, cacheNow.AddHours(-13)), cacheNow.AddHours(-13));
var firstFailedPoll = bothFailed with { UpdatedAt = cacheNow.AddHours(-3) };
cacheStore.Save(UsageSnapshotCachePolicy.Apply(firstFailedPoll, oldReading, updatedAt: firstFailedPoll.UpdatedAt));
var persistedStale = cacheStore.Load(TimeSpan.FromHours(12));
Expect(persistedStale is not null, "Recent poll snapshot survives a restart");
ExpectEqual(persistedStale!.Claude.UpdatedAt, oldReading.Claude.UpdatedAt, "Disk cache preserves the original measurement time");
var afterRestartFailure = UsageSnapshotCachePolicy.Apply(bothFailed, persistedStale, updatedAt: cacheNow);
ExpectEqual(afterRestartFailure.Claude.Source, UsageSource.Unavailable, "Restart does not revive an expired Claude measurement");
ExpectEqual(afterRestartFailure.Codex.Source, UsageSource.Unavailable, "Restart does not revive an expired Codex measurement");
File.WriteAllText(cachePath, System.Text.Json.JsonSerializer.Serialize(staleSnapshot with { UpdatedAt = cacheNow }));
Expect(cacheStore.Load(TimeSpan.FromHours(12)) is null, "Legacy caches have untrustworthy timestamps and are discarded");
Directory.Delete(Path.GetDirectoryName(cachePath)!, recursive: true);

var historyPath = Path.Combine(Path.GetTempPath(), "token-tracker-history-" + Guid.NewGuid().ToString("N"), "usage-history.json");
var historyStore = new UsageHistoryStore(historyPath);
historyStore.Append(staleSnapshot, retentionDays: 7, now);
historyStore.Append(snapshot, retentionDays: 7, now.AddHours(1));
var historyEntries = historyStore.Load();
ExpectEqual(historyEntries.Count, 2, "History preserves entries outside merge window");

// The history file is decoded once per change, not once per reader: a refresh and
// every right-click used to read it whole four times. Same truth table as the
// macOS and Linux tests.
for (var read = 0; read < 5; read++)
{
    ExpectEqual(historyStore.Load().Count, 2, "Every reader sees what was written");
}
ExpectEqual(historyStore.FileReads, 0, "What the store wrote itself is never read back");
var otherHistoryStore = new UsageHistoryStore(historyPath);
ExpectEqual(otherHistoryStore.Load().Count, 2, "Another store reads the file");
otherHistoryStore.Load();
otherHistoryStore.CsvString();
ExpectEqual(otherHistoryStore.FileReads, 1, "And decodes it once while it stays unchanged");
otherHistoryStore.Append(snapshot, retentionDays: 7, now.AddHours(2));
ExpectEqual(historyStore.Load().Count, 3, "A write by another store is picked up");
ExpectEqual(historyStore.FileReads, 1, "By reading the file once more");
File.Delete(historyPath);
ExpectEqual(historyStore.Load().Count, 0, "A deleted file is an empty history, not the remembered one");
File.WriteAllText(historyPath, "not json");
ExpectEqual(historyStore.Load().Count, 0, "A corrupt file reads as empty");
historyStore.Load();
ExpectEqual(historyStore.FileReads, 2, "And is not decoded again until it changes");
historyStore.Append(snapshot, retentionDays: 7, now.AddHours(3));
ExpectEqual(new UsageHistoryStore(historyPath).Load().Count, 1, "Appending over a corrupt file starts a fresh history");
// The checks below expect the two entries this section started with.
File.Delete(historyPath);
historyStore = new UsageHistoryStore(historyPath);
historyStore.Append(staleSnapshot, retentionDays: 7, now);
historyStore.Append(snapshot, retentionDays: 7, now.AddHours(1));
historyEntries = historyStore.Load();
var trend = UsageHistoryFormatter.TrendSummary(
    historyEntries,
    snapshot,
    TimeSpan.FromDays(1),
    new Localizer(AppLanguage.English));
ExpectEqual(trend, "24h trend: Claude 5h 0% 7d 0% Codex 5h 0% 7d 0%", "History trend summarizes 5h and 7d provider deltas");

var missingSevenDayBaseline = new UsageSnapshot(
    Claude: Usage(Provider.Claude, 40, null, now),
    Codex: Usage(Provider.Codex, 90, 99, now),
    UpdatedAt: now);
var missingSevenDayTrend = UsageHistoryFormatter.TrendSummary(
    new[] { new UsageHistoryEntry(now, missingSevenDayBaseline) },
    snapshot,
    TimeSpan.FromDays(1),
    new Localizer(AppLanguage.English));
ExpectEqual(missingSevenDayTrend, "24h trend: Claude 5h +23% 7d -- Codex 5h +1% 7d 0%", "History trend shows -- when a 7d value is missing");
var csv = historyStore.CsvString();
Expect(csv.Contains("recorded_at,provider,remaining_5h", StringComparison.Ordinal), "History CSV includes header");
Expect(csv.Contains("claude,63,80", StringComparison.Ordinal), "History CSV includes Claude row");
Directory.Delete(Path.GetDirectoryName(historyPath)!, recursive: true);

var korean = new Localizer(AppLanguage.Korean);
ExpectEqual(korean.Text(L10nKey.RefreshNow), "지금 새로고침", "Korean refresh label");
ExpectEqual(korean.Text(L10nKey.ClaudeOnly), "Claude만", "Korean Claude-only label");
ExpectEqual(korean.Text(L10nKey.Diagnostics), "진단", "Korean diagnostics label");

var settingsPath = Path.Combine(Path.GetTempPath(), "token-tracker-settings-" + Guid.NewGuid().ToString("N"), "settings.json");
var settingsStore = new SettingsStore(settingsPath);
settingsStore.Save(new AppSettings
{
    Language = AppLanguage.Korean,
    DisplayMode = DisplayMode.ClaudeOnly,
    NotificationsEnabled = true,
    FiveHourAlertThreshold = 25,
    HistoryRetentionDays = 30
});
var loadedSettings = settingsStore.Load();
ExpectEqual(loadedSettings.Language, AppLanguage.Korean, "Language setting persists");
ExpectEqual(loadedSettings.DisplayMode, DisplayMode.ClaudeOnly, "Display mode setting persists");
ExpectEqual(loadedSettings.NotificationsEnabled, true, "Notification setting persists");
ExpectEqual(loadedSettings.FiveHourAlertThreshold, 25, "Alert threshold persists");
ExpectEqual(loadedSettings.HistoryRetentionDays, 30, "History retention persists");
ExpectEqual(settingsStore.Save(loadedSettings), true, "A save that worked says so");
// A save that cannot replace the file reports it instead of throwing: it used to
// reach WinForms as an unhandled exception and its error dialog. The file is held
// open without delete sharing, as antivirus or a backup tool would hold it.
using (new FileStream(settingsPath, FileMode.Open, FileAccess.Read, FileShare.Read))
{
    ExpectEqual(settingsStore.Save(new AppSettings { HistoryRetentionDays = 90 }), false, "A save the file's holder blocks fails without throwing");
}
ExpectEqual(settingsStore.Load().HistoryRetentionDays, 30, "And leaves the saved settings as they were");
ExpectEqual(settingsStore.Save(new AppSettings { HistoryRetentionDays = 90 }), true, "Once the file is free the next save goes through");
ExpectEqual(settingsStore.Load().HistoryRetentionDays, 90, "With the new settings");
Directory.Delete(Path.GetDirectoryName(settingsPath)!, recursive: true);

ExpectEqual(RateLimitBackoff.Cooldown(TimeSpan.FromSeconds(300), 0, 0).TotalSeconds, 300.0, "First headerless 429 waits the 300s default");
ExpectEqual(RateLimitBackoff.Cooldown(TimeSpan.Zero, 0, 0).TotalSeconds, 120.0, "Absent Retry-After falls back to the 120s minimum");
ExpectEqual(RateLimitBackoff.Cooldown(TimeSpan.FromSeconds(300), 2, 0).TotalSeconds, 1200.0, "Repeated 429 escalates exponentially");
ExpectEqual(RateLimitBackoff.Cooldown(TimeSpan.FromSeconds(300), 5, 0).TotalSeconds, 1800.0, "Escalation is capped at 30m");
ExpectEqual(RateLimitBackoff.Cooldown(TimeSpan.FromSeconds(3600), 0, 0).TotalSeconds, 3600.0, "An explicit longer Retry-After is honored above the cap");
var jitteredCooldown = RateLimitBackoff.Cooldown(TimeSpan.FromSeconds(300), 0, RateLimitBackoff.JitterFraction).TotalSeconds;
Expect(jitteredCooldown > 300.0 && jitteredCooldown <= 360.0, "Jitter adds up to 20 percent on top of the base cooldown");

var legacyIntervalPath = Path.Combine(Path.GetTempPath(), "token-tracker-settings-" + Guid.NewGuid().ToString("N"), "settings.json");
var legacyIntervalStore = new SettingsStore(legacyIntervalPath);
legacyIntervalStore.Save(new AppSettings { RefreshIntervalSeconds = 30 });
ExpectEqual(legacyIntervalStore.Load().RefreshIntervalSeconds, 60, "Legacy sub-60 refresh interval migrates to the 60s floor");
legacyIntervalStore.Save(new AppSettings { RefreshIntervalSeconds = 300 });
ExpectEqual(legacyIntervalStore.Load().RefreshIntervalSeconds, 300, "Valid refresh interval is left unchanged");
Directory.Delete(Path.GetDirectoryName(legacyIntervalPath)!, recursive: true);

// Usage forecast
UsageHistoryEntry ForecastEntry(double secondsAgo, int? claude5h, int? claude7d = 100)
{
    var at = now.AddSeconds(-secondsAgo);
    return new UsageHistoryEntry(at, new UsageSnapshot(
        Usage(Provider.Claude, claude5h, claude7d, at),
        Usage(Provider.Codex, 100, 100, at),
        at));
}

var steadyEntries = new[] { ForecastEntry(3600, 60), ForecastEntry(1800, 50), ForecastEntry(0, 40) };
var steadyForecast = UsageForecaster.Forecast(steadyEntries, Provider.Claude, ForecastWindow.FiveHour, now.AddHours(3), now);
Expect(steadyForecast is not null, "Forecast is produced for a steady decline");
ExpectEqual((int)steadyForecast!.BurnPerHour, 20, "Burn rate is 20%/h");
ExpectEqual((int)steadyForecast.SecondsToEmpty, 7200, "5h window empties in 2h at the observed rate");
Expect(steadyForecast.WillEmptyBeforeReset, "Empties before a 3h reset");

var earlyResetForecast = UsageForecaster.Forecast(steadyEntries, Provider.Claude, ForecastWindow.FiveHour, now.AddHours(1), now);
Expect(!earlyResetForecast!.WillEmptyBeforeReset, "Does not empty before an earlier reset");

var resetEntries = new[] { ForecastEntry(3600, 30), ForecastEntry(1800, 80), ForecastEntry(900, 70), ForecastEntry(0, 60) };
var resetForecast = UsageForecaster.Forecast(resetEntries, Provider.Claude, ForecastWindow.FiveHour, null, now);
ExpectEqual((int)resetForecast!.SecondsToEmpty, 5400, "Forecast uses only the post-reset segment");
ExpectEqual((int)resetForecast.BurnPerHour, 40, "Post-reset burn rate is 40%/h");

Expect(UsageForecaster.Forecast(new[] { ForecastEntry(3600, 40), ForecastEntry(0, 50) }, Provider.Claude, ForecastWindow.FiveHour, null, now) is null, "No forecast while replenishing");
Expect(UsageForecaster.Forecast(new[] { ForecastEntry(300, 60), ForecastEntry(0, 50) }, Provider.Claude, ForecastWindow.FiveHour, null, now) is null, "No forecast under the minimum span");
Expect(UsageForecaster.Forecast(new[] { ForecastEntry(10800, 60), ForecastEntry(7200, 40) }, Provider.Claude, ForecastWindow.FiveHour, null, now) is null, "No forecast when the newest sample is stale");

ExpectEqual(UsageForecaster.DurationText(7800), "2h 10m", "Duration formats hours and minutes");
ExpectEqual(UsageForecaster.DurationText(2700), "45m", "Duration formats minutes");
ExpectEqual(UsageForecaster.DurationText(30), "<1m", "Sub-minute duration collapses to <1m");
ExpectEqual(UsageForecaster.DurationText(86400), "1d 0h", "One-day duration switches to days");
ExpectEqual(UsageForecaster.DurationText(273000), "3d 3h", "Duration formats days and hours");

var forecastLocalizer = new Localizer(AppLanguage.English);
ExpectEqual(
    UsageForecastText.MenuLine(steadyForecast, ForecastWindow.FiveHour, forecastLocalizer),
    "Projected depletion: ~2h 0m · empties before reset",
    "5h forecast menu line format is unchanged");
ExpectEqual(
    UsageForecastText.MenuLine(steadyForecast, ForecastWindow.SevenDay, forecastLocalizer),
    "Projected depletion: ~2h 0m (7d) · empties before reset",
    "7d fallback forecast line carries the window marker");
Expect(UsageForecastText.MenuLine(null, ForecastWindow.SevenDay, forecastLocalizer) is null, "No forecast menu line without a forecast");

var forecastAlertInput = new ForecastAlertInput(Provider.Claude, ForecastWindow.FiveHour, steadyForecast, now.AddHours(3));
ExpectEqual(
    string.Join(",", UsageForecastAlert.Candidates(new[] { forecastAlertInput }, enabled: true).Select(candidate => candidate.Id)),
    $"claude-5h-empty-before-reset-{UsageAlertEvaluator.ResetKey(now.AddHours(3))}",
    "Forecast alert fires when depletion precedes reset");

// Alert ids name the window by its reset rounded to the minute. Providers report
// one reset a fraction of a second apart from poll to poll — Claude's came back as
// 04:59:59 and 05:00:00 in turn — and a new id each time sent the alert again.
var wholeMinute = DateTimeOffset.FromUnixTimeSeconds(1_800_000_000);
ExpectEqual(UsageAlertEvaluator.ResetKey(wholeMinute), 1_800_000_000L, "a whole-minute reset keys to itself");
ExpectEqual(UsageAlertEvaluator.ResetKey(wholeMinute.AddMilliseconds(-400)), 1_800_000_000L, "a reset reported just early keys to the same minute");
ExpectEqual(UsageAlertEvaluator.ResetKey(wholeMinute.AddMilliseconds(400)), 1_800_000_000L, "a reset reported just late keys to the same minute");
ExpectEqual(UsageAlertEvaluator.ResetKey(wholeMinute.AddSeconds(29)), 1_800_000_000L, "under half a minute rounds down");
ExpectEqual(UsageAlertEvaluator.ResetKey(wholeMinute.AddSeconds(30)), 1_800_000_060L, "half a minute rounds up");
ExpectEqual(UsageAlertEvaluator.ResetKey(wholeMinute.AddMilliseconds(-1600)), 1_800_000_000L, "Codex's 58/59-second readings key alike");
Expect(UsageAlertEvaluator.ResetKey(wholeMinute.AddHours(5)) != UsageAlertEvaluator.ResetKey(wholeMinute), "the next window instance keeps its own key");

var jitterNow = wholeMinute.AddMinutes(-5);
var jitterSettings = new UsageAlertSettings(true, 20, 10, 10);
string JitterResetIds(DateTimeOffset reset)
{
    var usage = new ProviderUsage(Provider.Claude, 60, 90, reset, null, UsageSource.Api, null, null, null, jitterNow);
    var jitterSnapshot = new UsageSnapshot(usage, ProviderUsage.Unavailable(Provider.Codex, "Disabled"), jitterNow);
    return string.Join(",", UsageAlertEvaluator.Candidates(jitterSnapshot, jitterSettings, jitterNow).Select(candidate => candidate.Id));
}
var earlyReset = wholeMinute.AddMilliseconds(-200);
var lateReset = wholeMinute.AddMilliseconds(300);
ExpectEqual(JitterResetIds(earlyReset), "claude-5h-reset-1800000000", "the reset warning names the minute");
ExpectEqual(JitterResetIds(lateReset), JitterResetIds(earlyReset), "a reset that flips across a second keeps its warning id");
string JitterDepletionIds(DateTimeOffset reset)
{
    var forecast = new UsageForecast(30, 60, jitterNow.AddMinutes(1), true);
    var input = new ForecastAlertInput(Provider.Claude, ForecastWindow.FiveHour, forecast, reset);
    return string.Join(",", UsageForecastAlert.Candidates(new[] { input }, enabled: true).Select(candidate => candidate.Id));
}
ExpectEqual(JitterDepletionIds(lateReset), JitterDepletionIds(earlyReset), "a reset that flips across a second keeps its depletion id");
string JitterFreedIds(DateTimeOffset reset)
{
    var before = new ProviderUsage(Provider.Claude, 5, 90, reset, null, UsageSource.Api, null, null, null, wholeMinute.AddMinutes(-10));
    var after = new ProviderUsage(Provider.Claude, 100, 90, null, null, UsageSource.Api, null, null, null, wholeMinute.AddMinutes(1));
    var previous = new Dictionary<Provider, ProviderUsage> { [Provider.Claude] = before };
    var current = new UsageSnapshot(after, ProviderUsage.Unavailable(Provider.Codex, "Disabled"), after.UpdatedAt);
    return string.Join(",", UsageAlertEvaluator.FreedUpCandidates(previous, current, jitterSettings).Select(candidate => candidate.Id));
}
ExpectEqual(JitterFreedIds(lateReset), JitterFreedIds(earlyReset), "a reset that flips across a second keeps its freed id");
ExpectEqual(JitterFreedIds(earlyReset), "claude-5h-freed-1800000000", "the freed alert names the minute too");
ExpectEqual(UsageForecastAlert.Candidates(new[] { forecastAlertInput }, enabled: false).Count, 0, "Forecast alert suppressed when disabled");
var safeForecastInput = new ForecastAlertInput(Provider.Claude, ForecastWindow.FiveHour, earlyResetForecast, now.AddHours(1));
ExpectEqual(UsageForecastAlert.Candidates(new[] { safeForecastInput }, enabled: true).Count, 0, "No forecast alert when the reset comes first");

// Alert bodies follow the language setting like their titles. The ids do not:
// duplicate suppression is keyed on them, so switching language must not re-fire.
string AlertBodies(AppLanguage language)
{
    var bodyLocalizer = new Localizer(language);
    var stalledSnapshot = new UsageSnapshot(
        Claude: new ProviderUsage(Provider.Claude, 55, 70, null, null, UsageSource.StaleCache, null, null, null, now - TimeSpan.FromMinutes(90)),
        Codex: new ProviderUsage(Provider.Codex, 80, 90, null, null, UsageSource.Api, null, null, null, now),
        UpdatedAt: now);
    var stalledAlerts = UsageAlertEvaluator.Candidates(
        stalledSnapshot,
        new UsageAlertSettings(true, 0, 0, 0, TimeSpan.FromMinutes(30)),
        now,
        bodyLocalizer);
    var lowAndResetAlerts = UsageAlertEvaluator.Candidates(
        alertSnapshot,
        new UsageAlertSettings(true, 20, 10, 10),
        now,
        bodyLocalizer);
    var depletionAlerts = UsageForecastAlert.Candidates(new[] { forecastAlertInput }, enabled: true, localizer: bodyLocalizer);
    return string.Join(" | ", stalledAlerts.Concat(lowAndResetAlerts).Concat(depletionAlerts).Select(candidate => candidate.Body));
}

ExpectEqual(
    AlertBodies(AppLanguage.English),
    "Claude last measured 1h30m ago | Claude 5h 19% <= 20% | Claude 7d 9% <= 10% | Claude 5h: ~2h 0m → 0% (before reset)",
    "English alert bodies are unchanged");
ExpectEqual(
    AlertBodies(AppLanguage.Korean),
    "Claude 마지막 측정 1h30m 전 | Claude 5h 19% <= 20% | Claude 7d 9% <= 10% | Claude 5h: ~2h 0m → 0% (리셋 전)",
    "Korean alert bodies are translated; the symbol-only low-balance bodies stay as they are");
ExpectEqual(
    string.Join(",", UsageAlertEvaluator.Candidates(alertSnapshot, new UsageAlertSettings(true, 20, 10, 10), now, new Localizer(AppLanguage.Korean)).Select(candidate => candidate.Id)),
    string.Join(",", alerts.Select(alert => alert.Id)),
    "Alert ids do not depend on the language");
ExpectEqual(
    string.Join(",", UsageForecastAlert.Candidates(new[] { forecastAlertInput }, enabled: true, localizer: new Localizer(AppLanguage.Korean)).Select(candidate => candidate.Id)),
    string.Join(",", UsageForecastAlert.Candidates(new[] { forecastAlertInput }, enabled: true).Select(candidate => candidate.Id)),
    "Forecast alert ids do not depend on the language");

// Sparkline
ExpectEqual(SparklineText.Render(new[] { 0, 25, 50, 75, 100 }), "▁▃▅▇█", "Sparkline maps values across the eight blocks");
ExpectEqual(SparklineText.Render(new[] { 42 }), "", "A single point renders nothing");
ExpectEqual(SparklineText.Render(Array.Empty<int>()), "", "An empty series renders nothing");
var sparkEntries = new[] { ForecastEntry(3600, 100), ForecastEntry(1800, 50), ForecastEntry(0, 0) };
ExpectEqual(string.Join(",", SparklineSeries.Build(sparkEntries, Provider.Claude, ForecastWindow.FiveHour)), "100,50,0", "Sparkline series extracts remaining values in time order");
ExpectEqual(SparklineText.Render(SparklineSeries.Build(sparkEntries, Provider.Claude, ForecastWindow.FiveHour)), "█▅▁", "End-to-end sparkline render");
var manySparkEntries = Enumerable.Range(0, 40).Select(i => ForecastEntry((40 - i) * 60, 100)).ToArray();
ExpectEqual(SparklineSeries.Build(manySparkEntries, Provider.Claude, ForecastWindow.FiveHour, 20).Count, 20, "Sparkline series is downsampled to maxPoints");

// Pause controller
var pauseNow = now;
Expect(PauseController.IsPaused(pauseNow.AddHours(1), pauseNow), "Paused while the resume instant is in the future");
Expect(!PauseController.IsPaused(pauseNow.AddSeconds(-10), pauseNow), "Not paused once the resume instant has passed");
Expect(!PauseController.IsPaused(null, pauseNow), "Not paused when unset");
ExpectEqual((int)PauseController.Remaining(pauseNow.AddMinutes(30), pauseNow).TotalSeconds, 1800, "Remaining reports seconds until resume");
ExpectEqual((int)PauseController.Remaining(null, pauseNow).TotalSeconds, 0, "Remaining is zero when not paused");
Expect(PauseController.IsIndefinite(DateTimeOffset.MaxValue, pauseNow), "A max-value pause is indefinite");
Expect(!PauseController.IsIndefinite(pauseNow.AddHours(1), pauseNow), "A timed pause is not indefinite");

var pausePath = Path.Combine(Path.GetTempPath(), "token-tracker-settings-" + Guid.NewGuid().ToString("N"), "settings.json");
var pauseStore = new SettingsStore(pausePath);
pauseStore.Save(new AppSettings { PollPausedUntil = pauseNow.AddHours(2) });
var reloadedPause = pauseStore.Load().PollPausedUntil;
Expect(reloadedPause is not null && reloadedPause.Value.ToUnixTimeSeconds() == pauseNow.AddHours(2).ToUnixTimeSeconds(), "PollPausedUntil round-trips through the settings store");
Directory.Delete(Path.GetDirectoryName(pausePath)!, recursive: true);

// Which credential store is live, and how long since anything wrote to it. The old
// diagnostic only asked whether the file existed, which says nothing about the
// question that matters when usage stops loading: is anything still refreshing it?
var credentialFilePath = Path.Combine(Path.GetTempPath(), "token-tracker-credentials-" + Guid.NewGuid().ToString("N") + ".json");
File.WriteAllText(credentialFilePath, "{}");

var keychainWrittenAt = now.AddHours(-48);
var fromKeychain = ClaudeCredentialSource.Detect(credentialFilePath, keychainWrittenAt);
ExpectEqual(fromKeychain.Kind, ClaudeCredentialStore.Keychain, "The keychain wins over the fallback file when both are present");
ExpectEqual(fromKeychain.LastWrittenAt, keychainWrittenAt, "The keychain's write time is reported");
ExpectEqual(fromKeychain.Age(now)!.Value.TotalHours, 48d, "Age measures from the last write");

var fromFile = ClaudeCredentialSource.Detect(credentialFilePath);
ExpectEqual(fromFile.Kind, ClaudeCredentialStore.CredentialsFile, "Without a keychain entry the fallback file is the source");
Expect(fromFile.LastWrittenAt is not null, "The fallback file reports its write time");

var noCredentialSource = ClaudeCredentialSource.Detect(credentialFilePath + ".missing");
ExpectEqual(noCredentialSource.Kind, ClaudeCredentialStore.None, "No keychain entry and no file means no source");
Expect(noCredentialSource.Age(now) is null, "A source that was never written has no age");
File.Delete(credentialFilePath);

// A store nothing has written for longer than a token lives is the whole answer to
// "I signed in and it still says sign in", so the menu says how stale it is. Only for
// a lapsed Claude token: no other failure is explained by the store's age.
var abandonedStore = new ClaudeCredentialSource(ClaudeCredentialStore.Keychain, now);
var expiredCredentialIssue = CredentialIssue(Provider.Claude, "HTTP 401", UsageSource.Unavailable);
ExpectEqual(
    CredentialStoreAdvice.StaleLine(Provider.Claude, expiredCredentialIssue, abandonedStore, now.AddHours(48)),
    "Credentials not refreshed in 2d 0h",
    "A lapsed token over an abandoned store reports how long it has been abandoned");
Expect(
    CredentialStoreAdvice.StaleLine(Provider.Claude, expiredCredentialIssue, abandonedStore, now.Add(CredentialStoreAdvice.StaleAfter).AddMinutes(-1)) is null,
    "A store younger than the token life explains nothing the recovery does not already say");
Expect(
    CredentialStoreAdvice.StaleLine(Provider.Claude, CredentialIssue(Provider.Claude, "HTTP 429", UsageSource.Unavailable), abandonedStore, now.AddHours(48)) is null,
    "A rate limit is not a credential problem, however old the store is");
Expect(
    CredentialStoreAdvice.StaleLine(Provider.Codex, CredentialIssue(Provider.Codex, "HTTP 401", UsageSource.Unavailable), abandonedStore, now.AddHours(48)) is null,
    "The Claude store says nothing about Codex");
// The state this is actually seen in: the last good reading is being carried, so the
// visible status is "using cached data" and only the error names the expiry.
Expect(
    CredentialStoreAdvice.StaleLine(Provider.Claude, CredentialIssue(Provider.Claude, "credentials expired", UsageSource.StaleCache), abandonedStore, now.AddHours(48)) is not null,
    "A lapsed token behind a carried reading still reports the stale store");
Expect(
    CredentialStoreAdvice.StaleLine(Provider.Claude, expiredCredentialIssue, new ClaudeCredentialSource(ClaudeCredentialStore.None, null), now.AddHours(48)) is null,
    "A store that was never written has no age to report");

// The tray app's own version line. Until the publish started stamping it, every
// build ever shipped reported 1.0.0 — including the diagnostics report whose job is
// to say which build the user is on.
ExpectEqual(AppVersionText.Format(new Version(1, 1, 6, 8)), "1.1.6 (build 8)", "A stamped release publish names its build");
ExpectEqual(AppVersionText.Format(new Version(1, 1, 6, 0)), "1.1.6", "A local build leaves the revision unstamped and shows just the version");
ExpectEqual(AppVersionText.Format(new Version(1, 1, 6)), "1.1.6", "A three-part version has no build to show");
ExpectEqual(AppVersionText.Format(null), "unknown", "An assembly with no version is reported as unknown");

// The status-line bridge. Same truth table as the macOS smoke tests: a reading
// relayed from Claude Code's status line is account data, but it was measured at
// a time this app did not choose, so it is aged by its own measurement time and
// never by the moment the bridge last ran.
var statusLineNow = new DateTimeOffset(2026, 5, 27, 12, 0, 0, TimeSpan.Zero);
var statusLineReset5h = statusLineNow.AddHours(1);
var statusLineReset7d = statusLineNow.AddDays(1);

var statusLineRecord = ClaudeStatusLineBridge.Parse(
    StatusLinePayload(statusLineNow, statusLineNow, 23.5, statusLineReset5h, 41.2, statusLineReset7d),
    statusLineNow);
ExpectEqual(statusLineRecord?.MeasuredAt, statusLineNow, "The measurement time is read from the record");
var statusLineUsage = ClaudeStatusLineBridge.Usage(statusLineRecord, null, statusLineNow);
ExpectEqual(statusLineUsage?.Source, UsageSource.StatusLine, "A status-line reading is not labelled as an API measurement");
ExpectEqual(statusLineUsage?.RemainingPercent5h, 77, "23.5% used leaves 77% of the 5h window");
ExpectEqual(statusLineUsage?.RemainingPercent7d, 59, "41.2% used leaves 59% of the 7d window");
ExpectEqual(statusLineUsage?.ResetAt5h, statusLineReset5h, "The 5h reset instant carries through");
ExpectEqual(statusLineUsage?.ResetAt7d, statusLineReset7d, "The 7d reset instant carries through");
ExpectEqual(statusLineUsage?.UpdatedAt, statusLineNow, "The reading is stamped with the measurement time, not the poll time");

// Either window can be absent, and one window is enough.
var fiveHourOnly = ClaudeStatusLineBridge.Usage(
    ClaudeStatusLineBridge.Parse(
        StatusLinePayload(statusLineNow, statusLineNow, 23.5, statusLineReset5h, null, null),
        statusLineNow),
    null,
    statusLineNow);
ExpectEqual(fiveHourOnly?.RemainingPercent5h, 77, "A record with only the 5h window still reports it");
ExpectEqual(fiveHourOnly?.RemainingPercent7d, (int?)null, "An absent 7d window reports nothing rather than zero");
var sevenDayOnly = ClaudeStatusLineBridge.Usage(
    ClaudeStatusLineBridge.Parse(
        StatusLinePayload(statusLineNow, statusLineNow, null, null, 41.2, statusLineReset7d),
        statusLineNow),
    null,
    statusLineNow);
ExpectEqual(sevenDayOnly?.RemainingPercent7d, 59, "A record with only the 7d window still reports it");
Expect(
    ClaudeStatusLineBridge.Parse(
        StatusLinePayload(statusLineNow, statusLineNow, null, null, null, null),
        statusLineNow) is null,
    "A record with neither window is not a reading");

// Claude Code drops a window once its reset passes, so one still sitting in the
// file describes a window instance that no longer exists.
var expiredWindow = ClaudeStatusLineBridge.Parse(
    StatusLinePayload(statusLineNow, statusLineNow, 99, statusLineNow.AddSeconds(-1), 41.2, statusLineReset7d),
    statusLineNow);
ExpectEqual(expiredWindow?.UsedPercent5h, (double?)null, "A window whose reset has passed is dropped");
ExpectEqual(expiredWindow?.UsedPercent7d, 41.2, "Dropping one expired window keeps the other");

// A malformed or future record is not a reading. Ageing a measurement stamped in
// the future would make it look permanently fresh.
Expect(ClaudeStatusLineBridge.Parse("{", statusLineNow) is null, "Truncated JSON is not a reading");
Expect(
    ClaudeStatusLineBridge.Parse(
        StatusLinePayload(statusLineNow, statusLineNow, 23.5, statusLineReset5h, 41.2, statusLineReset7d, version: 2),
        statusLineNow) is null,
    "An unknown format version is not read");
Expect(
    ClaudeStatusLineBridge.Parse(
        StatusLinePayload(null, statusLineNow, 23.5, statusLineReset5h, 41.2, statusLineReset7d),
        statusLineNow) is null,
    "A record without a measurement time is not read");
Expect(
    ClaudeStatusLineBridge.Parse(
        StatusLinePayload(
            statusLineNow + ClaudeStatusLineBridge.ClockSkew + TimeSpan.FromMinutes(1),
            statusLineNow,
            23.5,
            statusLineReset5h,
            41.2,
            statusLineReset7d),
        statusLineNow) is null,
    "A measurement stamped in the future is not read");

// The trap this whole design exists to avoid: the status-line command re-runs on
// a timer, and a re-run is not a new measurement. written_at moves, so a reading
// aged by it would never expire; measured_at does not, so it does.
var replayed = ClaudeStatusLineBridge.Parse(
    StatusLinePayload(statusLineNow.AddHours(-2), statusLineNow, 23.5, statusLineReset5h, 41.2, statusLineReset7d),
    statusLineNow);
ExpectEqual(replayed?.WrittenAt, statusLineNow, "The replay time is recorded");
Expect(
    ClaudeStatusLineBridge.Usage(replayed, null, statusLineNow) is null,
    "Replaying an old measurement does not make it current");
var atFreshnessEdge = ClaudeStatusLineBridge.Parse(
    StatusLinePayload(
        statusLineNow - ClaudeStatusLineBridge.FreshnessWindow + TimeSpan.FromSeconds(1),
        statusLineNow,
        23.5,
        statusLineReset5h,
        41.2,
        statusLineReset7d),
    statusLineNow);
Expect(
    ClaudeStatusLineBridge.Usage(atFreshnessEdge, null, statusLineNow) is not null,
    "A measurement inside the freshness window is still current");

// A stale status-line reading hands over to the ordinary cache path rather than
// presenting itself as live: the last good value keeps showing, but labelled as
// cached, and it expires on the measurement's own clock.
var agedStatusLineReading = new ProviderUsage(
    Provider.Claude,
    77,
    59,
    statusLineReset5h,
    statusLineReset7d,
    UsageSource.StatusLine,
    Error: null,
    Plan: null,
    Model: null,
    UpdatedAt: statusLineNow.AddHours(-1));
var statusLineFailure = new UsageSnapshot(
    ProviderUsage.Unavailable(Provider.Claude, "Credentials expired for Claude API"),
    ProviderUsage.Unavailable(Provider.Codex, "Timed out"),
    statusLineNow);
var statusLineCache = new UsageSnapshot(
    agedStatusLineReading,
    ProviderUsage.Unavailable(Provider.Codex, "Timed out"),
    statusLineNow);
var carriedStatusLine = UsageSnapshotCachePolicy.Apply(
    statusLineFailure, statusLineCache, updatedAt: statusLineNow, maxAge: TimeSpan.FromHours(12));
ExpectEqual(carriedStatusLine.Claude.Source, UsageSource.StaleCache, "An aged-out status-line reading is carried as cached data");
ExpectEqual(carriedStatusLine.Claude.RemainingPercent5h, 77, "The last status-line value keeps showing while it is in tolerance");
ExpectEqual(carriedStatusLine.Claude.UpdatedAt, agedStatusLineReading.UpdatedAt, "Carrying it forward does not refresh its measurement time");
var droppedStatusLine = UsageSnapshotCachePolicy.Apply(
    statusLineFailure, statusLineCache, updatedAt: statusLineNow, maxAge: TimeSpan.FromMinutes(30));
ExpectEqual(droppedStatusLine.Claude.Source, UsageSource.Unavailable, "Past tolerance the status-line reading is gone, not frozen");

// History records measurements this app took and timestamps them with the poll it
// took them on. A status-line reading is re-read on every poll, so admitting it
// would append one measurement under a run of fresh timestamps and tell the
// forecast that usage had stopped moving.
var liveStatusLineReading = agedStatusLineReading with { UpdatedAt = statusLineNow };
var statusLineForHistory = UsageHistoryPolicy.MeasurementsOnly(new UsageSnapshot(
    liveStatusLineReading,
    ProviderUsage.Unavailable(Provider.Codex, "Timed out"),
    statusLineNow));
ExpectEqual(statusLineForHistory.Claude.RemainingPercent5h, (int?)null, "A status-line reading does not enter history");
ExpectEqual(statusLineForHistory.Claude.RemainingPercent7d, (int?)null, "A status-line reading does not enter the trend or forecast");

// The menu says where the number came from, and still names the fix for whatever
// stopped the API path.
var statusLineIssue = UsageIssueFormatter.Issue(
    liveStatusLineReading with { Error = "Credentials expired for Claude API" },
    new Localizer(AppLanguage.English));
ExpectEqual(statusLineIssue.Kind, UsageIssueKind.StatusLine, "A status-line reading reads as its own status, not as OK");
ExpectEqual(statusLineIssue.TechnicalDetail, "Credentials expired for Claude API", "The reason the API path failed is kept");
ExpectEqual(
    statusLineIssue.Recovery,
    new Localizer(AppLanguage.English).Text(L10nKey.RecoverySignInAgainClaude),
    "Showing status-line numbers still tells the user to sign in again");

// Windows has no key-to-English fallback: a missing Korean string shows the enum
// name to the user.
foreach (var statusLineLanguage in new[] { AppLanguage.English, AppLanguage.Korean })
{
    var statusLineLocalizer = new Localizer(statusLineLanguage);
    foreach (var key in new[] { L10nKey.StatusFromStatusLine, L10nKey.StatusFromStatusLineDetail })
    {
        Expect(
            statusLineLocalizer.Text(key) != key.ToString(),
            $"{statusLineLanguage} has a string for {key}");
    }
}

RunStatusLineBridgeScriptTests();

// Pace: one measurement against the line of using the window evenly to its reset.
var paceNow = new DateTimeOffset(2027, 1, 15, 8, 0, 0, TimeSpan.Zero);
UsagePaceReading? PaceAt(int? remaining, double? resetInSeconds, ForecastWindow window = ForecastWindow.FiveHour)
{
    DateTimeOffset? resetAt = resetInSeconds is { } seconds ? paceNow.AddSeconds(seconds) : null;
    return UsagePaceEvaluator.Reading(remaining, resetAt, window, paceNow);
}

ExpectEqual(PaceAt(30, 9000)?.Pace, UsagePace.Ahead, "half the window gone with 70% used is ahead of pace");
ExpectEqual(PaceAt(50, 9000)?.Pace, UsagePace.OnTrack, "half gone with half used is on track");
ExpectEqual(PaceAt(41, 9000)?.Pace, UsagePace.OnTrack, "nine points under the line is within the tolerance");
ExpectEqual(PaceAt(39, 9000)?.Pace, UsagePace.Ahead, "eleven points under the line is ahead");
ExpectEqual(PaceAt(75, 9000)?.Pace, UsagePace.Behind, "half gone with a quarter used is behind pace");
ExpectEqual(PaceAt(50, 9000)?.ExpectedRemainingPercent, 50, "the even line sits at the share of the window still to run");
ExpectEqual(PaceAt(80, 302_400, ForecastWindow.SevenDay)?.Pace, UsagePace.Behind, "the 7d window is measured against its own length");
Expect(PaceAt(50, null) is null, "without a reset there is no line to compare against");
Expect(PaceAt(50, -60) is null, "a window past its reset has no pace");
Expect(PaceAt(50, 20_000) is null, "a reset further off than the window is long is not placed");
Expect(PaceAt(null, 9000) is null, "without a balance there is no pace");

var pacedUsage = new ProviderUsage(
    Provider.Claude,
    30,
    75,
    paceNow.AddSeconds(9000),
    paceNow.AddSeconds(302_400),
    UsageSource.Api,
    Error: null,
    Plan: null,
    Model: null,
    UpdatedAt: paceNow);
ExpectEqual(
    UsagePaceText.MenuLine(pacedUsage, new Localizer(AppLanguage.Korean)),
    "페이스: 5h 빠름 · 7d 여유",
    "the pace line names each window in Korean");
ExpectEqual(
    UsagePaceText.MenuLine(pacedUsage, new Localizer(AppLanguage.English)),
    "Pace: 5h ahead · 7d under",
    "the pace line names each window in English");
var cachedPacedUsage = pacedUsage with { Source = UsageSource.StaleCache };
Expect(UsagePaceEvaluator.Reading(cachedPacedUsage, ForecastWindow.FiveHour) is null, "a cached reading gets no pace");
Expect(UsagePaceText.MenuLine(cachedPacedUsage, new Localizer(AppLanguage.English)) is null, "nor a pace line");
var bridgedPacedUsage = pacedUsage with { Source = UsageSource.StatusLine };
ExpectEqual(
    UsagePaceEvaluator.Reading(bridgedPacedUsage, ForecastWindow.FiveHour)?.Pace,
    UsagePace.Ahead,
    "a status-line measurement is paced at its own time");

// Freed up: a window that had run low, measured again after its reset.
var freedNow = new DateTimeOffset(2027, 1, 15, 8, 0, 0, TimeSpan.Zero);
var freedSettings = new UsageAlertSettings(true, 20, 10, 0);
ProviderUsage ClaudeReading(int? remaining5h, DateTimeOffset? reset5h, DateTimeOffset at, UsageSource source = UsageSource.Api) =>
    new(Provider.Claude, remaining5h, 80, reset5h, null, source, null, null, null, at);
UsageSnapshot ClaudeOnlySnapshot(ProviderUsage claude) =>
    new(claude, ProviderUsage.Unavailable(Provider.Codex, "Disabled"), claude.UpdatedAt);
string FreedIds(ProviderUsage? before, ProviderUsage after, UsageAlertSettings? settings = null)
{
    var previous = new Dictionary<Provider, ProviderUsage>();
    if (before is not null)
    {
        previous[Provider.Claude] = before;
    }

    return string.Join(
        ",",
        UsageAlertEvaluator.FreedUpCandidates(previous, ClaudeOnlySnapshot(after), settings ?? freedSettings)
            .Select(candidate => candidate.Id));
}

var passedReset = freedNow.AddSeconds(-60);
var lowBeforeReset = ClaudeReading(8, passedReset, freedNow.AddSeconds(-600));
var refilled = ClaudeReading(100, null, freedNow);
ExpectEqual(FreedIds(lowBeforeReset, refilled), $"claude-5h-freed-{passedReset.ToUnixTimeSeconds()}", "a low window measured again after its reset is reported freed");
ExpectEqual(FreedIds(ClaudeReading(50, passedReset, freedNow.AddSeconds(-600)), refilled), "", "a window that never ran low is no news when it resets");
ExpectEqual(
    FreedIds(ClaudeReading(8, freedNow.AddSeconds(600), freedNow.AddSeconds(-600)), ClaudeReading(30, freedNow.AddSeconds(600), freedNow)),
    "",
    "a balance cannot refill before its reset has passed");
ExpectEqual(FreedIds(lowBeforeReset, ClaudeReading(100, null, freedNow, UsageSource.StaleCache)), "", "only a measurement can say the window refilled");
ExpectEqual(FreedIds(lowBeforeReset, ClaudeReading(15, null, freedNow)), "", "still under the threshold after the reset is not freed");
ExpectEqual(FreedIds(null, refilled), "", "with no earlier reading there is nothing to compare");
ExpectEqual(
    FreedIds(lowBeforeReset, refilled, new UsageAlertSettings(true, 0, 10, 0)),
    "",
    "a zero threshold turns the freed alert off with the low alert");
ExpectEqual(
    UsageAlertEvaluator.FreedUpCandidates(
        new Dictionary<Provider, ProviderUsage> { [Provider.Claude] = lowBeforeReset },
        ClaudeOnlySnapshot(refilled),
        freedSettings,
        new Localizer(AppLanguage.Korean)).FirstOrDefault()?.Body,
    "Claude 5h 리셋됨 — 잔량 100%",
    "the freed alert reads in Korean");

var seededReadings = UsageAlertEvaluator.LastReadings(
    new Dictionary<Provider, ProviderUsage>(),
    ClaudeOnlySnapshot(ClaudeReading(8, passedReset, freedNow, UsageSource.StaleCache)));
ExpectEqual(seededReadings[Provider.Claude].RemainingPercent5h, 8, "a cached reading seeds an empty map");
Expect(!seededReadings.ContainsKey(Provider.Codex), "a provider with no numbers is not recorded");
var keptThroughOutage = UsageAlertEvaluator.LastReadings(
    new Dictionary<Provider, ProviderUsage> { [Provider.Claude] = lowBeforeReset },
    ClaudeOnlySnapshot(ClaudeReading(8, passedReset, freedNow, UsageSource.StaleCache)));
ExpectEqual(keptThroughOutage[Provider.Claude], lowBeforeReset, "a cached reading does not replace a measurement");
ExpectEqual(
    UsageAlertEvaluator.LastReadings(new Dictionary<Provider, ProviderUsage> { [Provider.Claude] = lowBeforeReset }, ClaudeOnlySnapshot(refilled))[Provider.Claude],
    refilled,
    "a measurement replaces the last reading");

// Update check: what GitHub names as the latest release, and whether it is newer.
ExpectEqual(UpdateCheck.LatestVersion("{\"tag_name\":\"v1.1.17\",\"draft\":false,\"prerelease\":false}"), "1.1.17", "a release tag reads without its v");
Expect(UpdateCheck.LatestVersion("{\"tag_name\":\"v1.2.0\",\"prerelease\":true}") is null, "a prerelease is not offered");
Expect(UpdateCheck.LatestVersion("{\"tag_name\":\"v1.2.0\",\"draft\":true}") is null, "a draft is not offered");
Expect(UpdateCheck.LatestVersion("{\"tag_name\":\"v1.2.0-beta\"}") is null, "a tag that is not plain numbers is not offered");
Expect(UpdateCheck.LatestVersion("{\"message\":\"Not Found\"}") is null, "an error body has no version");
Expect(UpdateCheck.LatestVersion("not json") is null, "a body that is not JSON has no version");
Expect(UpdateCheck.IsNewer("1.1.17", "1.1.16"), "a later patch is newer");
Expect(UpdateCheck.IsNewer("1.2", "1.1.16"), "a later minor is newer whatever the patch");
Expect(UpdateCheck.IsNewer("1.1.10", "1.1.9"), "versions compare as numbers, not text");
Expect(!UpdateCheck.IsNewer("1.1.16", "1.1.16"), "the same version is not newer");
Expect(!UpdateCheck.IsNewer("1.1.15", "1.1.16"), "an older version is not newer");
Expect(!UpdateCheck.IsNewer("1.1.17", "unknown"), "a build that cannot name its version is never prompted");
var updateAttempt = new DateTimeOffset(2027, 1, 15, 8, 0, 0, TimeSpan.Zero);
ExpectEqual(UpdateCheck.NextCheck(updateAttempt, true), updateAttempt.AddHours(24), "a good check waits a day");
ExpectEqual(UpdateCheck.NextCheck(updateAttempt, false), updateAttempt.AddHours(1), "a failed check is retried within the hour");
Expect(UpdateCheck.IsDue(null, updateAttempt), "the first check is due at once");
Expect(!UpdateCheck.IsDue(updateAttempt.AddMinutes(1), updateAttempt), "a check is not due before its time");
Expect(UpdateCheck.IsDue(updateAttempt, updateAttempt), "a check is due at its time");
ExpectEqual(UpdateCheck.CurrentVersion(new Version(1, 1, 16, 18)), "1.1.16", "the running build names itself the way tags do");
Expect(UpdateCheck.CurrentVersion(null) is null, "an assembly with no version names none");

using (var releaseClient = new HttpClient(new QueueHttpMessageHandler(new HttpResponseMessage(HttpStatusCode.OK)
       {
           Content = new StringContent("{\"tag_name\":\"v1.1.17\"}")
       })))
{
    ExpectEqual(await UpdateCheck.FetchLatestVersionAsync(releaseClient), "1.1.17", "a release response yields its version");
}

using (var limitedClient = new HttpClient(new QueueHttpMessageHandler(new HttpResponseMessage(HttpStatusCode.Forbidden))))
{
    Expect(await UpdateCheck.FetchLatestVersionAsync(limitedClient) is null, "a refused check yields nothing rather than an error");
}

Expect(new AppSettings().CheckForUpdates, "update checks are on by default");
var updateSettingsPath = Path.Combine(Path.GetTempPath(), "token-tracker-update-settings-" + Guid.NewGuid().ToString("N"), "settings.json");
Directory.CreateDirectory(Path.GetDirectoryName(updateSettingsPath)!);
File.WriteAllText(updateSettingsPath, "{\"RefreshIntervalSeconds\":300}");
Expect(new SettingsStore(updateSettingsPath).Load().CheckForUpdates, "a settings file from before the update check lands on the default");
Directory.Delete(Path.GetDirectoryName(updateSettingsPath)!, recursive: true);

// Renewing a lapsed Claude token through Claude Code: when to try again, and what
// the CLI is started with. Mirrors Sources/TokenTrackerSmokeTests/ClaudeCLIRefreshTests.swift.
var renewalAttemptAt = new DateTimeOffset(2027, 1, 15, 8, 0, 0, TimeSpan.Zero);
var renewedAttempt = new ClaudeCliRefreshAttempt(renewalAttemptAt, ClaudeCliRefreshOutcome.Refreshed);
var unchangedAttempt = new ClaudeCliRefreshAttempt(renewalAttemptAt, ClaudeCliRefreshOutcome.Unchanged);
var notFoundAttempt = new ClaudeCliRefreshAttempt(renewalAttemptAt, ClaudeCliRefreshOutcome.CliNotFound);
Expect(ClaudeCliRefresh.IsDue(null, renewalAttemptAt), "the first renewal is due at once");
ExpectEqual(ClaudeCliRefresh.NextAttempt(renewedAttempt), renewalAttemptAt.AddMinutes(5), "a renewal waits five minutes before another");
ExpectEqual(ClaudeCliRefresh.NextAttempt(unchangedAttempt), renewalAttemptAt.AddMinutes(30), "a renewal that changed nothing waits half an hour");
ExpectEqual(ClaudeCliRefresh.NextAttempt(notFoundAttempt), renewalAttemptAt.AddMinutes(30), "a missing CLI is looked for again in half an hour");
Expect(!ClaudeCliRefresh.IsDue(unchangedAttempt, renewalAttemptAt.AddSeconds(1799)), "a failed renewal is not retried early");
Expect(ClaudeCliRefresh.IsDue(unchangedAttempt, renewalAttemptAt.AddSeconds(1800)), "a failed renewal is retried on time");

Expect(
    ClaudeCliRefresh.Arguments.SequenceEqual(new[] { "--strict-mcp-config", "--settings", "{\"remoteControlAtStartup\":false,\"disableAllHooks\":true}" }),
    "the launch turns off hooks, MCP servers and Remote Control");

var cliEnvironment = ClaudeCliRefresh.LaunchEnvironment(
    new Dictionary<string, string>
    {
        ["Path"] = @"C:\Windows\System32",
        ["USERPROFILE"] = @"C:\Users\x",
        ["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:1",
        ["ClaudeCode"] = "1",
        ["CLAUDE_CODE_OAUTH_TOKEN"] = "token",
        ["CLAUDE_CONFIG_DIR"] = @"D:\elsewhere"
    },
    @"C:\Users\x\.local\bin");
ExpectEqual(cliEnvironment["PATH"], @"C:\Users\x\.local\bin" + Path.PathSeparator + @"C:\Windows\System32", "the CLI's own directory leads PATH, whatever its case");
ExpectEqual(cliEnvironment.Keys.Count(key => key.Equals("PATH", StringComparison.OrdinalIgnoreCase)), 1, "PATH is set once");
ExpectEqual(cliEnvironment["USERPROFILE"], @"C:\Users\x", "the rest of the environment is kept");
Expect(
    cliEnvironment.Keys.All(key =>
        !key.StartsWith("ANTHROPIC_", StringComparison.OrdinalIgnoreCase) &&
        !key.StartsWith("CLAUDE", StringComparison.OrdinalIgnoreCase)),
    "nothing points the CLI at other credentials or another config");
ExpectEqual(cliEnvironment["DISABLE_AUTOUPDATER"], "1", "a renewal never updates the CLI");
ExpectEqual(cliEnvironment["TERM"], "xterm-256color", "the terminal gets a type");
ExpectEqual(
    ClaudeCliRefresh.LaunchEnvironment(new Dictionary<string, string> { ["TERM"] = "dumb" }, @"C:\b")["TERM"],
    "dumb",
    "a terminal type already set is kept");
ExpectEqual(
    ClaudeCliRefresh.LaunchEnvironment(new Dictionary<string, string>(), @"C:\b")["PATH"],
    @"C:\b",
    "no PATH becomes the CLI's directory alone");

var cliCandidates = ClaudeCliRefresh.CandidatePaths(
    @"C:\Users\x",
    string.Join(Path.PathSeparator, @"C:\Tools", "\"C:\\Program Files\\Claude\"", "", @"c:\tools"));
Expect(
    cliCandidates.SequenceEqual(new[]
    {
        Path.Combine(@"C:\Users\x", ".local", "bin", "claude.exe"),
        Path.Combine(@"C:\Tools", "claude.exe"),
        Path.Combine(@"C:\Program Files\Claude", "claude.exe")
    }),
    "the native install comes first, then PATH unquoted, each once");

Expect(new AppSettings().RenewClaudeTokenWithCli, "Claude token renewal is on by default");
var renewalSettingsPath = Path.Combine(Path.GetTempPath(), "token-tracker-renewal-settings-" + Guid.NewGuid().ToString("N"), "settings.json");
Directory.CreateDirectory(Path.GetDirectoryName(renewalSettingsPath)!);
File.WriteAllText(renewalSettingsPath, "{\"RefreshIntervalSeconds\":300}");
Expect(new SettingsStore(renewalSettingsPath).Load().RenewClaudeTokenWithCli, "a settings file from before renewal lands on the default");
Directory.Delete(Path.GetDirectoryName(renewalSettingsPath)!, recursive: true);

RunCostTests();
RunAdminCostTests();
RunCostDisplayTests();
RunCostAlertTests();

Console.WriteLine("TokenTracker.Windows.Tests passed");

// Money display: local-time boundaries in several zones, both Admin API parsers,
// the subscription fields, and the rule that keeps money off a subscription-only
// user's screen. Mirrors Sources/TokenTrackerSmokeTests/CostTests.swift.
static void RunCostTests()
{
    var seoul = TimeZoneInfo.FindSystemTimeZoneById("Asia/Seoul");
    var newYork = TimeZoneInfo.FindSystemTimeZoneById("America/New_York");
    var kolkata = TimeZoneInfo.FindSystemTimeZoneById("Asia/Kolkata");
    var utc = TimeZoneInfo.Utc;
    static DateTimeOffset At(string text) => DateTimeOffset.Parse(text, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal);
    static decimal Dollars(string text) => decimal.Parse(text, NumberStyles.Float, CultureInfo.InvariantCulture);
    static string Iso(DateTimeOffset date) => date.UtcDateTime.ToString("yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture);

    // Boundaries follow the zone the device is in, whichever it is.
    var now = At("2026-11-03T05:00:00Z");
    ExpectEqual(CostPeriod.DayStart(now, seoul), At("2026-11-02T15:00:00Z"), "Seoul's day starts at its own midnight");
    ExpectEqual(CostPeriod.MonthStart(now, seoul), At("2026-10-31T15:00:00Z"), "Seoul's month starts at its own midnight on the 1st");
    ExpectEqual(CostPeriod.DayStart(now, newYork), At("2026-11-03T05:00:00Z"), "An instant exactly at local midnight starts that day");
    ExpectEqual(CostPeriod.MonthStart(now, newYork), At("2026-11-01T04:00:00Z"), "New York's month started under daylight time");
    ExpectEqual(CostPeriod.DayStart(now, kolkata), At("2026-11-02T18:30:00Z"), "A half-hour zone keeps its half hour");
    ExpectEqual(CostPeriod.DayStart(now, utc), At("2026-11-03T00:00:00Z"), "UTC is one zone among others");
    ExpectEqual(
        CostPeriod.DayStart(At("2026-11-02T12:00:00Z"), newYork) - CostPeriod.DayStart(At("2026-11-01T12:00:00Z"), newYork),
        TimeSpan.FromHours(25),
        "The day daylight time ends has 25 hours");
    ExpectEqual(
        CostPeriod.DayStart(At("2026-03-09T12:00:00Z"), newYork) - CostPeriod.DayStart(At("2026-03-08T12:00:00Z"), newYork),
        TimeSpan.FromHours(23),
        "The day daylight time starts has 23 hours");
    ExpectEqual(CostPeriod.MonthKey(At("2026-11-30T16:00:00Z"), seoul), "2026-12", "Seoul is already in December");
    ExpectEqual(CostPeriod.MonthKey(At("2026-11-30T16:00:00Z"), utc), "2026-11", "UTC is still in November");
    ExpectEqual(CostPeriod.RequestRange(now, seoul).Start, At("2026-10-31T15:00:00Z"), "A fetch asks from the local 1st");

    // Anthropic Analytics: hourly buckets, cents as decimal strings.
    static string AnthropicHourly(int hours, string start, string cents, string lastCents, string? refreshed)
    {
        var first = At(start);
        var rows = new List<string>();
        for (var hour = 0; hour < hours; hour++)
        {
            var begin = first.AddHours(hour);
            var amount = hour == hours - 1 ? lastCents : cents;
            rows.Add("{\"starting_at\":\"" + Iso(begin) + "\",\"ending_at\":\"" + Iso(begin.AddHours(1)) +
                "\",\"group\":{\"model\":null},\"cost\":{\"currency\":\"USD\",\"amount\":\"" + amount +
                "\",\"list_amount\":\"" + cents + "\",\"requests\":3}}");
        }

        var refreshedJson = refreshed is null ? "null" : "\"" + refreshed + "\"";
        return "{\"data\":[" + string.Join(",", rows) + "],\"has_more\":false,\"next_page\":null,\"data_refreshed_at\":" + refreshedJson + "}";
    }

    var hourly = AnthropicCostParser.Page(AnthropicHourly(16, "2026-11-02T13:00:00Z", "100.000000", "33.333333", "2026-11-03T04:30:00Z"))!;
    ExpectEqual(hourly.Buckets.Count, 16, "Every hourly row becomes a bucket");
    ExpectEqual(hourly.Freshness, CostFreshness.At(At("2026-11-03T04:30:00Z")), "The refresh time is kept");
    Expect(hourly.NextPage is null, "A last page has no next page");
    var seoulReport = CostAggregator.Report(hourly.Buckets, hourly.Freshness, now, seoul);
    ExpectEqual(seoulReport.MonthToDate?.Value, Dollars("15.33333333"), "Cents add up exactly across the month");
    ExpectEqual(seoulReport.Today?.Value, Dollars("13.33333333"), "Seoul's today starts at 15:00 UTC");
    ExpectEqual(CostAggregator.Report(hourly.Buckets, hourly.Freshness, now, utc).Today?.Value, Dollars("4.33333333"), "UTC's today starts at 00:00 UTC");
    ExpectEqual(CostAggregator.Report(hourly.Buckets, hourly.Freshness, now, kolkata).Today?.Value, Dollars("10.33333333"), "A half-hour zone takes the hour it starts in");
    var stale = CostAggregator.Report(hourly.Buckets, CostFreshness.At(At("2026-11-02T14:00:00Z")), now, seoul);
    Expect(stale.Today is null, "A day the data has not reached yet is not tallied, not zero");
    ExpectEqual(stale.MonthToDate?.Value, Dollars("15.33333333"), "The month still adds up what there is");
    var beforeMonth = CostAggregator.Report(Array.Empty<CostBucket>(), CostFreshness.At(At("2026-10-31T12:00:00Z")), now, seoul);
    Expect(beforeMonth.MonthToDate is null && beforeMonth.Today is null, "A watermark from before the 1st has tallied none of this month");
    var empty = AnthropicCostParser.Page("{\"data\":[],\"has_more\":false,\"next_page\":null,\"data_refreshed_at\":null}")!;
    ExpectEqual(empty.Freshness, CostFreshness.NotTallied, "No refresh time means nothing is tallied");
    var untallied = CostAggregator.Report(empty.Buckets, empty.Freshness, now, seoul);
    Expect(untallied.MonthToDate is null && untallied.Today is null, "Nothing tallied shows no amounts at all");

    var tinyPage = AnthropicCostParser.Page(AnthropicHourly(744, "2026-10-01T00:00:00Z", "0.100000", "0.100000", "2026-11-01T00:00:00Z"))!;
    ExpectEqual(
        CostAggregator.Report(tinyPage.Buckets, tinyPage.Freshness, At("2026-10-31T23:59:59Z"), utc).MonthToDate?.Value,
        Dollars("0.744"),
        "744 tenths of a cent add up to exactly $0.744");

    var models = AnthropicCostParser.Page("""
        {"data":[
          {"starting_at":"2026-11-01T00:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-sonnet-5-5"},"cost":{"currency":"USD","amount":"412.500000"}},
          {"starting_at":"2026-11-01T00:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-opus-5-5"},"cost":{"currency":"USD","amount":"1120.000000"}},
          {"starting_at":"2026-11-01T00:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":null},"cost":{"currency":"USD","amount":"12.000000"}}
        ],"has_more":true,"next_page":"page_2","data_refreshed_at":"2026-11-03T04:30:00Z"}
        """)!;
    ExpectEqual(models.NextPage, "page_2", "A page with more says where to go next");
    ExpectEqual(
        string.Join(",", CostLineItem.Top(models.Items).Select(item => item.Name)),
        "claude-opus-5-5,claude-sonnet-5-5",
        "Models rank by spend; a row with no model is not one");
    ExpectEqual(CostLineItem.Top(models.Items)[0].Amount.Value, Dollars("11.2"), "A model's cents become dollars");
    Expect(AnthropicCostParser.Page("{\"error\":{\"type\":\"permission_error\"}}") is null, "An error body is not a page");

    // OpenAI: day buckets in dollars, under "results" or "result".
    var utcDays = OpenAICostParser.Page("""
        {"object":"page","data":[
          {"object":"bucket","start_time":1793404800,"end_time":1793491200,"results":[{"object":"organization.costs.result","amount":{"value":5.0,"currency":"usd"},"line_item":"gpt-6, input"}]},
          {"object":"bucket","start_time":1793491200,"end_time":1793577600,"results":[{"amount":{"value":1.1,"currency":"usd"},"line_item":"gpt-6, input"},{"amount":{"value":0.06,"currency":"usd"},"line_item":"web search"}]},
          {"object":"bucket","start_time":1793577600,"end_time":1793664000,"result":[{"amount":{"value":2.2,"currency":"usd"},"line_item":"gpt-6, output"}]},
          {"object":"bucket","start_time":1793664000,"end_time":1793750400,"results":[{"amount":{"value":0.3,"currency":"usd"},"line_item":"gpt-6, output"}]}
        ],"has_more":false,"next_page":null}
        """)!;
    ExpectEqual(utcDays.Buckets.Count, 4, "Every day bucket is read, whichever name its list has");
    ExpectEqual(utcDays.Buckets[1].Amount, new CostAmount(Dollars("1.16"), "USD"), "A bucket's results add up, in upper-case currency");
    var openAIInSeoul = CostAggregator.Report(utcDays.Buckets, utcDays.Freshness, now, seoul);
    Expect(openAIInSeoul.Today is null, "A UTC day bucket cannot say what Seoul spent today");
    ExpectEqual(openAIInSeoul.MonthToDate?.Value, Dollars("8.66"), "The month counts every bucket that reaches into it");
    var openAIInUtc = CostAggregator.Report(utcDays.Buckets, utcDays.Freshness, now, utc);
    ExpectEqual(openAIInUtc.Today?.Value, Dollars("0.3"), "UTC day buckets give UTC its today");
    ExpectEqual(openAIInUtc.MonthToDate?.Value, Dollars("3.66"), "A bucket that ends where the month starts is last month's");
    var localDays = OpenAICostParser.Page("""
        {"data":[
          {"start_time":1793458800,"end_time":1793545200,"results":[{"amount":{"value":1.0,"currency":"usd"}}]},
          {"start_time":1793545200,"end_time":1793631600,"results":[{"amount":{"value":2.0,"currency":"usd"}}]},
          {"start_time":1793631600,"end_time":1793718000,"results":[{"amount":{"value":0.25,"currency":"usd"}}]}
        ],"has_more":false}
        """)!;
    var openAILocal = CostAggregator.Report(localDays.Buckets, localDays.Freshness, now, seoul);
    ExpectEqual(openAILocal.Today?.Value, Dollars("0.25"), "Buckets cut at Seoul's midnight give Seoul its today");
    ExpectEqual(openAILocal.MonthToDate?.Value, Dollars("3.25"), "And its month");
    ExpectEqual(
        string.Join(",", CostLineItem.Top(utcDays.Items).Select(item => item.Name)),
        "gpt-6, input,gpt-6, output,web search",
        "Line items merge across buckets");

    // Subscription money appears only for accounts that pay it.
    const string proAccount = """
        {"extra_usage":{"is_enabled":false,"monthly_limit":null,"used_credits":null,"utilization":null,"currency":null,"decimal_places":null,"user_disabled":true},
         "spend":{"used":{"amount_minor":0,"currency":"USD","exponent":2},"limit":null,"percent":0,"severity":"normal","enabled":false}}
        """;
    Expect(SubscriptionCostParser.ClaudeExtra(proAccount) is null, "A subscription with extra usage off shows no money");
    var extraOn = SubscriptionCostParser.ClaudeExtra("""
        {"spend":{"enabled":true,"used":{"amount_minor":186,"currency":"USD","exponent":2},"limit":{"amount_minor":500,"currency":"USD","exponent":2},"severity":"normal"}}
        """);
    ExpectEqual(extraOn?.Used.Value, Dollars("1.86"), "Spend states its unit: 186 minor units at exponent 2");
    ExpectEqual(extraOn?.Limit?.Value, Dollars("5"), "The limit reads the same way");
    Expect(SubscriptionCostParser.ClaudeExtra("{\"extra_usage\":{\"is_enabled\":true,\"used_credits\":186}}") is null, "Extra usage without a spend amount is not guessed at");
    Expect(SubscriptionCostParser.ClaudeExtra("{\"spend\":{\"enabled\":true,\"used\":{\"amount_minor\":5,\"currency\":\"USD\",\"exponent\":9}}}") is null, "An implausible exponent reads as off");
    Expect(SubscriptionCostParser.ClaudeExtra("{\"five_hour\":{\"utilization\":10}}") is null, "No spend fields at all reads as off");
    Expect(SubscriptionCostParser.CodexCredits("{\"credits\":{\"has_credits\":false,\"balance\":\"0\"}}") is null, "No credits shows nothing");
    ExpectEqual(SubscriptionCostParser.CodexCredits("{\"credits\":{\"has_credits\":true,\"balance\":\"18.40\"}}"), Dollars("18.4"), "A credit balance reads exactly");
    Expect(SubscriptionCostParser.CodexCredits("{\"credits\":{\"has_credits\":true,\"balance\":\"lots\"}}") is null, "A balance that is not a number shows nothing");

    Expect(!CostVisibility.Shows(false, false, false), "A subscription-only user sees no money");
    Expect(CostVisibility.Shows(true, false, false), "Extra usage on shows money");
    Expect(!CostVisibility.Shows(false, true, false), "API cost needs a key");
    Expect(!CostVisibility.Shows(false, false, true), "A stored key alone does not switch it on");
    Expect(CostVisibility.Shows(false, true, true), "API cost with a key shows money");
    Expect(!CostVisibility.MenuBarShowsCost(true, true), "The tray keeps percentages while limits are shown");
    Expect(CostVisibility.MenuBarShowsCost(false, true), "An API-only provider puts its spend in the tray");
    Expect(!CostVisibility.MenuBarShowsCost(false, false), "Nothing to show is nothing shown");

    static string Usd(string text) => CostFormatter.Text(new CostAmount(Dollars(text)));
    ExpectEqual(Usd("0"), "$0.00", "Zero is two places");
    ExpectEqual(Usd("0.004"), "<$0.01", "Under a cent is not zero");
    ExpectEqual(Usd("12.345"), "$12.35", "Halves round away from zero");
    ExpectEqual(Usd("999.99"), "$999.99", "Under a thousand stays in cents");
    ExpectEqual(Usd("1234.5"), "$1.2K", "Thousands shorten");
    ExpectEqual(Usd("1250"), "$1.3K", "And round away from zero");
    ExpectEqual(Usd("3400000"), "$3.4M", "Millions shorten");
    ExpectEqual(Usd("-2.5"), "-$2.50", "A refund keeps its sign");
    ExpectEqual(CostFormatter.Text(new CostAmount(Dollars("12.3"), "eur")), "12.30 EUR", "Another currency is named");
    ExpectEqual(CostFormatter.Credits(Dollars("18.4")), "18.40", "Credits are a number, not dollars");

    ExpectEqual(CostBudget.Percent(Dollars("79.99"), 100m), 79, "Percent rounds down");
    ExpectEqual(string.Join(",", CostBudget.ReachedThresholds(80m, 100m)), "80", "80% reached");
    ExpectEqual(string.Join(",", CostBudget.ReachedThresholds(100m, 100m)), "80,100", "100% reached");
    ExpectEqual(string.Join(",", CostBudget.ReachedThresholds(150m, 100m)), "80,100", "Past the budget still reports both");
    Expect(CostBudget.Percent(50m, 0m) is null && CostBudget.ReachedThresholds(50m, 0m).Count == 0, "A zero budget is off");
    ExpectEqual(CostBudget.AlertId(Provider.Claude, 80, At("2026-11-30T16:00:00Z"), seoul), "claude-cost-80-2026-12", "An alert belongs to the local month");
    ExpectEqual(CostBudget.AlertId(Provider.Codex, 100, At("2026-11-30T16:00:00Z"), utc), "codex-cost-100-2026-11", "Which depends on the zone");

    ExpectEqual(CostFetchPolicy.NextFetch(now, true), now.AddMinutes(10), "A good fetch waits ten minutes");
    ExpectEqual(CostFetchPolicy.NextFetch(now, false), now.AddMinutes(5), "A failed fetch is retried in five");
    Expect(CostFetchPolicy.IsDue(null, now), "The first fetch is due at once");
    Expect(!CostFetchPolicy.IsDue(now.AddSeconds(1), now), "A fetch is not due early");
    Expect(CostFetchPolicy.IsDue(now, now), "A fetch is due on time");

    // Stage 1: the usage responses carry the money onto the submenu, and only for
    // accounts that pay it. The off cases are the responses measured on 2026-11-03.
    var english = new Localizer(AppLanguage.English);
    var korean = new Localizer(AppLanguage.Korean);
    var proUsage = UsageParser.ParseClaudeUsage("""
        {"five_hour":{"utilization":20},"seven_day":{"utilization":40},
         "extra_usage":{"is_enabled":false,"monthly_limit":null,"used_credits":null,"utilization":null,"currency":null,"decimal_places":null,"user_disabled":true},
         "spend":{"used":{"amount_minor":0,"currency":"USD","exponent":2},"limit":null,"percent":0,"severity":"normal","enabled":false}}
        """, now);
    Expect(proUsage.ExtraUsage is null, "The measured Pro response carries no money");
    Expect(SubscriptionCostText.Line(proUsage, english) is null, "So its submenu stays as it was");
    var payingUsage = UsageParser.ParseClaudeUsage("""
        {"five_hour":{"utilization":20},
         "spend":{"enabled":true,"used":{"amount_minor":186,"currency":"USD","exponent":2},"limit":{"amount_minor":500,"currency":"USD","exponent":2},"severity":"normal"}}
        """, now);
    var extraLine = SubscriptionCostText.Line(payingUsage, korean);
    ExpectEqual(extraLine?.Label, "추가 사용량", "Extra usage is named in the user's language");
    ExpectEqual(extraLine?.Value, "$1.86 / $5.00", "Spent against the limit");
    ExpectEqual(extraLine?.Percent, 37, "And the share of the limit, rounded down");
    var noLimit = payingUsage with { ExtraUsage = new ClaudeExtraUsage(new CostAmount(Dollars("1.86")), null, null) };
    ExpectEqual(SubscriptionCostText.Line(noLimit, english)?.Value, "$1.86", "Without a limit, just the amount");
    Expect(SubscriptionCostText.Line(noLimit, english)?.Percent is null, "And no bar");
    var otherCurrency = payingUsage with { ExtraUsage = new ClaudeExtraUsage(new CostAmount(2m), new CostAmount(5m, "EUR"), null) };
    ExpectEqual(SubscriptionCostText.Line(otherCurrency, english)?.Value, "$2.00", "A limit in another currency is not set against the amount");

    var codexPlus = UsageParser.ParseCodexUsage("""
        {"plan_type":"plus","rate_limit":{"primary_window":{"used_percent":10,"limit_window_seconds":18000,"reset_at":1793700000}},
         "credits":{"has_credits":false,"unlimited":false,"overage_limit_reached":false,"balance":"0"}}
        """, now);
    Expect(codexPlus.CreditBalance is null, "The measured Plus response without credits carries no money");
    Expect(SubscriptionCostText.Line(codexPlus, english) is null, "So its submenu stays as it was");
    var codexWithCredits = UsageParser.ParseCodexUsage("""
        {"plan_type":"plus","rate_limit":{"primary_window":{"used_percent":10,"limit_window_seconds":18000,"reset_at":1793700000}},
         "credits":{"has_credits":true,"balance":"18.4"}}
        """, now);
    var creditLine = SubscriptionCostText.Line(codexWithCredits, english);
    ExpectEqual(creditLine?.Label, "Credit balance", "A credit balance is labelled as one");
    ExpectEqual(creditLine?.Value, "18.40", "In credits, not dollars");
    Expect(creditLine?.Percent is null, "A balance has no bar");

    // The money rides the snapshot cache, so a failed poll keeps showing it, but
    // not the history, which would copy the same balance on every poll. The cache
    // compares against the real clock, so these snapshots are stamped with it.
    var costCachePath = Path.Combine(Path.GetTempPath(), "token-tracker-cost-" + Guid.NewGuid().ToString("N"), "usage-cache.json");
    var costCache = new CacheStore(costCachePath);
    costCache.Save(new UsageSnapshot(payingUsage, codexWithCredits, DateTimeOffset.Now));
    var cached = costCache.Load(TimeSpan.FromHours(1));
    ExpectEqual(cached?.Claude.ExtraUsage, payingUsage.ExtraUsage, "Extra usage survives the cache");
    ExpectEqual(cached?.Codex.CreditBalance, Dollars("18.4"), "So does a credit balance");
    const string cacheBeforeMoney = """
        {"Version":1,"Snapshot":{
          "Claude":{"Provider":"Claude","RemainingPercent5h":80,"RemainingPercent7d":60,"ResetAt5h":null,"ResetAt7d":null,"Source":"Api","Error":null,"Plan":"pro","Model":null,"UpdatedAt":"STAMP"},
          "Codex":{"Provider":"Codex","RemainingPercent5h":90,"RemainingPercent7d":95,"ResetAt5h":null,"ResetAt7d":null,"Source":"Api","Error":null,"Plan":"plus","Model":null,"UpdatedAt":"STAMP"},
          "UpdatedAt":"STAMP"}}
        """;
    File.WriteAllText(costCachePath, cacheBeforeMoney.Replace("STAMP", DateTimeOffset.Now.ToString("O", CultureInfo.InvariantCulture)));
    var oldCache = costCache.Load(TimeSpan.FromHours(1));
    Expect(oldCache is not null, "A cache written before money existed still loads");
    Expect(oldCache!.Claude.ExtraUsage is null && oldCache.Codex.CreditBalance is null, "And reads as no money");
    var recorded = UsageHistoryPolicy.MeasurementsOnly(new UsageSnapshot(payingUsage, codexWithCredits, now));
    Expect(recorded.Claude.ExtraUsage is null && recorded.Codex.CreditBalance is null, "History keeps no money");
    ExpectEqual(recorded.Claude.RemainingPercent5h, payingUsage.RemainingPercent5h, "But keeps the measurement");
}

// Admin API cost collection: what each API is asked, how pages and failures come
// back, and the schedule and cache that hold a reading between fetches. Mirrors
// Sources/TokenTrackerSmokeTests/AdminCostTests.swift.
static void RunAdminCostTests()
{
    var seoul = TimeZoneInfo.FindSystemTimeZoneById("Asia/Seoul");
    var utc = TimeZoneInfo.Utc;
    static DateTimeOffset At(string text) => DateTimeOffset.Parse(text, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal);
    static decimal Dollars(string text) => decimal.Parse(text, NumberStyles.Float, CultureInfo.InvariantCulture);
    static HttpResponseMessage Reply(HttpStatusCode status, string body) => new(status) { Content = new StringContent(body) };
    static string Lines(IEnumerable<string> lines) => string.Join("\n", lines);
    // 14:00 on the 3rd in Seoul, whose month began at 15:00 UTC on October 31.
    var now = At("2026-11-03T05:00:00Z");
    var (start, end) = CostPeriod.RequestRange(now, seoul);
    var directory = Path.Combine(Path.GetTempPath(), "token-tracker-admin-cost-" + Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(directory);
    try
    {
        ExpectEqual(
            AdminCostRequest.AnthropicHourly(start, end, null)?.AbsoluteUri,
            "https://api.anthropic.com/v1/organizations/analytics/cost?starting_at=2026-10-31T15:00:00Z&ending_at=2026-11-03T05:00:00Z&time_bucket_width=1h&limit=1000",
            "Anthropic is asked for hourly buckets from the local 1st");
        ExpectEqual(
            AdminCostRequest.AnthropicByModel(start, end, "page_a+b/c")?.AbsoluteUri,
            "https://api.anthropic.com/v1/organizations/analytics/cost?starting_at=2026-10-31T15:00:00Z&ending_at=2026-11-03T05:00:00Z&group_by%5B%5D=model&limit=100&page=page_a%2Bb%2Fc",
            "The model split names its group in brackets, and a cursor goes escaped");
        ExpectEqual(
            AdminCostRequest.OpenAI(start, end, null)?.AbsoluteUri,
            "https://api.openai.com/v1/organization/costs?start_time=1793458800&end_time=1793682000&bucket_width=1d&group_by%5B%5D=line_item&limit=31",
            "OpenAI is asked for days by line item from the local 1st");
        var (firstHourStart, firstHourEnd) = CostPeriod.RequestRange(At("2026-11-01T00:40:00Z"), utc);
        Expect(AdminCostRequest.AnthropicHourly(firstHourStart, firstHourEnd, null) is null, "The first hour of a month has nothing to ask Anthropic yet");
        Expect(AdminCostRequest.OpenAI(firstHourStart, firstHourEnd, null) is not null, "OpenAI takes any range that is not empty");
        var claudeHeaders = AdminCostRequest.Headers(Provider.Claude, "sk-ant-admin01-key").ToDictionary(header => header.Name, header => header.Value);
        ExpectEqual(claudeHeaders["x-api-key"], "sk-ant-admin01-key", "Anthropic takes the key in x-api-key");
        ExpectEqual(claudeHeaders["anthropic-beta"], "analytics-usage-cost-2026-08-24", "And the Analytics beta header");
        ExpectEqual(
            AdminCostRequest.Headers(Provider.Codex, "sk-admin-key").Single(header => header.Name == "Authorization").Value,
            "Bearer sk-admin-key",
            "OpenAI takes a bearer token");

        ExpectEqual(AdminKeyText.Suffix("sk-ant-admin01-abcdefgh1234"), "…1234", "A key shows its last four characters");
        ExpectEqual(AdminKeyText.Suffix("short"), "…", "A key too short to be real shows nothing of itself");

        // Anthropic: hourly totals over two pages, then the model split.
        var anthropicHandler = new QueueHttpMessageHandler(
            Reply(HttpStatusCode.OK, """
                {"data":[
                  {"starting_at":"2026-11-01T03:00:00Z","ending_at":"2026-11-01T04:00:00Z","group":{},"cost":{"currency":"USD","amount":"250000.000000"}},
                  {"starting_at":"2026-11-02T15:00:00Z","ending_at":"2026-11-02T16:00:00Z","group":{},"cost":{"currency":"USD","amount":"1234.500000"}}
                ],"has_more":true,"next_page":"page_2","data_refreshed_at":"2026-11-03T04:00:00Z"}
                """),
            Reply(HttpStatusCode.OK, """
                {"data":[
                  {"starting_at":"2026-11-03T01:00:00Z","ending_at":"2026-11-03T02:00:00Z","group":{},"cost":{"currency":"USD","amount":"100.000000"}}
                ],"has_more":false,"next_page":null,"data_refreshed_at":"2026-11-03T04:00:00Z"}
                """),
            Reply(HttpStatusCode.OK, """
                {"data":[
                  {"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-opus-5-5"},"cost":{"currency":"USD","amount":"200000.000000"}},
                  {"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-sonnet-5-5"},"cost":{"currency":"USD","amount":"51334.500000"}},
                  {"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":null},"cost":{"currency":"USD","amount":"0.000000"}},
                  {"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-haiku-5-5"},"cost":{"currency":"USD","amount":"50.000000"}},
                  {"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-fable-5-1"},"cost":{"currency":"USD","amount":"10.000000"}}
                ],"has_more":false,"next_page":null,"data_refreshed_at":"2026-11-03T04:00:00Z"}
                """));
        var anthropic = new AdminCostClient(Provider.Claude, new HttpClient(anthropicHandler), Path.Combine(directory, "claude-cost-rate-limit.json"));
        var claudeResult = anthropic.FetchAsync("sk-ant-admin01-abcdefgh1234", now, seoul).GetAwaiter().GetResult();
        var claudeCost = claudeResult.Cost ?? throw new InvalidOperationException("Anthropic's month is read: " + claudeResult.Failure);
        ExpectEqual(claudeCost.MonthToDate?.Value, Dollars("2513.345"), "Every page's hours add up to the month");
        ExpectEqual(claudeCost.Today?.Value, Dollars("13.345"), "Today is Seoul's, from its midnight");
        ExpectEqual(claudeCost.DataRefreshedAt, At("2026-11-03T04:00:00Z"), "The reading says how far the data reaches");
        ExpectEqual(
            string.Join(",", claudeCost.TopItems.Select(item => item.Name)),
            "claude-opus-5-5,claude-sonnet-5-5,claude-haiku-5-5",
            "The three largest models");
        ExpectEqual(anthropicHandler.Requests.Count, 3, "Two pages of hours and one of models");
        Expect(anthropicHandler.Requests[1].Uri.EndsWith("&page=page_2", StringComparison.Ordinal), "The second page carries the cursor");
        ExpectEqual(anthropicHandler.Requests[0].Headers["x-api-key"], "sk-ant-admin01-abcdefgh1234", "The key goes in its header");

        var untalliedHandler = new QueueHttpMessageHandler(
            Reply(HttpStatusCode.OK, """{"data":[],"has_more":false,"next_page":null,"data_refreshed_at":null}"""));
        var untallied = new AdminCostClient(Provider.Claude, new HttpClient(untalliedHandler), Path.Combine(directory, "untallied-rate-limit.json"));
        var untalliedResult = untallied.FetchAsync("sk-ant-admin01-abcdefgh1234", now, seoul).GetAwaiter().GetResult();
        var untalliedCost = untalliedResult.Cost ?? throw new InvalidOperationException("An untallied month is still a reading: " + untalliedResult.Failure);
        Expect(untalliedCost.MonthToDate is null && untalliedCost.Today is null, "Nothing tallied is no amount, not zero");
        ExpectEqual(untalliedHandler.CallCount, 1, "And leaves no model split to ask for");

        // OpenAI: days cut at Seoul's midnight, by line item.
        var limitedReply = Reply(HttpStatusCode.TooManyRequests, "{}");
        limitedReply.Headers.RetryAfter = new System.Net.Http.Headers.RetryConditionHeaderValue(TimeSpan.FromSeconds(30));
        var openAIHandler = new QueueHttpMessageHandler(
            Reply(HttpStatusCode.OK, """
                {"object":"page","data":[
                  {"object":"bucket","start_time":1793458800,"end_time":1793545200,"results":[
                    {"amount":{"value":1.25,"currency":"usd"},"line_item":"gpt-6, input"},
                    {"amount":{"value":0.75,"currency":"usd"},"line_item":"gpt-6, output"}]},
                  {"object":"bucket","start_time":1793631600,"end_time":1793718000,"results":[
                    {"amount":{"value":0.5,"currency":"usd"},"line_item":"gpt-6, input"}]}
                ],"has_more":false,"next_page":null}
                """),
            Reply(HttpStatusCode.Unauthorized, """{"error":{"message":"Invalid API key"}}"""),
            limitedReply);
        var openAI = new AdminCostClient(Provider.Codex, new HttpClient(openAIHandler), Path.Combine(directory, "codex-cost-rate-limit.json"));
        var codexResult = openAI.FetchAsync("sk-admin-abcdefgh5678", now, seoul).GetAwaiter().GetResult();
        var codexCost = codexResult.Cost ?? throw new InvalidOperationException("OpenAI's month is read: " + codexResult.Failure);
        ExpectEqual(codexCost.MonthToDate?.Value, Dollars("2.5"), "OpenAI's days add up to the month");
        ExpectEqual(codexCost.Today?.Value, Dollars("0.5"), "And a day cut at local midnight is today");
        Expect(codexCost.DataRefreshedAt is null, "OpenAI has no watermark to report");
        ExpectEqual(string.Join(",", codexCost.TopItems.Select(item => item.Name)), "gpt-6", "A model's line items add up under the model");
        ExpectEqual(codexCost.TopItems[0].Amount.Value, Dollars("2.5"), "Across every day");
        ExpectEqual(
            openAI.FetchAsync("sk-admin-abcdefgh5678", now, seoul).GetAwaiter().GetResult(),
            CostFetchResult.Failed(CostFetchFailureKind.KeyRejected, "HTTP 401 from OpenAI Admin API"),
            "A 401 is a rejected key");
        var limited = openAI.FetchAsync("sk-admin-abcdefgh5678", now, seoul).GetAwaiter().GetResult();
        Expect(limited.Failure?.Kind == CostFetchFailureKind.RateLimited, "A 429 is a rate limit: " + limited);
        var again = openAI.FetchAsync("sk-admin-abcdefgh5678", now, seoul).GetAwaiter().GetResult();
        Expect(again.Failure?.Kind == CostFetchFailureKind.RateLimited, "The cooldown holds: " + again);
        ExpectEqual(openAIHandler.CallCount, 3, "And is waited out without asking again");
        var otherKey = openAI.FetchAsync("sk-admin-otherkey9999", now, seoul).GetAwaiter().GetResult();
        Expect(otherKey.Failure?.Kind == CostFetchFailureKind.Failed, "Another key is not held to this key's cooldown: " + otherKey);
        ExpectEqual(openAIHandler.CallCount, 4, "So it asks");

        // A reading holds for the rest of its local month; today, for the rest of its day.
        ExpectEqual(CostCachePolicy.Usable(claudeCost, At("2026-11-03T14:59:00Z"), seoul), claudeCost, "Later the same Seoul day, all of it holds");
        var nextDay = CostCachePolicy.Usable(claudeCost, At("2026-11-03T15:30:00Z"), seoul);
        Expect(nextDay?.Today is null && nextDay?.MonthToDate == claudeCost.MonthToDate, "The next day keeps the month but not today");
        Expect(CostCachePolicy.Usable(claudeCost, At("2026-11-30T15:30:00Z"), seoul) is null, "A new local month keeps nothing");

        // The service: a key is read only when switched on, fetches keep their own
        // schedule, and the last good reading carries through failures.
        var keys = new MemoryAdminKeys();
        var cachePath = Path.Combine(directory, "cost-cache.json");
        var serviceHandler = new QueueHttpMessageHandler(
            Reply(HttpStatusCode.OK, """{"data":[{"starting_at":"2026-11-03T01:00:00Z","ending_at":"2026-11-03T02:00:00Z","group":{},"cost":{"currency":"USD","amount":"4200.000000"}}],"has_more":false,"data_refreshed_at":"2026-11-03T04:00:00Z"}"""),
            Reply(HttpStatusCode.OK, """{"data":[{"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-opus-5-5"},"cost":{"currency":"USD","amount":"4200.000000"}}],"has_more":false,"data_refreshed_at":"2026-11-03T04:00:00Z"}"""),
            Reply(HttpStatusCode.InternalServerError, "{}"),
            Reply(HttpStatusCode.Unauthorized, "{}"));
        AdminCostService Service(QueueHttpMessageHandler handler) => new(
            keys,
            new Dictionary<Provider, AdminCostClient>
            {
                [Provider.Claude] = new AdminCostClient(Provider.Claude, new HttpClient(handler), Path.Combine(directory, "service-rate-limit.json"))
            },
            new CostCacheStore(cachePath));
        var claudeOn = new Dictionary<Provider, bool> { [Provider.Claude] = true };
        var costs = Service(serviceHandler);
        var off = costs.RefreshIfDueAsync(new Dictionary<Provider, bool>(), now, seoul).GetAwaiter().GetResult();
        Expect(!off[Provider.Claude].Enabled && keys.Reads == 0, "Switched off, the key is never read");
        ExpectEqual(Lines(CostDiagnostics.Lines(Provider.Claude, off[Provider.Claude])), "Claude API cost: off", "Diagnostics says it is off");
        var noKey = costs.RefreshIfDueAsync(claudeOn, now, seoul).GetAwaiter().GetResult();
        Expect(noKey[Provider.Claude].Enabled && noKey[Provider.Claude].KeySuffix is null, "Switched on without a key, there is nothing to fetch");
        ExpectEqual(Lines(CostDiagnostics.Lines(Provider.Claude, noKey[Provider.Claude])), "Claude API cost: on, no Admin key", "Diagnostics says the key is missing");
        ExpectEqual(serviceHandler.CallCount, 0, "So nothing is asked");

        keys.Save(Provider.Claude, "sk-ant-admin01-abcdefgh1234");
        var fetched = costs.RefreshIfDueAsync(claudeOn, now, seoul).GetAwaiter().GetResult()[Provider.Claude];
        ExpectEqual(fetched.Cost?.MonthToDate?.Value, Dollars("42"), "With a key the month is fetched");
        ExpectEqual(fetched.NextFetchAt, now.AddSeconds(600), "And the next fetch waits ten minutes");
        ExpectEqual(
            Lines(CostDiagnostics.Lines(Provider.Claude, fetched)),
            Lines(new[]
            {
                "Claude API cost: on, key …1234",
                "Claude API cost month: $42.00",
                "Claude API cost today: $42.00",
                "Claude API cost complete up to: 2026-11-03T04:00:00Z",
                "Claude API cost top: claude-opus-5-5 $42.00",
                "Claude API cost measured: 2026-11-03T05:00:00Z",
                "Claude API cost last fetch: 2026-11-03T05:00:00Z ok"
            }),
            "Diagnostics lists the reading, and never the key");
        costs.RefreshIfDueAsync(claudeOn, now.AddSeconds(60), seoul).GetAwaiter().GetResult();
        ExpectEqual(serviceHandler.CallCount, 2, "A fetch is not repeated before its turn");
        var failed = costs.RefreshIfDueAsync(claudeOn, now.AddSeconds(601), seoul).GetAwaiter().GetResult()[Provider.Claude];
        Expect(failed.Failure?.Kind == CostFetchFailureKind.Failed && failed.IsCached, "A failed fetch keeps the last reading, marked as cached");
        ExpectEqual(failed.Cost?.MonthToDate?.Value, Dollars("42"), "The reading is the last good one");
        ExpectEqual(failed.NextFetchAt, now.AddSeconds(901), "And is retried in five minutes");

        var relaunched = Service(new QueueHttpMessageHandler(Reply(HttpStatusCode.ServiceUnavailable, "{}")))
            .RefreshIfDueAsync(claudeOn, now.AddSeconds(700), seoul).GetAwaiter().GetResult()[Provider.Claude];
        Expect(relaunched.IsCached && relaunched.Cost?.MonthToDate?.Value == Dollars("42"), "A relaunch that cannot fetch shows the saved reading");

        keys.Save(Provider.Claude, "sk-ant-admin01-otherkey9999");
        var rejected = costs.RefreshIfDueAsync(claudeOn, now.AddSeconds(602), seoul).GetAwaiter().GetResult()[Provider.Claude];
        ExpectEqual(serviceHandler.CallCount, 4, "A new key is fetched at once, whatever the schedule");
        Expect(rejected.Failure?.Kind == CostFetchFailureKind.KeyRejected && rejected.Cost is null, "And another key's reading is not passed off as its own");
        ExpectEqual(rejected.KeySuffix, "…9999", "The new key is the one named");

        // DPAPI: the keys survive a new store, and the file does not hold them in the clear.
        if (OperatingSystem.IsWindows())
        {
            var keyPath = Path.Combine(directory, "admin-keys.dat");
            var dpapi = new DpapiAdminKeyStore(keyPath);
            Expect(dpapi.Key(Provider.Claude) is null, "No key file is no key");
            Expect(dpapi.Save(Provider.Claude, " sk-ant-admin01-abcdefgh1234\n"), "A key is saved");
            Expect(dpapi.Save(Provider.Codex, "sk-admin-abcdefgh5678"), "Each provider has its own key");
            ExpectEqual(new DpapiAdminKeyStore(keyPath).Key(Provider.Claude), "sk-ant-admin01-abcdefgh1234", "And reads back trimmed");
            Expect(!System.Text.Encoding.UTF8.GetString(File.ReadAllBytes(keyPath)).Contains("abcdefgh", StringComparison.Ordinal), "The file does not hold a key in the clear");
            dpapi.Delete(Provider.Claude);
            Expect(dpapi.Key(Provider.Claude) is null && dpapi.Key(Provider.Codex) == "sk-admin-abcdefgh5678", "Deleting one key keeps the other");
            dpapi.Delete(Provider.Codex);
            Expect(!File.Exists(keyPath), "Deleting the last key removes the file");
        }
    }
    finally
    {
        Directory.Delete(directory, recursive: true);
    }
}

// What the API cost puts on screen: the submenu's lines, the tray's money, and the
// rule that keeps a provider showing limits on its percentage. Mirrors
// runCostDisplayTests in Sources/TokenTrackerSmokeTests/AdminCostTests.swift.
static void RunCostDisplayTests()
{
    var seoul = TimeZoneInfo.FindSystemTimeZoneById("Asia/Seoul");
    static DateTimeOffset At(string text) => DateTimeOffset.Parse(text, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal);
    static decimal Dollars(string text) => decimal.Parse(text, NumberStyles.Float, CultureInfo.InvariantCulture);
    static CostAmount Usd(string text) => new(Dollars(text));
    var english = new Localizer(AppLanguage.English);
    var korean = new Localizer(AppLanguage.Korean);
    var now = At("2026-11-03T05:00:00Z");
    var cost = new ProviderCost(
        Provider.Claude,
        Usd("2513.345"),
        Usd("13.345"),
        new[]
        {
            new CostLineItem("claude-opus-5-5", Usd("2000")),
            new CostLineItem("claude-sonnet-5-5", Usd("513.345")),
            new CostLineItem("claude-haiku-5-5", Usd("0.5"))
        },
        At("2026-11-03T04:00:00Z"),
        now);
    static CostState State(bool enabled = true, string? key = "…1234", ProviderCost? cost = null, CostFetchFailure? failure = null) =>
        new() { Enabled = enabled, KeySuffix = key, Cost = cost, Failure = failure };

    Expect(AdminCostText.View(State(enabled: false, cost: cost), 0, now, seoul, korean) is null, "Switched off, there is no cost to show");
    Expect(AdminCostText.View(State(key: null), 0, now, seoul, korean) is null, "And without a key, neither");

    var loading = AdminCostText.View(State(), 100, now, seoul, korean)!;
    ExpectEqual(loading.Status, AdminCostStatus.Loading, "Switched on with a key and no answer yet is loading");
    ExpectEqual(loading.StatusTitle, "불러오는 중", "In the user's language");
    Expect(loading.MonthValue == "--" && loading.Detail is null && loading.BudgetText is null && loading.AsOf is null, "With nothing to show yet");

    var ok = AdminCostText.View(State(cost: cost), 5000, now, seoul, korean)!;
    ExpectEqual(ok.Status, AdminCostStatus.Ok, "A fresh reading is fine");
    ExpectEqual(ok.StatusTitle, "정상", "And says so");
    ExpectEqual(ok.MonthLabel, "이번 달 사용", "The month is this month's use");
    ExpectEqual(ok.MonthValue, "$2.5K", "In the cost format");
    ExpectEqual(ok.BudgetPercent, 50, "Against the budget, rounded down");
    ExpectEqual(ok.BudgetText, "예산 $5.0K 중 50%", "The budget line names the budget");
    Expect(!ok.IsBudgetWarning, "Half a budget is no warning");
    ExpectEqual(ok.Detail, "오늘 $13.35 · Opus 5.5 79% · Sonnet 5.5 20%", "Today, then the two largest models' shares");
    ExpectEqual(ok.AsOf, "13:00 기준", "And how far the data reaches, in Seoul's time");

    var english83 = AdminCostText.View(State(cost: cost), 3000, now, seoul, english)!;
    ExpectEqual(english83.BudgetText, "83% of $3.0K budget", "The budget line in English");
    Expect(english83.IsBudgetWarning, "80% of a budget is a warning");
    var yesterday = new ProviderCost(Provider.Claude, cost.MonthToDate, null, Array.Empty<CostLineItem>(), At("2026-11-02T13:10:00Z"), now);
    var lagging = AdminCostText.View(State(cost: yesterday), 0, now, seoul, english)!;
    ExpectEqual(lagging.AsOf, "as of Nov 2 22:10", "Data from another day names the day");
    ExpectEqual(lagging.Detail, "Today not tallied yet", "And today is not tallied, not zero");
    ExpectEqual(AdminCostText.View(State(cost: yesterday), 0, now, seoul, korean)?.AsOf, "11월 2일 22:10 기준", "In Korean too");
    var untallied = new ProviderCost(Provider.Claude, null, null, Array.Empty<CostLineItem>(), null, now);
    var empty = AdminCostText.View(State(cost: untallied), 100, now, seoul, korean)!;
    Expect(empty.MonthValue == "집계 전" && empty.Detail is null && empty.BudgetPercent is null, "An untallied month shows no amount and no bar");

    var failedFetch = new CostFetchFailure(CostFetchFailureKind.Failed, "HTTP 500 from Anthropic Admin API");
    var cached = AdminCostText.View(State(cost: cost, failure: failedFetch), 0, now, seoul, korean)!;
    Expect(cached.Status == AdminCostStatus.Cached && cached.StatusTitle == "캐시 데이터 사용 중", "A failed fetch over a reading is the cache");
    ExpectEqual(cached.FailureMessage, "HTTP 500 from Anthropic Admin API", "And keeps why for a tooltip");
    var rejected = new CostFetchFailure(CostFetchFailureKind.KeyRejected, "HTTP 401 from Anthropic Admin API");
    ExpectEqual(AdminCostText.View(State(cost: cost, failure: rejected), 0, now, seoul, korean)?.Status, AdminCostStatus.KeyRejected, "A rejected key outranks the cache");
    ExpectEqual(AdminCostText.View(State(failure: rejected), 0, now, seoul, korean)?.StatusTitle, "Admin 키 거부됨", "And is named");
    ExpectEqual(
        AdminCostText.View(State(failure: new CostFetchFailure(CostFetchFailureKind.RateLimited, "HTTP 429")), 0, now, seoul, korean)?.Status,
        AdminCostStatus.RateLimited,
        "A rate limit with no reading");
    ExpectEqual(AdminCostText.View(State(failure: failedFetch), 0, now, seoul, korean)?.StatusTitle, "비용을 가져오지 못함", "Any other failure with no reading");

    ExpectEqual(CostItemName.Display("claude-opus-5-5"), "Opus 5.5", "A model reads as family and version");
    ExpectEqual(CostItemName.Display("claude-sonnet-4-5-20250929"), "Sonnet 4.5", "Without its date stamp");
    ExpectEqual(CostItemName.Display("claude-3-5-haiku-20241022"), "Haiku 3.5", "Whichever order the version comes in");
    ExpectEqual(CostItemName.Display("gpt-6"), "gpt-6", "Other names stay as they are");
    ExpectEqual(CostItemName.LineItemGroup("gpt-6, input"), "gpt-6", "An OpenAI line item belongs to its model");
    ExpectEqual(CostItemName.LineItemGroup("web search"), "web search", "One with no model stays whole");

    ExpectEqual(CostFormatter.Compact(Usd("42.18")), "$42", "The tray drops cents from $10");
    ExpectEqual(CostFormatter.Compact(Usd("42.5")), "$43", "Rounding half away from zero");
    ExpectEqual(CostFormatter.Compact(Usd("3.07")), "$3.07", "But keeps them below, so a small spend is not $0");
    ExpectEqual(CostFormatter.Compact(Usd("1234")), "$1.2K", "And shortens thousands");

    Expect(AdminKeyText.LooksLikeAdminKey("sk-ant-admin01-abcdefgh", Provider.Claude), "An Anthropic Admin key");
    Expect(!AdminKeyText.LooksLikeAdminKey("sk-ant-api03-abcdefgh", Provider.Claude), "An ordinary Anthropic API key cannot read cost");
    Expect(AdminKeyText.LooksLikeAdminKey(" sk-admin-abcdefgh\n", Provider.Codex), "An OpenAI Admin key, pasted with whitespace");
    Expect(!AdminKeyText.LooksLikeAdminKey("sk-proj-abcdefgh", Provider.Codex), "An OpenAI project key cannot read cost");

    // The tray: only a provider tracked by API cost alone shows money.
    var small = new ProviderCost(Provider.Claude, Usd("42.18"), null, Array.Empty<CostLineItem>(), null, now);
    var states = new Dictionary<Provider, CostState> { [Provider.Claude] = State(cost: small), [Provider.Codex] = State(cost: small) };
    var claudeOffCodexOn = new Dictionary<Provider, bool> { [Provider.Claude] = false, [Provider.Codex] = true };
    var bothOff = new Dictionary<Provider, bool> { [Provider.Claude] = false, [Provider.Codex] = false };
    var entries = CostMenuBar.Entries(claudeOffCodexOn, states, new Dictionary<Provider, int> { [Provider.Claude] = 50 });
    Expect(
        entries.Count == 1 && entries[Provider.Claude] == new MenuBarCost("$42", true),
        "Claude shows its spend, past 80% of its budget; Codex keeps its percentage");
    var placeholder = CostMenuBar.Entries(bothOff, new Dictionary<Provider, CostState> { [Provider.Claude] = State() }, new Dictionary<Provider, int>());
    Expect(placeholder.Count == 1 && placeholder[Provider.Claude] == new MenuBarCost("--", false), "Before the first reading the spend is a placeholder");
    Expect(
        CostMenuBar.Entries(bothOff, new Dictionary<Provider, CostState> { [Provider.Claude] = State(key: null) }, new Dictionary<Provider, int>()).Count == 0,
        "No key, no money in the tray");
    Expect(!CostMenuBar.TrayShowsDollar(claudeOffCodexOn, entries), "The tray keeps a percentage while any limit is shown");
    Expect(CostMenuBar.TrayShowsDollar(bothOff, entries), "And draws $ when only money is left");
    Expect(!CostMenuBar.TrayShowsDollar(bothOff, new Dictionary<Provider, MenuBarCost>()), "But not with nothing to show");

    var snapshot = new UsageSnapshot(
        ProviderUsage.Unavailable(Provider.Claude, "Disabled"),
        new ProviderUsage(Provider.Codex, null, 91, null, null, UsageSource.Api, null, "plus", null, now),
        now);
    var claudeSpend = new Dictionary<Provider, MenuBarCost> { [Provider.Claude] = new MenuBarCost("$42", false) };
    ExpectEqual(DisplayFormatter.StatusTitle(snapshot, DisplayMode.Both, costs: claudeSpend), "Cdx 91% · Cl $42", "Both: Codex's percentage beside Claude's spend");
    ExpectEqual(DisplayFormatter.StatusTitle(snapshot, DisplayMode.ClaudeOnly, costs: claudeSpend), "Cl $42", "Claude alone");
    ExpectEqual(DisplayFormatter.StatusTitle(snapshot, DisplayMode.ClaudeOnly, ProviderLabelStyle.Icon, claudeSpend), "Claude $42", "With the logo label");
    ExpectEqual(DisplayFormatter.StatusTitle(snapshot, DisplayMode.LowestRemaining, costs: claudeSpend), "AI 91%", "The lowest-remaining mode keeps its percentage");
    var noLimits = new UsageSnapshot(ProviderUsage.Unavailable(Provider.Claude, "Disabled"), ProviderUsage.Unavailable(Provider.Codex, "Disabled"), now);
    var bothSpend = new Dictionary<Provider, MenuBarCost>
    {
        [Provider.Claude] = new MenuBarCost("$42", false),
        [Provider.Codex] = new MenuBarCost("$8.00", false)
    };
    ExpectEqual(DisplayFormatter.StatusTitle(noLimits, DisplayMode.LowestRemaining, costs: bothSpend), "Cl $42 · Cdx $8.00", "And lists the spend when no provider has a percentage");
    ExpectEqual(DisplayFormatter.StatusTitle(snapshot, DisplayMode.Both), "Cdx 91% · Cl --", "Without costs the title is as it was");
    ExpectEqual(
        DisplayFormatter.Tooltip(snapshot, ProviderLabelStyle.Icon, false, korean, claudeSpend),
        "Codex 91%, Claude $42 (이번 달 사용)",
        "The tooltip names the spend as this month's");
}

// Money alerts: a budget threshold once per local month, Claude extra usage leaving
// `normal`, Codex reaching its credit overage limit, and nothing at all for an
// account that does not pay this way. Mirrors
// Sources/TokenTrackerSmokeTests/CostAlertTests.swift.
static void RunCostAlertTests()
{
    var seoul = TimeZoneInfo.FindSystemTimeZoneById("Asia/Seoul");
    static DateTimeOffset At(string text) => DateTimeOffset.Parse(text, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal);
    static CostAmount Usd(string text) => new(decimal.Parse(text, NumberStyles.Float, CultureInfo.InvariantCulture));
    static string Ids(CostAlertResult result) => string.Join(",", result.Alerts.Select(alert => alert.Id));
    static string Sorted(IEnumerable<string> ids) => string.Join(",", ids.OrderBy(id => id, StringComparer.Ordinal));
    var korean = new Localizer(AppLanguage.Korean);
    var english = new Localizer(AppLanguage.English);
    // 14:00 on November 3rd in Seoul.
    var now = At("2026-11-03T05:00:00Z");
    CostState Spent(string? month, string? key = "…1234") => new()
    {
        Enabled = true,
        KeySuffix = key,
        Cost = new ProviderCost(Provider.Claude, month is null ? null : Usd(month), null, Array.Empty<CostLineItem>(), null, now)
    };
    CostAlertResult Evaluate(
        IReadOnlyDictionary<Provider, CostState> costs,
        int budget = 100,
        UsageSnapshot? snapshot = null,
        IEnumerable<string>? delivered = null,
        bool notifications = true,
        DateTimeOffset? time = null,
        Localizer? localizer = null) =>
        CostAlertEvaluator.Evaluate(
            snapshot,
            costs,
            new Dictionary<Provider, int> { [Provider.Claude] = budget },
            delivered ?? Array.Empty<string>(),
            notifications,
            time ?? now,
            seoul,
            localizer ?? korean);
    Dictionary<Provider, CostState> Claude(CostState state) => new() { [Provider.Claude] = state };
    var none = new Dictionary<Provider, CostState>();

    var at84 = Evaluate(Claude(Spent("84.2")));
    ExpectEqual(Ids(at84), "claude-cost-80-2026-11", "84% of a budget is its 80% alert");
    ExpectEqual(at84.Alerts[0].Title, "API 비용 예산", "Titled in the user's language");
    ExpectEqual(at84.Alerts[0].Body, "Anthropic API: 이번 달 $84.20, 예산 $100의 84%", "Naming the organization, the month's spend and the budget");
    ExpectEqual(Sorted(at84.Delivered), "claude-cost-80-2026-11", "And remembered");
    Expect(Evaluate(Claude(Spent("90")), delivered: at84.Delivered).Alerts.Count == 0, "The same threshold is not said twice in a month");
    ExpectEqual(Ids(Evaluate(Claude(Spent("105")), delivered: at84.Delivered)), "claude-cost-100-2026-11", "Reaching the budget is news after 80%");
    var jump = Evaluate(Claude(Spent("105")));
    ExpectEqual(Ids(jump), "claude-cost-100-2026-11", "A jump past both thresholds says only 100%");
    ExpectEqual(Sorted(jump.Delivered), "claude-cost-100-2026-11,claude-cost-80-2026-11", "And counts 80% as said");
    ExpectEqual(
        Evaluate(Claude(Spent("84.2")), localizer: english).Alerts[0].Body,
        "Anthropic API: $84.20 this month, 84% of the $100 budget",
        "The body in English");
    Expect(Evaluate(Claude(Spent("79.99"))).Alerts.Count == 0, "Under 80% is no alert");
    Expect(Evaluate(Claude(Spent("500")), budget: 0).Alerts.Count == 0, "A zero budget is off");
    Expect(Evaluate(Claude(Spent("500", key: null))).Alerts.Count == 0, "No key, no money, no alert");
    Expect(Evaluate(Claude(Spent(null))).Alerts.Count == 0, "An untallied month has nothing to say");
    var off = Evaluate(Claude(Spent("105")), delivered: new[] { "claude-cost-80-2026-11" }, notifications: false);
    Expect(off.Alerts.Count == 0, "Notifications off sends nothing");
    ExpectEqual(Sorted(off.Delivered), "claude-cost-80-2026-11", "And records nothing new, so turning them on still says 100%");
    var december = Evaluate(none, delivered: new[] { "claude-cost-100-2026-11" }, time: At("2026-11-30T15:30:00Z"));
    Expect(december.Delivered.Count == 0, "A new local month forgets the last one's alerts");

    // Stage 1: the money the subscription responses carry.
    ProviderUsage ClaudeUsage(string spend) =>
        UsageParser.ParseClaudeUsage("{\"five_hour\":{\"utilization\":20},\"spend\":" + spend + "}", now);
    var codexPlus = UsageParser.ParseCodexUsage("""
        {"plan_type":"plus","rate_limit":{"primary_window":{"used_percent":10,"limit_window_seconds":18000,"reset_at":1793700000}},
         "credits":{"has_credits":false,"unlimited":false,"overage_limit_reached":false,"balance":"0"}}
        """, now);
    UsageSnapshot Snapshot(ProviderUsage claude, ProviderUsage? codex = null) => new(claude, codex ?? codexPlus, now);
    var warning = ClaudeUsage("""{"enabled":true,"used":{"amount_minor":410,"currency":"USD","exponent":2},"limit":{"amount_minor":500,"currency":"USD","exponent":2},"severity":"Warning"}""");
    var extra = Evaluate(none, snapshot: Snapshot(warning));
    ExpectEqual(Ids(extra), "claude-extra-warning-2026-11", "Extra usage leaving normal is an alert");
    ExpectEqual(extra.Alerts[0].Title, "Claude 추가 사용량", "About Claude's extra usage");
    ExpectEqual(extra.Alerts[0].Body, "추가 사용량이 $4.10 / $5.00입니다.", "With spend against the limit");
    Expect(Evaluate(none, snapshot: Snapshot(warning), delivered: extra.Delivered).Alerts.Count == 0, "Said once a month");
    var normal = ClaudeUsage("""{"enabled":true,"used":{"amount_minor":100,"currency":"USD","exponent":2},"severity":"normal"}""");
    Expect(Evaluate(none, snapshot: Snapshot(normal)).Alerts.Count == 0, "Normal is no news");
    var pro = ClaudeUsage("""{"used":{"amount_minor":0,"currency":"USD","exponent":2},"limit":null,"percent":0,"severity":"warning","enabled":false}""");
    Expect(Evaluate(none, snapshot: Snapshot(pro)).Alerts.Count == 0, "With extra usage off there is no alert, whatever the severity");

    Expect(codexPlus.CreditLimitReached is null, "The measured Plus response has no credit limit to reach");
    var limited = UsageParser.ParseCodexUsage("""
        {"plan_type":"plus","rate_limit":{"primary_window":{"used_percent":10,"limit_window_seconds":18000,"reset_at":1793700000}},
         "credits":{"has_credits":true,"overage_limit_reached":true,"balance":"0"}}
        """, now);
    ExpectEqual(limited.CreditLimitReached, true, "An account holding credits can reach their overage limit");
    var credits = Evaluate(none, snapshot: Snapshot(pro, limited));
    ExpectEqual(Ids(credits), "codex-credits-limit-2026-11", "Which is an alert");
    ExpectEqual(credits.Alerts[0].Body, "Codex 크레딧 초과 사용 한도에 도달했습니다.", "Saying so");
    var noCredits = UsageParser.ParseCodexUsage("""
        {"plan_type":"plus","rate_limit":{"primary_window":{"used_percent":10,"limit_window_seconds":18000,"reset_at":1793700000}},
         "credits":{"has_credits":false,"overage_limit_reached":true,"balance":"0"}}
        """, now);
    Expect(noCredits.CreditLimitReached is null, "An account without credits is never told about a credit limit");
    var recorded = UsageHistoryPolicy.MeasurementsOnly(Snapshot(pro, limited));
    Expect(recorded.Codex.CreditLimitReached is null, "History keeps no money flags");
}

static string StatusLinePayload(
    DateTimeOffset? measuredAt,
    DateTimeOffset? writtenAt,
    double? usedPercent5h,
    DateTimeOffset? resetAt5h,
    double? usedPercent7d,
    DateTimeOffset? resetAt7d,
    int version = ClaudeStatusLineBridge.Version)
{
    var windows = new List<string>();
    if (usedPercent5h is not null && resetAt5h is not null)
    {
        windows.Add(StatusLineWindow("five_hour", usedPercent5h.Value, resetAt5h.Value));
    }

    if (usedPercent7d is not null && resetAt7d is not null)
    {
        windows.Add(StatusLineWindow("seven_day", usedPercent7d.Value, resetAt7d.Value));
    }

    var fields = new List<string>
    {
        "\"version\":" + version.ToString(CultureInfo.InvariantCulture),
        "\"provider\":\"claude\""
    };
    if (measuredAt is not null)
    {
        fields.Add("\"measured_at\":" + Seconds(measuredAt.Value));
    }

    if (writtenAt is not null)
    {
        fields.Add("\"written_at\":" + Seconds(writtenAt.Value));
    }

    fields.Add("\"rate_limits\":{" + string.Join(",", windows) + "}");
    return "{" + string.Join(",", fields) + "}";
}

static string StatusLineWindow(string name, double usedPercent, DateTimeOffset resetAt) =>
    "\"" + name + "\":{\"used_percentage\":"
    + usedPercent.ToString(CultureInfo.InvariantCulture)
    + ",\"resets_at\":" + Seconds(resetAt) + "}";

static string Seconds(DateTimeOffset date) =>
    date.ToUnixTimeSeconds().ToString(CultureInfo.InvariantCulture);

/// <summary>
/// Drives scripts/claude-statusline-bridge.ps1 the way Claude Code does. The
/// script is where "a re-run is not a new measurement" is actually decided on
/// this platform, and it is the one piece of this feature that no compiler looks
/// at. Skipped when the checkout or PowerShell is not reachable; the truth table
/// above does not depend on it.
/// </summary>
static void RunStatusLineBridgeScriptTests()
{
    var script = FindRepoFile(Path.Combine("scripts", "claude-statusline-bridge.ps1"));
    var shell = ResolvePowerShell();
    if (script is null || shell is null)
    {
        return;
    }

    var directory = Path.Combine(Path.GetTempPath(), "token-tracker-statusline-" + Guid.NewGuid().ToString("N"));
    Directory.CreateDirectory(directory);
    try
    {
        var output = Path.Combine(directory, "claude-statusline.json");
        var reset5h = DateTimeOffset.UtcNow.AddHours(1);
        var reset7d = DateTimeOffset.UtcNow.AddDays(1);

        // Stands in for whatever status-line command the user already had. It
        // drains stdin and prints its own line, which is exactly the contract the
        // bridge must not break.
        var inner = Path.Combine(directory, "inner.cmd");
        File.WriteAllText(inner, "@echo off\r\nmore > nul\r\necho MY STATUS LINE\r\n");

        string Run(double? usedPercent5h)
        {
            var limits = new List<string>();
            if (usedPercent5h is not null)
            {
                limits.Add(StatusLineWindow("five_hour", usedPercent5h.Value, reset5h));
            }

            limits.Add(StatusLineWindow("seven_day", 41.2, reset7d));
            var payload = "{\"session_id\":\"s-123\",\"transcript_path\":\"C:\\\\t\\\\t.jsonl\","
                + "\"model\":{\"id\":\"claude-opus-5\"},"
                + "\"rate_limits\":{" + string.Join(",", limits) + "}}";

            var startInfo = new ProcessStartInfo(shell)
            {
                ArgumentList = { "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script, "--", inner },
                UseShellExecute = false,
                RedirectStandardInput = true,
                RedirectStandardOutput = true
            };
            startInfo.Environment["TOKEN_TRACKER_STATUSLINE_FILE"] = output;
            startInfo.Environment["TOKEN_TRACKER_STATUSLINE_DEBUG"] = "1";

            using var process = Process.Start(startInfo)
                ?? throw new InvalidOperationException("Could not start the bridge script");
            process.StandardInput.Write(payload);
            process.StandardInput.Close();
            // stderr is left attached to this test's own, so anything the script
            // complains about lands in the build log instead of a swallowed pipe.
            var printed = process.StandardOutput.ReadToEnd();
            Expect(process.WaitForExit(60_000), "The bridge script finishes");
            ExpectEqual(process.ExitCode, 0, "The bridge script exits cleanly");
            return printed;
        }

        ClaudeStatusLineRecord ReadRecord() =>
            ClaudeStatusLineBridge.Parse(File.ReadAllText(output))
            ?? throw new InvalidOperationException("The bridge wrote a record Core cannot read");

        // The user's own status line keeps printing. This is the requirement that
        // outranks everything else here: a bridge that eats the status line is
        // worse than no bridge.
        Expect(
            Run(23.5).Contains("MY STATUS LINE", StringComparison.Ordinal),
            "The wrapped status-line command still prints its own output");

        var firstScriptRecord = ReadRecord();
        ExpectEqual(firstScriptRecord.UsedPercent5h, 23.5, "The 5h percentage is recorded");
        ExpectEqual(firstScriptRecord.UsedPercent7d, 41.2, "The 7d percentage is recorded");
        ExpectEqual(firstScriptRecord.MeasuredAt, firstScriptRecord.WrittenAt, "A first reading was measured when it was written");

        // Only usage. Everything else the status line is handed — the session id,
        // the transcript path, the model — is read and dropped.
        var onDisk = File.ReadAllText(output);
        foreach (var leaked in new[] { "s-123", "transcript", "claude-opus-5", "session_id" })
        {
            Expect(!onDisk.Contains(leaked, StringComparison.Ordinal), "The bridge file does not carry " + leaked);
        }

        // Age the record on disk, so "preserved" and "recomputed" are two
        // different numbers rather than two runs landing in the same second.
        var aged = DateTimeOffset.UtcNow.AddHours(-2);
        void Backdate() => File.WriteAllText(
            output,
            StatusLinePayload(aged, aged, 23.5, reset5h, 41.2, reset7d));

        // Same numbers again: a re-run, not a measurement. This is the rule the
        // whole format exists for — the status-line command re-runs on the CLI's
        // own schedule.
        Backdate();
        Run(23.5);
        ExpectEqual(
            ReadRecord().MeasuredAt.ToUnixTimeSeconds(),
            aged.ToUnixTimeSeconds(),
            "An unchanged payload does not advance the measurement time");
        Expect(
            ReadRecord().WrittenAt > aged,
            "An unchanged payload still records that the bridge ran");

        // Numbers moved: a measurement.
        Backdate();
        Run(44.0);
        var advancedScriptRecord = ReadRecord();
        ExpectEqual(advancedScriptRecord.UsedPercent5h, 44.0, "A changed payload is recorded");
        Expect(
            advancedScriptRecord.MeasuredAt > aged,
            "A changed payload advances the measurement time");

        // A second session rendering a staler copy of the same window must not
        // drag the record backwards: within one window instance, usage only climbs.
        Run(30.0);
        ExpectEqual(ReadRecord().UsedPercent5h, 44.0, "A lower percentage for the same window does not overwrite a newer one");
        ExpectEqual(ReadRecord().MeasuredAt, advancedScriptRecord.MeasuredAt, "An older concurrent reading does not advance the measurement time");

        // Early in a session, or on a plan without them, rate_limits is absent.
        // That must not erase what is already known.
        Run(null);
        ExpectEqual(ReadRecord().UsedPercent5h, 44.0, "A payload missing one window keeps the window already recorded");
    }
    finally
    {
        Directory.Delete(directory, recursive: true);
    }
}

/// Walks up from the test binary to the checkout, so the script can be found
/// whatever the build output layout is.
static string? FindRepoFile(string relativePath)
{
    var directory = new DirectoryInfo(AppContext.BaseDirectory);
    while (directory is not null)
    {
        var candidate = Path.Combine(directory.FullName, relativePath);
        if (File.Exists(candidate))
        {
            return candidate;
        }

        directory = directory.Parent;
    }

    return null;
}

static string? ResolvePowerShell()
{
    foreach (var candidate in new[] { "pwsh", "powershell" })
    {
        try
        {
            var startInfo = new ProcessStartInfo(candidate)
            {
                ArgumentList = { "-NoProfile", "-Command", "exit 0" },
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            using var probe = Process.Start(startInfo);
            if (probe is null)
            {
                continue;
            }

            probe.StandardOutput.ReadToEnd();
            probe.StandardError.ReadToEnd();
            if (probe.WaitForExit(60_000) && probe.ExitCode == 0)
            {
                return candidate;
            }
        }
        catch
        {
        }
    }

    return null;
}

static UsageIssue CredentialIssue(Provider provider, string error, UsageSource source) =>
    UsageIssueFormatter.Issue(new ProviderUsage(
        provider,
        RemainingPercent5h: source == UsageSource.StaleCache ? (int?)42 : null,
        RemainingPercent7d: null,
        ResetAt5h: null,
        ResetAt7d: null,
        source,
        error,
        Plan: null,
        Model: null,
        UpdatedAt: DateTimeOffset.Now));

static ProviderUsage Usage(
    Provider provider,
    int? remainingPercent5h,
    int? remainingPercent7d,
    DateTimeOffset now,
    string? plan = null) =>
    new(
        provider,
        remainingPercent5h,
        remainingPercent7d,
        ResetAt5h: null,
        ResetAt7d: null,
        UsageSource.Api,
        Error: null,
        Plan: plan,
        Model: null,
        UpdatedAt: now);

sealed class QueueHttpMessageHandler : HttpMessageHandler
{
    private readonly Queue<HttpResponseMessage> responses;

    public int CallCount { get; private set; }

    /// What was asked, kept as text: the client disposes each request once sent.
    public List<(string Uri, Dictionary<string, string> Headers)> Requests { get; } = new();

    public QueueHttpMessageHandler(params HttpResponseMessage[] responses)
    {
        this.responses = new Queue<HttpResponseMessage>(responses);
    }

    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        CallCount++;
        Requests.Add((
            request.RequestUri?.AbsoluteUri ?? "",
            request.Headers.ToDictionary(header => header.Key, header => string.Join(",", header.Value), StringComparer.OrdinalIgnoreCase)));
        if (responses.Count == 0)
        {
            throw new InvalidOperationException("No queued HTTP response");
        }

        return Task.FromResult(responses.Dequeue());
    }
}

/// The DPAPI stand-in. Counts reads, so a test can tell the key was never touched.
sealed class MemoryAdminKeys : IAdminKeyStore
{
    private readonly Dictionary<Provider, string> keys = new();

    public int Reads { get; private set; }

    public string? Key(Provider provider)
    {
        Reads++;
        return keys.TryGetValue(provider, out var key) ? key : null;
    }

    public bool Save(Provider provider, string key)
    {
        keys[provider] = key;
        return true;
    }

    public void Delete(Provider provider) => keys.Remove(provider);
}
