import threading
import pytest
from app.core.key_rotation import KeyRotator

def test_round_robin_wraps_around():
    r=KeyRotator("k1,k2,k3")
    seq=[r.next() for _ in range(4)]
    assert seq ==["k1","k2","k3","k1"]


def test_messy_input_parses_clean():
    r=KeyRotator("a,b ,,c")
    assert [r.next() for _ in range(3)]==["a","b","c"]


def test_empty_pool_raises():
    r=KeyRotator("")
    with pytest.raises(RuntimeError):
        r.next()

def test_concurrent_access_no_duplicates_or_errore():
    r=KeyRotator("k1,k2,k3,k4,k5")
    results=[]
    lock=threading.Lock()

    def worker():
        key=r.next()
        with lock:
            results.append(key)

    threads =[threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) ==20 
    assert all(k in {"k1","k2","k3","k4","k5"} for k in results)


def test_blocked_key_is_skipped_for_its_scope_only():
    r = KeyRotator("k1,k2,k3")
    r.block("k2", "model-a", 60)

    assert [r.next("model-a") for _ in range(4)] == ["k1", "k3", "k1", "k3"]
    assert "k2" in {r.next("model-b") for _ in range(3)}


def test_block_expires(monkeypatch):
    from app.core import key_rotation

    now = [100.0]
    monkeypatch.setattr(key_rotation.time, "monotonic", lambda: now[0])
    r = KeyRotator("k1,k2")
    r.block("k1", "m", 60)

    assert {r.next("m") for _ in range(4)} == {"k2"}
    now[0] += 61
    assert {r.next("m") for _ in range(4)} == {"k1", "k2"}


def test_all_keys_blocked_fails_fast():
    from app.core.key_rotation import AllKeysBlocked

    r = KeyRotator("k1,k2")
    r.block("k1", "m", 60)
    r.block("k2", "m", 60)

    with pytest.raises(AllKeysBlocked):
        r.next("m")


class _Limited(Exception):
    pass


class _Exhausted(_Limited):
    pass


def _rotate(operation, rotator, *, retries=2, backoff_base=1.0, scope="s"):
    import logging

    from app.core.key_rotation import call_with_key_rotation

    return call_with_key_rotation(
        operation,
        rotator,
        scope=scope,
        retries=retries,
        backoff_base=backoff_base,
        is_rate_limited=lambda exc: isinstance(exc, _Limited),
        is_quota_exhausted=lambda exc: isinstance(exc, _Exhausted),
        exhausted_block_seconds=60,
        label="Svc",
        log=logging.getLogger("test.rotation"),
    )


def test_call_rotates_to_the_next_key_on_a_rate_limit(monkeypatch):
    from app.core import key_rotation

    slept = []
    monkeypatch.setattr(key_rotation.time, "sleep", slept.append)
    used = []

    def operation(key):
        used.append(key)
        if key == "k1":
            raise _Limited("429")
        return "ok"

    assert _rotate(operation, KeyRotator("k1,k2")) == "ok"
    assert used == ["k1", "k2"]
    assert slept == [1.0]


def test_call_does_not_retry_other_errors(monkeypatch):
    from app.core import key_rotation

    monkeypatch.setattr(key_rotation.time, "sleep", lambda s: pytest.fail("must not back off"))
    used = []

    def operation(key):
        used.append(key)
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        _rotate(operation, KeyRotator("k1,k2"))
    assert used == ["k1"]


def test_call_reraises_the_last_rate_limit_after_backing_off_exponentially(monkeypatch):
    from app.core import key_rotation

    slept = []
    monkeypatch.setattr(key_rotation.time, "sleep", slept.append)

    def operation(key):
        raise _Limited(f"429 from {key}")

    with pytest.raises(_Limited):
        _rotate(operation, KeyRotator("k1,k2"), retries=2)
    assert slept == [1.0, 2.0]


def test_exhausted_key_is_blocked_without_spending_a_retry(monkeypatch):
    from app.core import key_rotation

    monkeypatch.setattr(key_rotation.time, "sleep", lambda s: pytest.fail("must not back off"))
    rotator = KeyRotator("dead,live")
    used = []

    def operation(key):
        used.append(key)
        if key == "dead":
            raise _Exhausted("quota gone")
        return "ok"

    assert _rotate(operation, rotator, retries=0) == "ok"
    assert used == ["dead", "live"]
    assert {rotator.next("s") for _ in range(3)} == {"live"}
    assert "dead" in {rotator.next("other-scope") for _ in range(3)}


def test_call_logs_the_service_label(caplog):
    import logging

    def operation(key):
        if key == "k1":
            raise _Limited("429")
        return "ok"

    with caplog.at_level(logging.WARNING, logger="test.rotation"):
        _rotate(operation, KeyRotator("k1,k2"), backoff_base=0)

    assert "Svc rate-limited" in caplog.text
