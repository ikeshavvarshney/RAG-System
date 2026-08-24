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
