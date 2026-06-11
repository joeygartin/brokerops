from brokerops_followupboss.ratelimit import TokenBucket


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_burst_within_capacity_never_sleeps() -> None:
    fake = FakeTime()
    bucket = TokenBucket(capacity=3, refill_per_second=1.0, clock=fake.clock, sleep=fake.sleep)
    for _ in range(3):
        await bucket.acquire()
    assert fake.sleeps == []


async def test_exhausted_bucket_waits_for_refill() -> None:
    fake = FakeTime()
    bucket = TokenBucket(capacity=2, refill_per_second=2.0, clock=fake.clock, sleep=fake.sleep)
    for _ in range(4):
        await bucket.acquire()
    # two burst tokens, then each extra token costs 1/refill = 0.5s of waiting
    assert fake.sleeps == [0.5, 0.5]


async def test_tokens_refill_with_elapsed_time() -> None:
    fake = FakeTime()
    bucket = TokenBucket(capacity=1, refill_per_second=1.0, clock=fake.clock, sleep=fake.sleep)
    await bucket.acquire()
    fake.now += 10.0  # plenty of idle time, but capacity caps the refill
    await bucket.acquire()
    await bucket.acquire()
    assert fake.sleeps == [1.0]
