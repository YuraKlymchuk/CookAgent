from agent.rate_limit import RateLimiter


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(per_client=2, global_limit=100, clock=None):
    return RateLimiter(per_client=per_client, per_client_window_s=3600, global_limit=global_limit,
                       global_window_s=86400, clock=clock or Clock())


def test_allows_up_to_the_limit_then_blocks():
    limiter = make(per_client=2)
    assert limiter.acquire("a").allowed
    assert limiter.acquire("a").allowed
    blocked = limiter.acquire("a")
    assert not blocked.allowed
    assert blocked.scope == "client"
    assert 0 < blocked.retry_after_s <= 3600


def test_clients_are_counted_separately():
    limiter = make(per_client=1)
    assert limiter.acquire("a").allowed
    assert limiter.acquire("b").allowed
    assert not limiter.acquire("a").allowed


def test_window_slides_and_frees_the_client():
    clock = Clock()
    limiter = make(per_client=1, clock=clock)
    assert limiter.acquire("a").allowed
    assert not limiter.acquire("a").allowed
    clock.now += 3601
    assert limiter.acquire("a").allowed


def test_global_limit_protects_the_shared_key():
    limiter = make(per_client=10, global_limit=2)
    assert limiter.acquire("a").allowed
    assert limiter.acquire("b").allowed
    blocked = limiter.acquire("c")
    assert not blocked.allowed
    assert blocked.scope == "global"


def test_denied_requests_are_not_counted():
    clock = Clock()
    limiter = make(per_client=1, clock=clock)
    limiter.acquire("a")
    for _ in range(5):
        limiter.acquire("a")
    clock.now += 3601
    assert limiter.acquire("a").allowed
