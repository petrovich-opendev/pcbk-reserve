"""Кэш ответов: срок по виду периода, «сейчас» вниз до 30 с, вытеснение четверти, single-flight."""
import asyncio
from datetime import datetime, timedelta

import anyio
import pytest

from pcbk_core.data.cache import SingleFlight, TTLCache, cache_key, round_now, ttl_for
from pcbk_core.data.historian import HistorianError


def test_round_now_and_ttl():
    now = datetime(2026, 10, 1, 12, 0)
    assert round_now(datetime(2026, 10, 1, 12, 0, 59)) == datetime(2026, 10, 1, 12, 0, 30)
    assert (ttl_for("tag_now", None, now), ttl_for("tag_period", now - timedelta(seconds=301), now),
            ttl_for("tag_period", now - timedelta(seconds=299), now)) == (15, 3600, 30)
    assert cache_key("tag_now", ["B", "A"], None, None) == cache_key("tag_now", ["A", "B"], None, None)


def test_ttl_cache_evicts_quarter_soonest():
    c = TTLCache(max_entries=8)
    for i in range(8):
        c.put(f"k{i}", i, ttl=10 + i, now=0.0)
    c.put("k8", 8, ttl=100, now=1.0)
    assert len(c) == 7 and c.get("k1", 1.0) is None and c.get("k2", 1.0) == 2
    assert c.get("k2", 12.5) is None


@pytest.mark.anyio
async def test_single_flight_shares_and_clears_on_error():
    sf, calls = SingleFlight(), []

    async def ok():
        calls.append(1); await anyio.sleep(0.05); return "r"     # noqa: E702
    rs = await asyncio.gather(*(sf.run("k", ok) for _ in range(10)))
    assert len(calls) == 1 and sorted(s for _, s in rs) == [False] + [True] * 9

    async def boom():
        calls.append(1); await anyio.sleep(0.01); raise HistorianError("connect", "x")     # noqa: E702
    rs = await asyncio.gather(*(sf.run("e", boom) for _ in range(3)), return_exceptions=True)
    assert all(isinstance(r, HistorianError) for r in rs) and len(calls) == 2
    assert (await sf.run("e", ok))[1] is False and len(calls) == 3
