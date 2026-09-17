"""Tests for the Semrush connector: CSV parsing, in-band errors and unit accounting."""

from __future__ import annotations

import httpx
import pytest

from src.core.errors import IntegrationError, UpstreamClientError
from src.integrations.schemas import KeywordSource
from src.integrations.semrush import SemrushClient

_HEADER = "Keyword;Search Volume;CPC;Competition;Number of Results"
_QUESTIONS_BODY = (
    f"{_HEADER}\r\n"
    "what is procurement;1200;3.50;0.42;123000\r\n"
    "how does procurement work;480;2.10;0.18;45600\r\n"
    "why is procurement important;;;;\r\n"
    "\r\n"
)


class Recorder:
    """MockTransport handler that records requests and serves canned responses."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.responses.pop(0)


def _client(settings, *responses: httpx.Response) -> tuple[SemrushClient, Recorder]:
    recorder = Recorder(*responses)
    return SemrushClient(settings, transport=httpx.MockTransport(recorder)), recorder


class TestPhraseQuestions:
    def test_parses_semicolon_csv_and_normalises_rows(self, settings):
        client, _ = _client(settings, httpx.Response(200, text=_QUESTIONS_BODY))
        rows = client.phrase_questions("procurement", display_limit=10)

        assert [r.keyword for r in rows] == [
            "what is procurement",
            "how does procurement work",
            "why is procurement important",
        ]
        first = rows[0]
        assert first.search_volume == 1200
        assert first.cpc == pytest.approx(3.5)
        assert first.competition == pytest.approx(0.42)
        assert first.num_results == 123000
        assert first.database == settings.semrush_database
        assert first.source is KeywordSource.PHRASE_QUESTIONS

    def test_blank_numeric_cells_parse_as_zero(self, settings):
        client, _ = _client(settings, httpx.Response(200, text=_QUESTIONS_BODY))
        blank = client.phrase_questions("procurement")[2]
        assert (blank.search_volume, blank.cpc, blank.competition, blank.num_results) == (
            0,
            0.0,
            0.0,
            0,
        )

    def test_request_always_carries_limit_database_columns_and_key(self, settings):
        client, recorder = _client(settings, httpx.Response(200, text=_QUESTIONS_BODY))
        client.phrase_questions("procurement", display_limit=7)

        request = recorder.requests[0]
        assert request.method == "GET"
        assert request.url.host == "api.semrush.com"
        params = request.url.params
        assert params["type"] == "phrase_questions"
        assert params["phrase"] == "procurement"
        assert params["display_limit"] == "7"
        assert params["database"] == settings.semrush_database
        assert params["export_columns"] == "Ph,Nq,Cp,Co,Nr"
        assert params["key"] == "test-semrush-key"

    def test_default_display_limit_comes_from_settings(self, settings):
        client, recorder = _client(settings, httpx.Response(200, text=_QUESTIONS_BODY))
        client.phrase_questions("procurement")
        expected = str(settings.semrush_display_limit)
        assert recorder.requests[0].url.params["display_limit"] == expected

    def test_negative_display_limit_is_rejected(self, settings):
        client, recorder = _client(settings)
        with pytest.raises(ValueError, match="at least 1"):
            client.phrase_questions("procurement", display_limit=-1)
        assert recorder.requests == []

    def test_zero_display_limit_is_rejected(self, settings):
        client, recorder = _client(settings, httpx.Response(200, text=_QUESTIONS_BODY))
        with pytest.raises(ValueError, match="at least 1"):
            client.phrase_questions("procurement", display_limit=0)
        assert recorder.requests == []


class TestPhraseAll:
    def test_returns_the_first_row(self, settings):
        body = f"{_HEADER}\r\nprocurement;9900;4.20;0.77;5000000\r\n"
        client, recorder = _client(settings, httpx.Response(200, text=body))
        record = client.phrase_all("procurement")

        assert record is not None
        assert record.keyword == "procurement"
        assert record.search_volume == 9900
        assert record.source is KeywordSource.PHRASE_ALL
        assert recorder.requests[0].url.params["type"] == "phrase_all"
        assert recorder.requests[0].url.params["display_limit"] == "1"

    def test_returns_none_when_semrush_knows_nothing(self, settings):
        client, _ = _client(settings, httpx.Response(200, text="ERROR 50 :: NOTHING FOUND"))
        assert client.phrase_all("zxqv") is None


class TestPhraseRelated:
    def test_uses_the_related_report(self, settings):
        body = f"{_HEADER}\r\nsourcing;300;1.00;0.10;1000\r\n"
        client, recorder = _client(settings, httpx.Response(200, text=body))
        rows = client.phrase_related("procurement", display_limit=3)
        assert rows[0].source is KeywordSource.PHRASE_RELATED
        assert recorder.requests[0].url.params["type"] == "phrase_related"


class TestInBandErrors:
    def test_nothing_found_is_an_empty_result(self, settings):
        client, _ = _client(settings, httpx.Response(200, text="ERROR 50 :: NOTHING FOUND\n"))
        assert client.phrase_questions("zxqv") == []
        assert client.units_consumed == 0

    def test_empty_body_is_an_empty_result(self, settings):
        client, _ = _client(settings, httpx.Response(200, text="   \r\n"))
        assert client.phrase_questions("zxqv") == []

    def test_other_error_bodies_are_client_errors(self, settings):
        client, _ = _client(settings, httpx.Response(200, text="ERROR 120 :: WRONG KEY - ID PAIR"))
        with pytest.raises(UpstreamClientError) as info:
            client.phrase_questions("procurement")
        assert info.value.status_code == 200
        assert "WRONG KEY" in info.value.detail

    def test_http_500_is_an_integration_error(self, settings):
        client, _ = _client(settings, httpx.Response(500, text="upstream down"))
        with pytest.raises(IntegrationError, match="HTTP 500"):
            client.phrase_questions("procurement")

    def test_short_rows_are_skipped(self, settings):
        body = f"{_HEADER}\r\nonly;two\r\n;100;1;0.1;10\r\nok;1;1;1.7;1\r\n"
        client, _ = _client(settings, httpx.Response(200, text=body))
        rows = client.phrase_questions("procurement")
        assert [r.keyword for r in rows] == ["ok"]
        # Competition is clamped so a vendor glitch cannot break the schema.
        assert rows[0].competition == 1.0

    def test_garbage_numeric_cells_parse_as_zero(self, settings):
        body = f"{_HEADER}\r\nk;n/a;abc;-;x\r\n"
        client, _ = _client(settings, httpx.Response(200, text=body))
        row = client.phrase_questions("procurement")[0]
        assert (row.search_volume, row.cpc, row.competition, row.num_results) == (0, 0.0, 0.0, 0)


class TestUnitAccounting:
    def test_estimate_units_uses_published_rates(self, settings):
        client, _ = _client(settings)
        assert client.estimate_units(KeywordSource.PHRASE_QUESTIONS, 3) == 120
        assert client.estimate_units(KeywordSource.PHRASE_ALL, 3) == 30
        assert client.estimate_units(KeywordSource.PHRASE_RELATED, 2) == 80

    def test_units_consumed_accumulates_per_returned_row(self, settings):
        body = f"{_HEADER}\r\nprocurement;9900;4.20;0.77;5000000\r\n"
        client, _ = _client(
            settings,
            httpx.Response(200, text=_QUESTIONS_BODY),
            httpx.Response(200, text=body),
        )
        assert client.units_consumed == 0
        client.phrase_questions("procurement", display_limit=10)
        assert client.units_consumed == 3 * 40
        client.phrase_all("procurement")
        assert client.units_consumed == 3 * 40 + 10

    def test_ceiling_refuses_before_any_request(self, settings):
        settings.semrush_max_units_per_run = 100
        client, recorder = _client(settings, httpx.Response(200, text=_QUESTIONS_BODY))
        with pytest.raises(IntegrationError, match="per-run ceiling of 100") as info:
            client.phrase_questions("procurement", display_limit=3)
        assert info.value.service == "semrush"
        assert recorder.requests == []
        assert client.units_consumed == 0

    def test_ceiling_accounts_for_units_already_spent(self, settings):
        settings.semrush_max_units_per_run = 125
        client, recorder = _client(settings, httpx.Response(200, text=_QUESTIONS_BODY))
        client.phrase_questions("procurement", display_limit=3)
        assert client.units_consumed == 120
        with pytest.raises(IntegrationError, match="would reach 130 units"):
            client.phrase_all("procurement")
        assert len(recorder.requests) == 1
