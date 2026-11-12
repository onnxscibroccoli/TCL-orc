"""vOS and Civitas-99 acceptance tests: no real funds or blockchain access."""
from __future__ import annotations
from copy import deepcopy
from http.client import HTTPConnection
import json
from threading import Thread
import random

import pytest

from tcl.vos import GameError, GameManifest, GameOS
from tcl.vos.runtime import RESOURCES
from tcl.vos.server import SessionStore, create_server


def cmd(actor, action, **params):
    return {"game": "civitas99", "actor": actor, "action": action, "params": params}


def two():
    os = GameOS()
    os.dispatch(cmd("alice", "join"))
    os.dispatch(cmd("bob", "join"))
    return os


def test_playable_work_shift_and_public_project():
    os = two()
    assert os.snapshot("alice")["player"]["credits"] == 1200
    assert os.snapshot("alice")["os"]["external_ledger_connected"] is False
    x = os.dispatch(cmd("alice", "work", role="solar"))
    assert x["result"]["units"] == 5
    with pytest.raises(GameError, match="one work shift"):
        os.dispatch(cmd("alice", "work", role="recycling"))
    os.dispatch(cmd("alice", "advance", turns=1))
    assert os.dispatch(cmd("alice", "work", role="recycling"))["result"]["units"] == 5
    event_count = len(os.journal)
    with pytest.raises(GameError, match="not enough"):
        os.dispatch(cmd("alice", "contribute", project="solar-microgrid", quantity=20))
    assert len(os.journal) == event_count
    out = os.dispatch(cmd("alice", "contribute", project="solar-microgrid", quantity=10))
    assert out["result"]["reputation"] == 12  # 2 shifts + 10 contributions
    os.game.check_conservation()


def test_maker_price_trade_improvement_and_locked_balances():
    os = two()
    initial_credit = os.game.issued_credits
    resting = os.dispatch(cmd("alice", "order", side="sell", resource="energy", price=12, quantity=5))
    number = resting["result"]["order_id"]
    bid = os.dispatch(cmd("bob", "order", side="buy", resource="energy", price=20, quantity=3))
    assert bid["result"]["filled"] == 3
    assert bid["result"]["remaining"] == 0
    assert os.game.trades[0]["price"] == 12  # resting ask, not taker bid
    assert os.game.players["bob"].credits == 1200 - 36  # $8/lot improved-price refund
    assert os.game.players["bob"].inventory["energy"] == 11
    assert os.game.players["alice"].credits == 1200 + 36
    assert os.game.orders[number].remaining == 2
    assert os.game.issued_credits == initial_credit
    os.game.check_conservation()
    os.dispatch(cmd("alice", "cancel", order_id=number))
    assert os.game.players["alice"].inventory["energy"] == 5
    assert not os.game.orders
    os.game.check_conservation()


def test_resting_buy_sell_market_and_cancel_refund():
    os = two()
    buy = os.dispatch(cmd("bob", "order", side="buy", resource="alloy", price=24, quantity=6))
    assert os.game.players["bob"].credits == 1200 - 144
    sell = os.dispatch(cmd("alice", "order", side="sell", resource="alloy", price=20, quantity=3))
    assert sell["result"]["matched"][0]["price"] == 24
    assert os.game.players["alice"].credits == 1200 + 72
    assert os.game.players["bob"].inventory["alloy"] == 11
    os.dispatch(cmd("bob", "cancel", order_id=buy["result"]["order_id"]))
    assert os.game.players["bob"].credits == 1200 - 72
    assert not os.game.orders
    os.game.check_conservation()


def test_fifo_price_priority_partial_fills():
    os = two()
    os.dispatch(cmd("carol", "join"))
    os.dispatch(cmd("alice", "order", side="sell", resource="water", price=15, quantity=5))
    os.dispatch(cmd("carol", "order", side="sell", resource="water", price=15, quantity=5))
    out = os.dispatch(cmd("bob", "order", side="buy", resource="water", price=15, quantity=7))
    assert [(t["seller"],t["quantity"]) for t in out["result"]["matched"]] == [
        ("alice", 5), ("carol", 2)]
    assert len(os.game.orders) == 1
    assert next(iter(os.game.orders.values())).owner == "carol"
    os.game.check_conservation()


def test_no_self_matching_or_invalid_actions_and_no_ghost_reservations():
    os = two()
    os.dispatch(cmd("alice", "order", side="sell", resource="energy", price=10, quantity=3))
    before = os.export()
    rejected = [
        cmd("alice", "order", side="buy", resource="energy", price=10, quantity=1),
        cmd("bob", "order", side="buy", resource="energy", price=0, quantity=2),
        cmd("bob", "order", side="buy", resource="energy", price=True, quantity=2),
        cmd("bob", "order", side="buy", resource="water", price=30, quantity=100),
        cmd("bob", "cancel", order_id=1),
        cmd("bob", "work", role="illegal"),
        cmd("dave", "work", role="solar"),
        cmd("bob", "advance", turns=10000),
        {**cmd("bob", "work", role="solar"), "untrusted": "extra"},
        cmd("bob", "order", side="buy", resource="alloy", price=10, quantity=1, extra=1),
    ]
    for request in rejected:
        with pytest.raises(GameError):
            os.dispatch(request)
        assert os.export() == before


