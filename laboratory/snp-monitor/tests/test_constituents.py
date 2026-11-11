from pathlib import Path

import pytest

from snp_monitor.constituents import (
    MIN_CONSTITUENTS,
    Constituent,
    ConstituentList,
    ConstituentsProvider,
    company_name,
    parse_datasets_csv,
    parse_wikipedia,
    write_snapshot,
)

WIKI_HTML = """
<html><body>
<table class="wikitable" id="other"><tr><th>Symbol</th></tr><tr><td>NOPE</td></tr></table>
<table class="wikitable sortable" id="constituents"><tbody>
<tr><th><a href="#">Symbol</a></th><th>Security</th><th><a>GICS</a> Sector</th><th>GICS Sub-Industry</th>
<th>Headquarters Location</th><th>Date added</th><th><a>CIK</a></th><th>Founded</th></tr>
<tr><td><a href="x">MMM</a></td><td><a>3M</a></td><td>Industrials</td><td>Industrial Conglomerates</td>
<td><a>Saint Paul</a>, Minnesota</td><td>1957-03-04</td><td>0000066740</td><td>1902</td></tr>
<tr><td>GOOGL</td><td>Alphabet Inc. (Class A)</td><td>Communication Services</td><td>Interactive Media</td>
<td>Mountain View</td><td>2014-04-03</td><td>0001652044</td><td>1998</td></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td><td>Multi-Sector Holdings</td>
<td>Omaha</td><td>2010-02-16</td><td>0001067983</td><td>1839</td></tr>
</tbody></table>
</body></html>
"""

CSV_TEXT = """Symbol,Security,GICS Sector,GICS Sub-Industry,Headquarters Location,Date added,CIK,Founded
MMM,3M,Industrials,Industrial Conglomerates,"Saint Paul, Minnesota",1957-03-04,66740,1902
GOOG,Alphabet Inc. (Class C),Communication Services,Interactive Media,"Mountain View, California",2006-04-03,1652044,1998
"""


def test_parse_wikipedia_reads_only_the_constituents_table():
    items = parse_wikipedia(WIKI_HTML)
    assert [i.symbol for i in items] == ["MMM", "GOOGL", "BRK.B"]
    assert items[0] == Constituent("MMM", "3M", "Industrials", "Industrial Conglomerates", "66740")
    assert items[2].yahoo_symbol == "BRK-B"


def test_parse_datasets_csv():
    items = parse_datasets_csv(CSV_TEXT)
    assert [i.symbol for i in items] == ["MMM", "GOOG"]
    assert items[1].cik == "1652044"


def test_company_name_strips_share_class():
    assert company_name("Alphabet Inc. (Class A)") == "Alphabet Inc."
    assert company_name("News Corp (Class B)") == "News Corp"
    assert company_name("3M") == "3M"


def _members(count: int) -> list[Constituent]:
    return [Constituent(f"S{i}", f"Company {i}") for i in range(count)]


class FakeSource:
    def __init__(self, name, items=None, error=None):
        self.name = name
        self.items = items
        self.error = error
        self.calls = 0

    def fetch(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.items


def test_provider_uses_fresh_cache_without_downloading(tmp_path: Path):
    clock = [1000.0]
    source = FakeSource("wikipedia", _members(MIN_CONSTITUENTS))
    provider = ConstituentsProvider([source], tmp_path / "c.json", max_age_seconds=100,
                                    snapshot_file=tmp_path / "none.json", clock=lambda: clock[0])
    assert provider.get().source == "wikipedia"
    clock[0] += 50
    assert len(provider.get().items) == MIN_CONSTITUENTS
    assert source.calls == 1
    clock[0] += 100
    provider.get()
    assert source.calls == 2


def test_provider_falls_back_to_next_source_then_stale_cache(tmp_path: Path):
    clock = [1000.0]
    broken = FakeSource("wikipedia", error=OSError("offline"))
    too_short = FakeSource("short", _members(10))
    good = FakeSource("github-datasets", _members(MIN_CONSTITUENTS))
    cache = tmp_path / "c.json"
    provider = ConstituentsProvider([broken, too_short, good], cache, max_age_seconds=10,
                                    snapshot_file=tmp_path / "none.json", clock=lambda: clock[0])
    assert provider.get().source == "github-datasets"

    good.error = OSError("offline too")
    clock[0] += 100
    stale = provider.get()
    assert stale.source == "github-datasets" and stale.fetched_at == 1000.0


def test_provider_uses_bundled_snapshot_when_offline_without_cache(tmp_path: Path):
    snapshot = tmp_path / "snapshot.json"
    write_snapshot(ConstituentList(tuple(_members(3)), "wikipedia", 5.0), snapshot)
    provider = ConstituentsProvider([FakeSource("wikipedia", error=OSError("offline"))], tmp_path / "c.json",
                                    snapshot_file=snapshot)
    result = provider.get()
    assert result.source == "bundled" and len(result.items) == 3


def test_provider_raises_when_nothing_is_available(tmp_path: Path):
    provider = ConstituentsProvider([FakeSource("wikipedia", error=OSError("offline"))], tmp_path / "c.json",
                                    snapshot_file=tmp_path / "none.json")
    with pytest.raises(RuntimeError):
        provider.get()


def test_bundled_snapshot_is_shipped_and_complete():
    from snp_monitor.constituents import SNAPSHOT_FILE, _read

    snapshot = _read(SNAPSHOT_FILE)
    assert snapshot is not None and len(snapshot.items) >= MIN_CONSTITUENTS
