"""Q32.32 integer-only physics, courier replay, binary integrity acceptance."""
import base64
from hashlib import sha256
from http.client import HTTPConnection
from threading import Thread

import pytest

from tcl.vos.fixedpoint import (
    ERROR_SENTINEL, MAX_VALID, MIN_VALID, Motion, Q, FixedPointError,
    add, check, div, golden_vectors, mul, ratio,
)
from tcl.vos.runtime import GameError, GameOS
from tcl.vos.server import Q32_WASM_HASH, create_server


def command(actor, action, **params):
    return {"game": "civitas99", "actor": actor, "action": action, "params": params}


def test_exact_fixed_point_arithmetic_and_round_toward_zero():
    assert ratio(1, 4) == Q // 4
    assert ratio(-1, 4) == -(Q // 4)
    assert mul(2 * Q, 3 * Q) == 6 * Q
    assert mul(-3 * Q, Q // 2) == -(3 * Q // 2)
    assert mul(-1, 1) == 0  # truncation, NOT Python floor (-1)
    assert div(Q, 2 * Q) == Q // 2
    assert add(ratio(2), ratio(-2)) == 0
    assert Motion(Q, Q).advance(Q).position == 3 * Q
    assert len(golden_vectors()) >= 100


@pytest.mark.parametrize("operand", [ERROR_SENTINEL, MAX_VALID + 1, True, 1.0, "42"])
def test_arithmetic_rejects_invalid_operands(operand):
    with pytest.raises(FixedPointError):
        check(operand)


def test_overflow_and_invalid_time_fail_closed():
    with pytest.raises(FixedPointError):
        add(MAX_VALID, 1)
    with pytest.raises(FixedPointError):
        mul(MAX_VALID, MAX_VALID)
    with pytest.raises(FixedPointError):
        div(Q, 0)
    with pytest.raises(FixedPointError):
        ratio(1, 0)
    with pytest.raises(FixedPointError):
        Motion().advance(Q, -1)
    with pytest.raises(FixedPointError):
        Motion().advance(Q, Q + 1)


def test_courier_actions_are_integer_deterministic_and_ledger_isolated():
    game = GameOS()
    game.dispatch(command("alice", "join"))
    before = game.snapshot("alice")
    assert before["player"]["credits"] == 1200
    out = game.dispatch(command("alice", "navigate", throttle=1, steps=2))
    assert out["result"]["courier_position_q32"] == 3 * Q // 4
    assert out["result"]["courier_velocity_q32"] == Q // 2
    assert game.snapshot("alice")["player"]["courier"]["cell"] == 0
    for _ in range(8):
        game.dispatch(command("alice", "navigate", throttle=2, steps=1))
    state = game.snapshot("alice")["player"]
    assert state["courier"]["deliveries"] >= 1
    assert state["reputation"] >= 5
    assert state["credits"] == before["player"]["credits"]
    assert state["inventory"] == before["player"]["inventory"]
    game.game.check_conservation()
    assert GameOS.restore(game.export()).export() == game.export()
    assert GameOS.restore(game.export()).snapshot("alice") == game.snapshot("alice")


def test_courier_rejections_have_no_event_side_effects():
    game = GameOS()
    game.dispatch(command("alice", "join"))
    initial = game.export()
    for params in (
        {"throttle": 2.0, "steps": 1},
        {"throttle": 3, "steps": 1},
        {"throttle": 1, "steps": True},
        {"throttle": 1, "steps": 0},
        {"throttle": 1},
    ):
        with pytest.raises(GameError):
            game.dispatch(command("alice", "navigate", **params))
        assert game.export() == initial


def test_local_server_serves_only_integrity_pinned_wasm():
    server = create_server(port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        con = HTTPConnection("127.0.0.1", server.server_address[1], timeout=3)
        con.request("GET", "/q32.wasm")
        response = con.getresponse()
        data = response.read()
        assert response.status == 200
        assert response.getheader("Content-Type") == "application/wasm"
        assert data[:4] == b"\x00asm"
        assert sha256(data).hexdigest() == Q32_WASM_HASH
        assert len(data) <= 16 * 1024
        con.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_bundled_base64_is_canonical_and_explicitly_bounded():
    from pathlib import Path
    b64 = Path(__file__).parents[1] / "src/tcl/vos/web/q32.wasm.b64"
    data = base64.b64decode(b64.read_bytes().strip(), validate=True)
    assert data.startswith(b"\x00asm\x01\x00\x00\x00")
    assert sha256(data).hexdigest() == Q32_WASM_HASH
    assert MIN_VALID == ERROR_SENTINEL + 1
