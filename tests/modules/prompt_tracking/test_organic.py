"""Tests for folding SERP samples into an organic ranking snapshot."""

from __future__ import annotations

from src.integrations.schemas import OrganicResult, SerpSnapshot
from src.modules.prompt_tracking.organic import TOP_N, build_organic_snapshot, client_position
from src.modules.prompt_tracking.schemas import ClientProfile, RankQueryKind


def _serp(domains: list[str], *, device: str = "desktop") -> SerpSnapshot:
    results = [
        OrganicResult(position=i, url=f"https://{d}/p{i}", domain=d.removeprefix("www."))
        for i, d in enumerate(domains, start=1)
    ]
    return SerpSnapshot(
        query="q", device=device, ai_overview_present=False, organic_results=results
    )


def _client(**overrides) -> ClientProfile:
    base = {
        "brand_name": "GEP",
        "domains": ["gep.com"],
        "competitor_domains": ["coupa.com", "sap.com"],
        "lob": "Procurement Software",
        "seed_keywords": ["procurement software"],
    }
    return ClientProfile(**{**base, **overrides})


class TestClientPosition:
    def test_first_matching_result_wins_including_subdomains(self):
        serp = _serp(["coupa.com", "blog.gep.com", "gep.com"])
        assert client_position(serp, ["gep.com"]) == (2, "https://blog.gep.com/p2")

    def test_lookalike_domains_do_not_match(self):
        serp = _serp(["notgep.com", "gep.com.evil.example"])
        assert client_position(serp, ["gep.com"]) is None

    def test_uses_rendered_position_not_list_order(self):
        serp = SerpSnapshot(
            query="q",
            ai_overview_present=False,
            organic_results=[
                OrganicResult(position=7, url="https://gep.com/a", domain="gep.com"),
                OrganicResult(position=2, url="https://gep.com/b", domain="gep.com"),
            ],
        )
        assert client_position(serp, ["gep.com"]) == (2, "https://gep.com/b")


class TestBuildOrganicSnapshot:
    def test_returns_none_without_samples(self):
        assert build_organic_snapshot(RankQueryKind.PROMPT, "q", [], _client()) is None

    def test_best_position_across_samples_and_competitors(self):
        samples = [
            _serp(["coupa.com", "sap.com", "gep.com"]),
            _serp(["coupa.com", "gep.com", "sap.com"]),
        ]
        snap = build_organic_snapshot(
            RankQueryKind.KEYWORD, "procurement software", samples, _client()
        )
        assert snap is not None
        assert snap.query == "procurement software"
        assert snap.query_kind is RankQueryKind.KEYWORD
        assert snap.samples == 2
        assert snap.client_position == 2
        assert snap.client_url == "https://gep.com/p2"
        assert snap.top_domains == ["coupa.com", "sap.com", "gep.com"]
        assert snap.competitor_positions == {"coupa.com": 1, "sap.com": 2}
        assert snap.device == "desktop"

    def test_absent_client_yields_no_position(self):
        snap = build_organic_snapshot(RankQueryKind.PROMPT, "q", [_serp(["coupa.com"])], _client())
        assert snap is not None
        assert snap.client_position is None
        assert snap.client_url is None
        assert snap.competitor_positions == {"coupa.com": 1}

    def test_top_domains_capped_and_device_carried(self):
        domains = [f"site{i}.example" for i in range(15)]
        snap = build_organic_snapshot(
            RankQueryKind.PROMPT, "q", [_serp(domains, device="mobile")], _client()
        )
        assert snap is not None
        assert len(snap.top_domains) == TOP_N
        assert snap.device == "mobile"

    def test_blank_competitor_entries_are_ignored(self):
        client = _client(competitor_domains=["", "coupa.com"])
        snap = build_organic_snapshot(RankQueryKind.PROMPT, "q", [_serp(["coupa.com"])], client)
        assert snap is not None
        assert snap.competitor_positions == {"coupa.com": 1}
