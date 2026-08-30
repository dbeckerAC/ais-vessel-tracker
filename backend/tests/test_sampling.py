from datetime import datetime, timedelta, timezone

from app.domain.sampling import HistorySampler


def test_history_sampling_is_independent_per_vessel():
    sampler = HistorySampler(10)
    start = datetime(2026, 8, 26, tzinfo=timezone.utc)

    assert sampler.should_store("111111111", start)
    assert sampler.should_store("222222222", start + timedelta(seconds=2))
    assert not sampler.should_store("111111111", start + timedelta(seconds=9))
    assert sampler.should_store("111111111", start + timedelta(seconds=10))


def test_history_sampler_can_resume_from_database_watermark():
    sampler = HistorySampler(10)
    start = datetime(2026, 8, 26, tzinfo=timezone.utc)
    sampler.seed({"111111111": start})

    assert not sampler.should_store("111111111", start + timedelta(seconds=5))
    assert sampler.should_store("111111111", start + timedelta(seconds=11))

