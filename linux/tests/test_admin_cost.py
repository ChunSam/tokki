"""Admin API cost: periods, parsers, requests, client, service, view, tray and
budget alerts. Same truth tables as RunCostTests / RunAdminCostTests /
RunCostDisplayTests / RunCostAlertTests in the Windows smoke tests."""

from __future__ import annotations

import json
import random
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tokki.core import admin_cost as ac  # noqa: E402
from tokki.core import cost  # noqa: E402
from tokki.core.client import HttpResponse  # noqa: E402
from tokki.core.models import AppLanguage, DisplayMode, Provider, ProviderUsage, UsageSnapshot, UsageSource  # noqa: E402
from tokki.core import display  # noqa: E402
from tokki.i18n import Localizer  # noqa: E402

SEOUL = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
KOLKATA = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
EN = Localizer(AppLanguage.ENGLISH).text
KO = Localizer(AppLanguage.KOREAN).text


def at(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def usd(text: str) -> cost.CostAmount:
    return cost.CostAmount(Decimal(text))


NOW = at("2026-11-03T05:00:00Z")


def iso(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def anthropic_hourly(hours, start, cents, last_cents, refreshed):
    first = at(start)
    rows = []
    for hour in range(hours):
        begin = first + timedelta(hours=hour)
        amount = last_cents if hour == hours - 1 else cents
        rows.append({"starting_at": iso(begin), "ending_at": iso(begin + timedelta(hours=1)), "group": {"model": None},
                     "cost": {"currency": "USD", "amount": amount, "list_amount": cents, "requests": 3}})
    return json.dumps({"data": rows, "has_more": False, "next_page": None, "data_refreshed_at": refreshed})


class PeriodAndParserTests(unittest.TestCase):
    def test_periods(self):
        self.assertEqual(ac.day_start(NOW, SEOUL), at("2026-11-02T15:00:00Z"))
        self.assertEqual(ac.month_start(NOW, SEOUL), at("2026-10-31T15:00:00Z"))
        self.assertEqual(ac.day_start(NOW, NEW_YORK), at("2026-11-03T05:00:00Z"))
        self.assertEqual(ac.month_start(NOW, NEW_YORK), at("2026-11-01T04:00:00Z"))
        self.assertEqual(ac.day_start(NOW, KOLKATA), at("2026-11-02T18:30:00Z"))
        self.assertEqual(ac.day_start(NOW, UTC), at("2026-11-03T00:00:00Z"))
        self.assertEqual(ac.day_start(at("2026-11-02T12:00:00Z"), NEW_YORK) - ac.day_start(at("2026-11-01T12:00:00Z"), NEW_YORK), timedelta(hours=25))
        self.assertEqual(ac.day_start(at("2026-03-09T12:00:00Z"), NEW_YORK) - ac.day_start(at("2026-03-08T12:00:00Z"), NEW_YORK), timedelta(hours=23))
        self.assertEqual(ac.request_range(NOW, SEOUL)[0], at("2026-10-31T15:00:00Z"))

    def test_anthropic_and_aggregator(self):
        hourly = ac.parse_anthropic_page(anthropic_hourly(16, "2026-11-02T13:00:00Z", "100.000000", "33.333333", "2026-11-03T04:30:00Z"))
        self.assertEqual(len(hourly.buckets), 16)
        self.assertEqual(hourly.freshness, ac.CostFreshness(ac.FreshnessKind.REFRESHED_AT, at("2026-11-03T04:30:00Z")))
        self.assertIsNone(hourly.next_page)
        seoul = ac.report(hourly.buckets, hourly.freshness, NOW, SEOUL)
        self.assertEqual(seoul.month_to_date.value, Decimal("15.33333333"))
        self.assertEqual(seoul.today.value, Decimal("13.33333333"))
        self.assertEqual(ac.report(hourly.buckets, hourly.freshness, NOW, UTC).today.value, Decimal("4.33333333"))
        self.assertEqual(ac.report(hourly.buckets, hourly.freshness, NOW, KOLKATA).today.value, Decimal("10.33333333"))
        stale = ac.report(hourly.buckets, ac.CostFreshness(ac.FreshnessKind.REFRESHED_AT, at("2026-11-02T14:00:00Z")), NOW, SEOUL)
        self.assertIsNone(stale.today)
        self.assertEqual(stale.month_to_date.value, Decimal("15.33333333"))
        before = ac.report([], ac.CostFreshness(ac.FreshnessKind.REFRESHED_AT, at("2026-10-31T12:00:00Z")), NOW, SEOUL)
        self.assertTrue(before.month_to_date is None and before.today is None)
        empty = ac.parse_anthropic_page('{"data":[],"has_more":false,"next_page":null,"data_refreshed_at":null}')
        self.assertEqual(empty.freshness, ac.NOT_TALLIED)
        tiny = ac.parse_anthropic_page(anthropic_hourly(744, "2026-10-01T00:00:00Z", "0.100000", "0.100000", "2026-11-01T00:00:00Z"))
        self.assertEqual(ac.report(tiny.buckets, tiny.freshness, at("2026-10-31T23:59:59Z"), UTC).month_to_date.value, Decimal("0.744"))

    def test_models_and_errors(self):
        models = ac.parse_anthropic_page("""{"data":[
          {"starting_at":"2026-11-01T00:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-sonnet-5-5"},"cost":{"currency":"USD","amount":"412.500000"}},
          {"starting_at":"2026-11-01T00:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-opus-5-5"},"cost":{"currency":"USD","amount":"1120.000000"}},
          {"starting_at":"2026-11-01T00:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":null},"cost":{"currency":"USD","amount":"12.000000"}}
        ],"has_more":true,"next_page":"page_2","data_refreshed_at":"2026-11-03T04:30:00Z"}""")
        self.assertEqual(models.next_page, "page_2")
        self.assertEqual([i.name for i in ac.top_items(models.items)], ["claude-opus-5-5", "claude-sonnet-5-5"])
        self.assertEqual(ac.top_items(models.items)[0].amount.value, Decimal("11.2"))
        self.assertIsNone(ac.parse_anthropic_page('{"error":{"type":"permission_error"}}'))

    def test_openai(self):
        utc_days = ac.parse_openai_page("""{"object":"page","data":[
          {"object":"bucket","start_time":1793404800,"end_time":1793491200,"results":[{"amount":{"value":5.0,"currency":"usd"},"line_item":"gpt-6, input"}]},
          {"object":"bucket","start_time":1793491200,"end_time":1793577600,"results":[{"amount":{"value":1.1,"currency":"usd"},"line_item":"gpt-6, input"},{"amount":{"value":0.06,"currency":"usd"},"line_item":"web search"}]},
          {"object":"bucket","start_time":1793577600,"end_time":1793664000,"result":[{"amount":{"value":2.2,"currency":"usd"},"line_item":"gpt-6, output"}]},
          {"object":"bucket","start_time":1793664000,"end_time":1793750400,"results":[{"amount":{"value":0.3,"currency":"usd"},"line_item":"gpt-6, output"}]}
        ],"has_more":false,"next_page":null}""")
        self.assertEqual(len(utc_days.buckets), 4)
        self.assertEqual(utc_days.buckets[1].amount, cost.CostAmount(Decimal("1.16"), "USD"))
        in_seoul = ac.report(utc_days.buckets, utc_days.freshness, NOW, SEOUL)
        self.assertIsNone(in_seoul.today)
        self.assertEqual(in_seoul.month_to_date.value, Decimal("8.66"))
        in_utc = ac.report(utc_days.buckets, utc_days.freshness, NOW, UTC)
        self.assertEqual(in_utc.today.value, Decimal("0.3"))
        self.assertEqual(in_utc.month_to_date.value, Decimal("3.66"))
        local = ac.parse_openai_page("""{"data":[
          {"start_time":1793458800,"end_time":1793545200,"results":[{"amount":{"value":1.0,"currency":"usd"}}]},
          {"start_time":1793545200,"end_time":1793631600,"results":[{"amount":{"value":2.0,"currency":"usd"}}]},
          {"start_time":1793631600,"end_time":1793718000,"results":[{"amount":{"value":0.25,"currency":"usd"}}]}
        ],"has_more":false}""")
        local_report = ac.report(local.buckets, local.freshness, NOW, SEOUL)
        self.assertEqual(local_report.today.value, Decimal("0.25"))
        self.assertEqual(local_report.month_to_date.value, Decimal("3.25"))
        self.assertEqual([i.name for i in ac.top_items(utc_days.items)], ["gpt-6, input", "gpt-6, output", "web search"])


class Queue:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, url, headers):
        self.requests.append((url, dict(headers)))
        return self.responses.pop(0)


def reply(status, body, headers=None):
    return HttpResponse(status, headers or {}, body)


class ClientAndServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_requests_and_keys(self):
        start, end = ac.request_range(NOW, SEOUL)
        self.assertEqual(ac.anthropic_hourly(start, end, None),
                         "https://api.anthropic.com/v1/organizations/analytics/cost?starting_at=2026-10-31T15:00:00Z&ending_at=2026-11-03T05:00:00Z&time_bucket_width=1h&limit=1000")
        self.assertEqual(ac.anthropic_by_model(start, end, "page_a+b/c"),
                         "https://api.anthropic.com/v1/organizations/analytics/cost?starting_at=2026-10-31T15:00:00Z&ending_at=2026-11-03T05:00:00Z&group_by%5B%5D=model&limit=100&page=page_a%2Bb%2Fc")
        self.assertEqual(ac.openai_days(start, end, None),
                         "https://api.openai.com/v1/organization/costs?start_time=1793458800&end_time=1793682000&bucket_width=1d&group_by%5B%5D=line_item&limit=31")
        fs, fe = ac.request_range(at("2026-11-01T00:40:00Z"), UTC)
        self.assertIsNone(ac.anthropic_hourly(fs, fe, None))
        self.assertIsNotNone(ac.openai_days(fs, fe, None))
        headers = ac.request_headers(Provider.CLAUDE, "sk-ant-admin01-key")
        self.assertEqual(headers["x-api-key"], "sk-ant-admin01-key")
        self.assertEqual(headers["anthropic-beta"], "analytics-usage-cost-2026-08-24")
        self.assertEqual(ac.request_headers(Provider.CODEX, "sk-admin-key")["Authorization"], "Bearer sk-admin-key")
        self.assertEqual(ac.key_suffix("sk-ant-admin01-abcdefgh1234"), "…1234")
        self.assertEqual(ac.key_suffix("short"), "…")
        self.assertTrue(ac.looks_like_admin_key("sk-ant-admin01-abcdefgh", Provider.CLAUDE))
        self.assertFalse(ac.looks_like_admin_key("sk-ant-api03-abcdefgh", Provider.CLAUDE))
        self.assertTrue(ac.looks_like_admin_key(" sk-admin-abcdefgh\n", Provider.CODEX))
        self.assertFalse(ac.looks_like_admin_key("sk-proj-abcdefgh", Provider.CODEX))

    def test_anthropic_client(self):
        queue = Queue(
            reply(200, """{"data":[
              {"starting_at":"2026-11-01T03:00:00Z","ending_at":"2026-11-01T04:00:00Z","group":{},"cost":{"currency":"USD","amount":"250000.000000"}},
              {"starting_at":"2026-11-02T15:00:00Z","ending_at":"2026-11-02T16:00:00Z","group":{},"cost":{"currency":"USD","amount":"1234.500000"}}
            ],"has_more":true,"next_page":"page_2","data_refreshed_at":"2026-11-03T04:00:00Z"}"""),
            reply(200, """{"data":[
              {"starting_at":"2026-11-03T01:00:00Z","ending_at":"2026-11-03T02:00:00Z","group":{},"cost":{"currency":"USD","amount":"100.000000"}}
            ],"has_more":false,"next_page":null,"data_refreshed_at":"2026-11-03T04:00:00Z"}"""),
            reply(200, """{"data":[
              {"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-opus-5-5"},"cost":{"currency":"USD","amount":"200000.000000"}},
              {"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-sonnet-5-5"},"cost":{"currency":"USD","amount":"51334.500000"}},
              {"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":null},"cost":{"currency":"USD","amount":"0.000000"}},
              {"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-haiku-5-5"},"cost":{"currency":"USD","amount":"50.000000"}},
              {"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-fable-5-1"},"cost":{"currency":"USD","amount":"10.000000"}}
            ],"has_more":false,"next_page":null,"data_refreshed_at":"2026-11-03T04:00:00Z"}"""))
        client = ac.AdminCostClient(Provider.CLAUDE, queue, self.dir / "c.json")
        result = client.fetch("sk-ant-admin01-abcdefgh1234", NOW, SEOUL)
        c = result.cost
        self.assertIsNotNone(c, result.failure)
        self.assertEqual(c.month_to_date.value, Decimal("2513.345"))
        self.assertEqual(c.today.value, Decimal("13.345"))
        self.assertEqual(c.data_refreshed_at, at("2026-11-03T04:00:00Z"))
        self.assertEqual([i.name for i in c.top], ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"])
        self.assertEqual(len(queue.requests), 3)
        self.assertTrue(queue.requests[1][0].endswith("&page=page_2"))
        self.assertEqual(queue.requests[0][1]["x-api-key"], "sk-ant-admin01-abcdefgh1234")
        untallied_queue = Queue(reply(200, '{"data":[],"has_more":false,"next_page":null,"data_refreshed_at":null}'))
        untallied = ac.AdminCostClient(Provider.CLAUDE, untallied_queue, self.dir / "u.json").fetch("sk-ant-admin01-abcdefgh1234", NOW, SEOUL)
        self.assertTrue(untallied.cost.month_to_date is None and untallied.cost.today is None)
        self.assertEqual(len(untallied_queue.requests), 1)
        # A reading holds for its local month; today for its day.
        self.assertEqual(ac.usable(c, at("2026-11-03T14:59:00Z"), SEOUL), c)
        nxt = ac.usable(c, at("2026-11-03T15:30:00Z"), SEOUL)
        self.assertTrue(nxt.today is None and nxt.month_to_date == c.month_to_date)
        self.assertIsNone(ac.usable(c, at("2026-11-30T15:30:00Z"), SEOUL))

    def test_openai_client_failures(self):
        queue = Queue(
            reply(200, """{"object":"page","data":[
              {"object":"bucket","start_time":1793458800,"end_time":1793545200,"results":[
                {"amount":{"value":1.25,"currency":"usd"},"line_item":"gpt-6, input"},
                {"amount":{"value":0.75,"currency":"usd"},"line_item":"gpt-6, output"}]},
              {"object":"bucket","start_time":1793631600,"end_time":1793718000,"results":[
                {"amount":{"value":0.5,"currency":"usd"},"line_item":"gpt-6, input"}]}
            ],"has_more":false,"next_page":null}"""),
            reply(401, '{"error":{"message":"Invalid API key"}}'),
            reply(429, "{}", {"retry-after": "30"}),
            reply(500, "{}"))
        client = ac.AdminCostClient(Provider.CODEX, queue, self.dir / "x.json", random.Random(0))
        c = client.fetch("sk-admin-abcdefgh5678", NOW, SEOUL).cost
        self.assertEqual(c.month_to_date.value, Decimal("2.5"))
        self.assertEqual(c.today.value, Decimal("0.5"))
        self.assertIsNone(c.data_refreshed_at)
        self.assertEqual([i.name for i in c.top], ["gpt-6"])
        self.assertEqual(c.top[0].amount.value, Decimal("2.5"))
        self.assertEqual(client.fetch("sk-admin-abcdefgh5678", NOW, SEOUL),
                         ac.CostFetchResult(None, ac.CostFailure(ac.FailureKind.KEY_REJECTED, "HTTP 401 from OpenAI Admin API")))
        self.assertIs(client.fetch("sk-admin-abcdefgh5678", NOW, SEOUL).failure.kind, ac.FailureKind.RATE_LIMITED)
        self.assertIs(client.fetch("sk-admin-abcdefgh5678", NOW, SEOUL).failure.kind, ac.FailureKind.RATE_LIMITED)
        self.assertEqual(len(queue.requests), 3)
        self.assertIs(client.fetch("sk-admin-otherkey9999", NOW, SEOUL).failure.kind, ac.FailureKind.FAILED)
        self.assertEqual(len(queue.requests), 4)

    def test_service(self):
        keys = ac.MemoryKeyStore()
        queue = Queue(
            reply(200, '{"data":[{"starting_at":"2026-11-03T01:00:00Z","ending_at":"2026-11-03T02:00:00Z","group":{},"cost":{"currency":"USD","amount":"4200.000000"}}],"has_more":false,"data_refreshed_at":"2026-11-03T04:00:00Z"}'),
            reply(200, '{"data":[{"starting_at":"2026-10-31T15:00:00Z","ending_at":"2026-11-03T05:00:00Z","group":{"model":"claude-opus-5-5"},"cost":{"currency":"USD","amount":"4200.000000"}}],"has_more":false,"data_refreshed_at":"2026-11-03T04:00:00Z"}'),
            reply(500, "{}"),
            reply(401, "{}"))
        cache_path = self.dir / "cost-cache.json"

        def service(transport):
            return ac.AdminCostService(keys, {Provider.CLAUDE: ac.AdminCostClient(Provider.CLAUDE, transport, self.dir / "s.json")}, ac.CostCacheStore(cache_path))

        claude_on = {Provider.CLAUDE: True}
        costs = service(queue)
        off = costs.refresh_if_due({}, NOW, SEOUL)
        self.assertTrue(not off[Provider.CLAUDE].enabled and keys.reads == 0)
        self.assertEqual(ac.diagnostics_lines(Provider.CLAUDE, off[Provider.CLAUDE]), ["Claude API cost: off"])
        no_key = costs.refresh_if_due(claude_on, NOW, SEOUL)
        self.assertTrue(no_key[Provider.CLAUDE].enabled and no_key[Provider.CLAUDE].key_suffix is None)
        self.assertEqual(ac.diagnostics_lines(Provider.CLAUDE, no_key[Provider.CLAUDE]), ["Claude API cost: on, no Admin key"])
        self.assertEqual(len(queue.requests), 0)

        keys.save(Provider.CLAUDE, "sk-ant-admin01-abcdefgh1234")
        fetched = costs.refresh_if_due(claude_on, NOW, SEOUL)[Provider.CLAUDE]
        self.assertEqual(fetched.cost.month_to_date.value, Decimal("42"))
        self.assertEqual(fetched.next_fetch_at, NOW + timedelta(seconds=600))
        self.assertEqual(ac.diagnostics_lines(Provider.CLAUDE, fetched), [
            "Claude API cost: on, key …1234",
            "Claude API cost month: $42.00",
            "Claude API cost today: $42.00",
            "Claude API cost complete up to: 2026-11-03T04:00:00Z",
            "Claude API cost top: claude-opus-5-5 $42.00",
            "Claude API cost measured: 2026-11-03T05:00:00Z",
            "Claude API cost last fetch: 2026-11-03T05:00:00Z ok",
        ])
        costs.refresh_if_due(claude_on, NOW + timedelta(seconds=60), SEOUL)
        self.assertEqual(len(queue.requests), 2)
        failed = costs.refresh_if_due(claude_on, NOW + timedelta(seconds=601), SEOUL)[Provider.CLAUDE]
        self.assertTrue(failed.failure.kind is ac.FailureKind.FAILED and failed.is_cached)
        self.assertEqual(failed.cost.month_to_date.value, Decimal("42"))
        self.assertEqual(failed.next_fetch_at, NOW + timedelta(seconds=901))
        relaunched = service(Queue(reply(503, "{}"))).refresh_if_due(claude_on, NOW + timedelta(seconds=700), SEOUL)[Provider.CLAUDE]
        self.assertTrue(relaunched.is_cached and relaunched.cost.month_to_date.value == Decimal("42"))
        keys.save(Provider.CLAUDE, "sk-ant-admin01-otherkey9999")
        rejected = costs.refresh_if_due(claude_on, NOW + timedelta(seconds=602), SEOUL)[Provider.CLAUDE]
        self.assertEqual(len(queue.requests), 4)
        self.assertTrue(rejected.failure.kind is ac.FailureKind.KEY_REJECTED and rejected.cost is None)
        self.assertEqual(rejected.key_suffix, "…9999")
        self.assertNotIn("abcdefgh", cache_path.read_text())


class ViewTests(unittest.TestCase):
    COST = ac.ProviderCost(Provider.CLAUDE, usd("2513.345"), usd("13.345"), [
        ac.CostLineItem("claude-opus-5-5", usd("2000")), ac.CostLineItem("claude-sonnet-5-5", usd("513.345")), ac.CostLineItem("claude-haiku-5-5", usd("0.5"))],
        at("2026-11-03T04:00:00Z"), NOW)

    @staticmethod
    def state(enabled=True, key="…1234", c=None, failure=None):
        return ac.CostState(enabled=enabled, key_suffix=key, cost=c, failure=failure)

    def v(self, state, budget=0, text=KO, lang=AppLanguage.KOREAN):
        return ac.view(state, budget, NOW, SEOUL, text, lang)

    def test_view(self):
        self.assertIsNone(self.v(self.state(enabled=False, c=self.COST)))
        self.assertIsNone(self.v(self.state(key=None)))
        loading = self.v(self.state(), 100)
        self.assertEqual((loading.status, loading.status_title), (ac.CostStatus.LOADING, "불러오는 중"))
        self.assertTrue(loading.month_value == "--" and loading.detail is None and loading.budget_text is None and loading.as_of is None)
        ok = self.v(self.state(c=self.COST), 5000)
        self.assertEqual((ok.status, ok.status_title, ok.month_label, ok.month_value), (ac.CostStatus.OK, "정상", "이번 달 사용", "$2.5K"))
        self.assertEqual((ok.budget_percent, ok.budget_text), (50, "예산 $5.0K 중 50%"))
        self.assertFalse(ok.is_budget_warning)
        self.assertEqual(ok.detail, "오늘 $13.35 · Opus 5.5 79% · Sonnet 5.5 20%")
        self.assertEqual(ok.as_of, "13:00 기준")
        en83 = self.v(self.state(c=self.COST), 3000, EN, AppLanguage.ENGLISH)
        self.assertEqual(en83.budget_text, "83% of $3.0K budget")
        self.assertTrue(en83.is_budget_warning)
        yesterday = ac.ProviderCost(Provider.CLAUDE, self.COST.month_to_date, None, [], at("2026-11-02T13:10:00Z"), NOW)
        lagging = self.v(self.state(c=yesterday), 0, EN, AppLanguage.ENGLISH)
        self.assertEqual(lagging.as_of, "as of Nov 2 22:10")
        self.assertEqual(lagging.detail, "Today not tallied yet")
        self.assertEqual(self.v(self.state(c=yesterday)).as_of, "11월 2일 22:10 기준")
        untallied = ac.ProviderCost(Provider.CLAUDE, None, None, [], None, NOW)
        empty = self.v(self.state(c=untallied), 100)
        self.assertTrue(empty.month_value == "집계 전" and empty.detail is None and empty.budget_percent is None)
        failed = ac.CostFailure(ac.FailureKind.FAILED, "HTTP 500 from Anthropic Admin API")
        cached = self.v(self.state(c=self.COST, failure=failed))
        self.assertTrue(cached.status is ac.CostStatus.CACHED and cached.status_title == "캐시 데이터 사용 중")
        self.assertEqual(cached.failure_message, "HTTP 500 from Anthropic Admin API")
        rejected = ac.CostFailure(ac.FailureKind.KEY_REJECTED, "HTTP 401 from Anthropic Admin API")
        self.assertIs(self.v(self.state(c=self.COST, failure=rejected)).status, ac.CostStatus.KEY_REJECTED)
        self.assertEqual(self.v(self.state(failure=rejected)).status_title, "Admin 키 거부됨")
        self.assertIs(self.v(self.state(failure=ac.CostFailure(ac.FailureKind.RATE_LIMITED, "HTTP 429"))).status, ac.CostStatus.RATE_LIMITED)
        self.assertEqual(self.v(self.state(failure=failed)).status_title, "비용을 가져오지 못함")

    def test_item_names(self):
        self.assertEqual(ac.item_display("claude-opus-5-5"), "Opus 5.5")
        self.assertEqual(ac.item_display("claude-sonnet-4-5-20250929"), "Sonnet 4.5")
        self.assertEqual(ac.item_display("claude-3-5-haiku-20241022"), "Haiku 3.5")
        self.assertEqual(ac.item_display("gpt-6"), "gpt-6")
        self.assertEqual(ac.line_item_group("gpt-6, input"), "gpt-6")
        self.assertEqual(ac.line_item_group("web search"), "web search")

    def test_tray(self):
        small = ac.ProviderCost(Provider.CLAUDE, usd("42.18"), None, [], None, NOW)
        states = {Provider.CLAUDE: self.state(c=small), Provider.CODEX: self.state(c=small)}
        claude_off = {Provider.CLAUDE: False, Provider.CODEX: True}
        both_off = {Provider.CLAUDE: False, Provider.CODEX: False}
        entries = ac.menu_bar_entries(claude_off, states, {Provider.CLAUDE: 50})
        self.assertEqual(entries, {Provider.CLAUDE: ac.MenuBarCost("$42", True)})
        self.assertEqual(ac.menu_bar_entries(both_off, {Provider.CLAUDE: self.state()}, {}), {Provider.CLAUDE: ac.MenuBarCost("--", False)})
        self.assertEqual(ac.menu_bar_entries(both_off, {Provider.CLAUDE: self.state(key=None)}, {}), {})
        snapshot = UsageSnapshot(ProviderUsage.unavailable(Provider.CLAUDE, "Disabled", NOW),
                                 ProviderUsage(Provider.CODEX, None, 91, None, None, UsageSource.API, None, "plus", NOW), NOW)
        spend = {Provider.CLAUDE: ac.MenuBarCost("$42", False)}
        A = display.ProviderLabelStyle
        self.assertEqual(display.status_title(snapshot, DisplayMode.BOTH, costs=spend), "Cdx 91% · Cl $42")
        self.assertEqual(display.status_title(snapshot, DisplayMode.CLAUDE_ONLY, costs=spend), "Cl $42")
        self.assertEqual(display.status_title(snapshot, DisplayMode.CLAUDE_ONLY, A.ICON, spend), "Claude $42")
        self.assertEqual(display.status_title(snapshot, DisplayMode.LOWEST_REMAINING, costs=spend), "AI 91%")
        no_limits = UsageSnapshot(ProviderUsage.unavailable(Provider.CLAUDE, "Disabled", NOW), ProviderUsage.unavailable(Provider.CODEX, "Disabled", NOW), NOW)
        both = {Provider.CLAUDE: ac.MenuBarCost("$42", False), Provider.CODEX: ac.MenuBarCost("$8.00", False)}
        self.assertEqual(display.status_title(no_limits, DisplayMode.LOWEST_REMAINING, costs=both), "Cl $42 · Cdx $8.00")
        self.assertEqual(display.status_title(snapshot, DisplayMode.BOTH), "Cdx 91% · Cl --")


class BudgetAlertTests(unittest.TestCase):
    def spent(self, month, key="…1234"):
        return {Provider.CLAUDE: ac.CostState(enabled=True, key_suffix=key, cost=ac.ProviderCost(
            Provider.CLAUDE, None if month is None else usd(month), None, [], None, NOW))}

    def evaluate(self, states, budget=100, delivered=(), notifications=True, when=None, text=KO):
        when = when or NOW
        return cost.evaluate_alerts(None, list(delivered), notifications, when, text, SEOUL,
                                    extra_alerts=lambda sent, month: ac.budget_alerts(states, {Provider.CLAUDE: budget}, sent, when, SEOUL, text))

    def test_budget(self):
        at84 = self.evaluate(self.spent("84.2"))
        self.assertEqual([a.id for a in at84.alerts], ["claude-cost-80-2026-11"])
        self.assertEqual(at84.alerts[0].title, "API 비용 예산")
        self.assertEqual(at84.alerts[0].body, "Anthropic API: 이번 달 $84.20, 예산 $100의 84%")
        self.assertEqual(at84.delivered, {"claude-cost-80-2026-11"})
        self.assertEqual(self.evaluate(self.spent("90"), delivered=at84.delivered).alerts, [])
        self.assertEqual([a.id for a in self.evaluate(self.spent("105"), delivered=at84.delivered).alerts], ["claude-cost-100-2026-11"])
        jump = self.evaluate(self.spent("105"))
        self.assertEqual([a.id for a in jump.alerts], ["claude-cost-100-2026-11"])
        self.assertEqual(jump.delivered, {"claude-cost-100-2026-11", "claude-cost-80-2026-11"})
        self.assertEqual(self.evaluate(self.spent("84.2"), text=EN).alerts[0].body, "Anthropic API: $84.20 this month, 84% of the $100 budget")
        self.assertEqual(self.evaluate(self.spent("79.99")).alerts, [])
        self.assertEqual(self.evaluate(self.spent("500"), budget=0).alerts, [])
        self.assertEqual(self.evaluate(self.spent("500", key=None)).alerts, [])
        self.assertEqual(self.evaluate(self.spent(None)).alerts, [])
        off = self.evaluate(self.spent("105"), delivered=["claude-cost-80-2026-11"], notifications=False)
        self.assertEqual(off.alerts, [])
        self.assertEqual(off.delivered, {"claude-cost-80-2026-11"})
        december = self.evaluate({}, delivered=["claude-cost-100-2026-11"], when=at("2026-11-30T15:30:00Z"))
        self.assertEqual(december.delivered, set())


if __name__ == "__main__":
    unittest.main()
