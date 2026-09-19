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
