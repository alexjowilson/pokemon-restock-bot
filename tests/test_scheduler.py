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


# ---------- backoff & health alerts ----------

from monitors.errors import Blocked, CheckFailed


def _pass(state, health, result, now, messages):
    async def fake_monitor(product):
        if isinstance(result, Exception):
            raise result
        return result

    async def send_alert(p):
        messages.append(("alert", p["id"]))

    async def send_health(text):
        messages.append(("health", text))

    MONITORS["fake"] = fake_monitor
    try:
        asyncio.run(run_checks(P, state, send_alert, health, send_health, now=now))
    finally:
        del MONITORS["fake"]


def test_health_warning_once_then_recovery():
    import bot.scheduler as sched
    state, health, msgs = {}, {}, []
    t = 0.0
    for _ in range(sched.FAIL_ALERT_THRESHOLD + 3):
        t = max(t, health.get("p1", {}).get("next_try", t)) + 1  # jump past any backoff
        _pass(state, health, CheckFailed("boom"), t, msgs)
    warnings = [m for m in msgs if m[0] == "health"]
    assert len(warnings) == 1 and "boom" in warnings[0][1]
    assert state == {}  # failures never touch stock state

    t = health["p1"]["next_try"] + 1
    _pass(state, health, {"in_stock": False}, t, msgs)
    assert "checks are working again" in msgs[-1][1]
    assert health == {}


def test_backoff_skips_product_until_next_try():
    state, health, msgs = {}, {}, []
    _pass(state, health, Blocked("429"), 1000.0, msgs)
    next_try = health["p1"]["next_try"]
    assert next_try >= 1000 + 120  # blocked -> at least 2 minutes
    # A pass before next_try doesn't even call the monitor (would succeed if it did).
    _pass(state, health, {"in_stock": True}, next_try - 1, msgs)
    assert msgs == [] and health["p1"]["failures"] == 1
    _pass(state, health, {"in_stock": True}, next_try + 1, msgs)
    assert msgs == [("alert", "p1")]


def test_plain_failures_retry_immediately_at_first():
    from bot.scheduler import backoff_seconds
    assert backoff_seconds(1, blocked=False) == 0
    assert backoff_seconds(2, blocked=False) == 0
    assert 60 <= backoff_seconds(3, blocked=False) <= 72
    assert backoff_seconds(50, blocked=True) <= 30 * 60 * 1.2