def test_game_manifest_capability_isolation():
    with pytest.raises(GameError, match="unknown capability"):
        GameManifest("civitas99", "x", frozenset({"network.raw"})).validate()
    os = GameOS(manifest=GameManifest("civitas99", "safe", frozenset({"world.read"})))
    os.dispatch(cmd("alice", "join"))
    with pytest.raises(GameError, match="not permitted"):
        os.dispatch(cmd("alice", "order", side="sell",resource="water",price=1,quantity=1))
    assert os.snapshot("alice")["os"]["capabilities"] == ["world.read"]


def test_event_chain_replay_and_tamper_detection():
    os = two()
    os.dispatch(cmd("alice", "work", role="solar"))
    os.dispatch(cmd("alice", "order", side="sell", resource="water",price=20,quantity=2))
    saved = os.export()
    clone = GameOS.restore(saved)
    assert clone.export() == saved
    assert clone.snapshot("alice") == os.snapshot("alice")
    forgery = deepcopy(saved)
    forgery["journal"][1]["command"]["actor"] = "mallory"
    with pytest.raises((GameError, ValueError)):
        GameOS.restore(forgery)
    wrong_hash=deepcopy(saved)
    wrong_hash["head"] = "f"*64
    with pytest.raises(GameError):
        GameOS.restore(wrong_hash)
    with pytest.raises(GameError):
        GameOS.restore({**saved,"format":"unknown"})


def test_reproducible_world_map():
    a,b = GameOS(seed=42),GameOS(seed=42)
    assert a.snapshot()["districts"] == b.snapshot()["districts"]
    assert a.snapshot()["districts"] != GameOS(seed=43).snapshot()["districts"]


def test_randomized_market_conservation_and_replay():
    rng=random.Random(99)
    os=two()
    names=("alice","bob")
    for _ in range(300):
        player = rng.choice(names)
        action = rng.choice(("work","trade","cancel","tick","contribute"))
        if action=="work": request=cmd(player,"work",role=rng.choice(tuple(("solar","waterworks","recycling"))))
        elif action=="trade": request=cmd(player,"order",side=rng.choice(("buy","sell")),resource=rng.choice(RESOURCES),price=rng.randint(1,40),quantity=rng.randint(1,9))
        elif action=="cancel": request=cmd(player,"cancel",order_id=rng.randint(1,max(1,os.game.next_order)))
        elif action=="tick": request=cmd(player,"advance",turns=1)
        else: request=cmd(player,"contribute",project=rng.choice(tuple(("solar-microgrid","water-cooperative","circular-factory"))),quantity=rng.randint(1,8))
        try: os.dispatch(request)
        except GameError: pass
        os.game.check_conservation()
    assert GameOS.restore(os.export()).export() == os.export()


def test_local_json_session_roundtrip_and_atomic_replay(tmp_path):
    store=SessionStore(tmp_path/"saves"/"world.json")
    store.execute(cmd("alice", "join"))
    store.execute(cmd("alice", "work", role="waterworks"))
    path=tmp_path/"saves"/"world.json"
    assert path.exists()
    reloaded=SessionStore(path)
    assert reloaded.os.snapshot("alice")==store.os.snapshot("alice")
    obj=json.loads(path.read_text())
    obj["journal"][1]["result"]["units"]=1000
    path.write_text(json.dumps(obj))
    with pytest.raises(GameError): SessionStore(path)


def test_http_loopback_accessibility_and_csrf(tmp_path):
    server=create_server(port=0,path=tmp_path/"sess.json")
    port=server.server_address[1]
    thread=Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        con=HTTPConnection("127.0.0.1",port,timeout=4)
        con.request("GET","/")
        resp=con.getresponse(); html=resp.read().decode()
        assert resp.status==200 and 'aria-live="polite"' in html and "CIVITAS-99" in html
        assert resp.getheader("Content-Security-Policy").startswith("default-src 'self'")
        con.request("GET","/api/state?actor=alice")
        resp=con.getresponse();s=json.loads(resp.read());assert resp.status==200 and not s["player"]
        b=json.dumps(cmd("alice","join"))
        con.request("POST","/api/commands",b,headers={"Content-Type":"application/json","Origin":"http://evil.invalid"})
        resp=con.getresponse();resp.read(); assert resp.status==403
        con.request("POST","/api/commands",b,headers={"Content-Type":"text/plain"})
        resp=con.getresponse();resp.read();assert resp.status==415
        con.request("POST","/api/commands",b,headers={"Content-Type":"application/json"})
        resp=con.getresponse();r=json.loads(resp.read());assert resp.status==200 and r["ok"]
        con.request("GET","/api/state?actor=alice")
        resp=con.getresponse(); s=json.loads(resp.read());assert s["player"]["credits"]==1200
        con.close()
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)
    with pytest.raises(GameError,match="loopback"):
        create_server(host="0.0.0.0",port=0)


def test_host_event_bus_isolation_and_failed_observers():
    os = GameOS()
    observed=[]
    def handler(event):
        observed.append(event)
        event["result"]["joined"] = "hacked"
    def failing(_event):
        raise RuntimeError("synthetic observer crash")
    os.on_event(handler)
    os.on_event(failing)
    response = os.dispatch(cmd("alice", "join"))
    assert response["result"]["joined"] == "alice"
    assert os.journal[-1]["result"]["joined"] == "alice"
    assert observed[0]["result"]["joined"] == "hacked"
    assert os.snapshot("alice")["os"]["observer_failures"] == 1
    assert GameOS.restore(os.export()).snapshot("alice")["player"]["id"] == "alice"

