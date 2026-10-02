import asyncio

from bot.scheduler import run_checks
from monitors import MONITORS
from utils.config import Product


def _run(products, state, monitor_results, fail_alert=False):
    sent = []
    calls = iter(monitor_results)

    async def fake_monitor(product):
        r = next(calls)
        if isinstance(r, Exception):
            raise r
        return r

    async def send_alert(p):
        if fail_alert:
            raise RuntimeError("discord down")
        sent.append(p)

    MONITORS["fake"] = fake_monitor
    try:
        asyncio.run(run_checks(products, state, send_alert))
    finally:
        del MONITORS["fake"]
    return sent


P = [Product(id="p1", name="ETB", retailer="fake", url="https://x")]


def test_alerts_on_first_in_stock():
    state = {}
    sent = _run(P, state, [{"in_stock": True, "price": 49.99}])
    assert len(sent) == 1 and sent[0]["price"] == 49.99
    assert state["p1"]["in_stock"] is True


def test_no_duplicate_alert_while_still_in_stock():
    state = {"p1": {"in_stock": True}}
    assert _run(P, state, [{"in_stock": True}]) == []


def test_realerts_after_going_out_and_back():
    state = {}
    _run(P, state, [{"in_stock": True}])
    _run(P, state, [{"in_stock": False}])
    assert len(_run(P, state, [{"in_stock": True}])) == 1


def test_failed_check_does_not_change_state():
    state = {"p1": {"in_stock": True, "last_checked": 1}}
    assert _run(P, state, [TimeoutError()]) == []
    assert state["p1"] == {"in_stock": True, "last_checked": 1}


def test_failed_alert_retries_next_pass():
    state = {}
    _run(P, state, [{"in_stock": True}], fail_alert=True)
    assert "p1" not in state
    assert len(_run(P, state, [{"in_stock": True}])) == 1


def test_unknown_retailer_is_skipped():
    state = {}
    products = [Product(id="x", name="x", retailer="nope", url="u")]
    asyncio.run(run_checks(products, state, lambda p: None))
    assert state == {}


def test_old_state_format_is_upgraded(tmp_path):
    from utils.state import load_state
    f = tmp_path / "state.json"
    f.write_text('{"demo_pikachu": true}')
    assert load_state(f) == {"demo_pikachu": {"in_stock": True}}
