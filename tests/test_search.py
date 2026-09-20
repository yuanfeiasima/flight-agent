from datetime import date

from flight_agent.config import Settings
from flight_agent.engine.search import search_sites
from flight_agent.models import Flight, SearchQuery, SiteResult


class _Empty:
    def search(self, session, query, settings):
        return SiteResult(site="empty", url="u", warnings=["empty"])


class _Good:
    def search(self, session, query, settings):
        return SiteResult(site="good", url="u", flights=[
            Flight("航司", "CA1234", price=500, cabin="经济舱")
        ])


class _Never:
    def search(self, session, query, settings):
        raise AssertionError("successful fallback should stop the chain")


def test_search_falls_back_and_stops_after_success(monkeypatch):
    monkeypatch.setattr("flight_agent.engine.search.ADAPTERS", {
        "empty": _Empty, "good": _Good, "never": _Never,
    })
    q = SearchQuery("北京", "上海", "BJS", "SHA", date(2026, 9, 20))
    settings = Settings(sites=("empty", "good", "never"), stop_after_first_success=True)
    results = search_sites(object(), q, settings)
    assert [r.site for r in results] == ["empty", "good"]


def test_priced_but_ineligible_result_still_falls_back(monkeypatch):
    class _WrongCabin:
        def search(self, session, query, settings):
            return SiteResult(site="wrong", url="u", flights=[
                Flight("航司", "CA1234", price=100, cabin="公务舱")
            ])

    monkeypatch.setattr("flight_agent.engine.search.ADAPTERS", {
        "wrong": _WrongCabin, "good": _Good,
    })
    q = SearchQuery("北京", "上海", "BJS", "SHA", date(2026, 9, 20))
    results = search_sites(object(), q, Settings(sites=("wrong", "good")))
    assert [r.site for r in results] == ["wrong", "good"]
