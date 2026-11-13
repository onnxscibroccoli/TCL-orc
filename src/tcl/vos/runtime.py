"""Civitas-99 vOS: deterministic, local-only gameplay and virtual resource market.

No brokerage, payment, remote authentication, privileged host access, or
production consensus integration. The game currency has no cash-out operation.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any, Callable

from .fixedpoint import Q, Motion, FixedPointError, ratio

RESOURCES = ("energy", "water", "alloy")
WORK = {
    "solar": ("energy", 5),
    "waterworks": ("water", 5),
    "recycling": ("alloy", 5),
}
PROJECTS = {
    "solar-microgrid": ("energy", 30),
    "water-cooperative": ("water", 30),
    "circular-factory": ("alloy", 30),
}
CAPABILITIES = frozenset({"world.read", "world.work", "market.trade", "civic.contribute",
                          "world.advance", "world.navigate"})
ACTOR_RE = re.compile(r"^[a-z][a-z0-9_-]{1,23}$")
MAX_OPEN_ORDERS = 200
MAX_EVENTS = 50_000
VERSION = "tcl-vos-civitas99-v1"


class GameError(ValueError):
    """Expected client-side rejection: no state is committed."""


def positive_int(value: Any, label: str, upper: int) -> int:
    if type(value) is not int or not 1 <= value <= upper:
        raise GameError(f"{label} must be an integer between 1 and {upper}")
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _owner(raw: Any) -> str:
    if not isinstance(raw, str) or not ACTOR_RE.fullmatch(raw):
        raise GameError("player name must be 2-24 lowercase ASCII letters, digits, _ or -")
    return raw


@dataclass(frozen=True)
class GameManifest:
    game_id: str
    title: str
    capabilities: frozenset[str]
    runtime: str = "builtin"
    api_version: int = 1

    def validate(self) -> None:
        if self.game_id != "civitas99" or self.runtime != "builtin" or self.api_version != 1:
            raise GameError("unsupported game manifest")
        if not self.capabilities <= CAPABILITIES:
            raise GameError("unknown capability requested")


@dataclass
class Player:
    credits: int = 1200
    inventory: dict[str, int] = field(default_factory=lambda: {r: 8 for r in RESOURCES})
    reputation: int = 0
    last_work_tick: int = -1
    courier_position: int = 0  # Q32.32, cells along the city delivery lane
    courier_velocity: int = 0  # Q32.32, cells per simulated second
    courier_deliveries: int = 0


@dataclass
class Order:
    order_id: int
    owner: str
    side: str
    resource: str
    price: int
    remaining: int
    sequence: int

    def as_dict(self) -> dict[str, Any]:
        return vars(self).copy()


@dataclass
class Civitas99:
    """One process-local deterministic market world; an in-memory game, not the TCL ledger."""

    seed: int = 99
    tick: int = 0
    players: dict[str, Player] = field(default_factory=dict)
    orders: dict[int, Order] = field(default_factory=dict)
    trades: list[dict[str, Any]] = field(default_factory=list)
    next_order: int = 1
    next_trade: int = 1
    issued_credits: int = 0
    issued_resources: dict[str, int] = field(default_factory=lambda: {r: 0 for r in RESOURCES})
    projects: dict[str, int] = field(default_factory=lambda: {k: 0 for k in PROJECTS})

    def _player(self, actor: str) -> Player:
        try:
            return self.players[actor]
        except KeyError as exc:
            raise GameError("join the city before taking this action") from exc

    def _join(self, actor: str) -> dict[str, Any]:
        if actor in self.players:
            raise GameError("player already joined")
        if len(self.players) >= 200:
            raise GameError("city is full (local prototype limit)")
        self.players[actor] = Player()
        self.issued_credits += 1200
        for resource in RESOURCES:
            self.issued_resources[resource] += 8
        return {"joined": actor, "starter_credits": 1200, "starter_resources": 8}

    def _work(self, actor: str, role: Any) -> dict[str, Any]:
        p = self._player(actor)
        if role not in WORK:
            raise GameError("unknown work role")
        if p.last_work_tick == self.tick:
            raise GameError("one work shift per player per city turn")
        resource, units = WORK[role]
        p.inventory[resource] += units
        p.reputation += 1
        p.last_work_tick = self.tick
        self.issued_resources[resource] += units
        return {"work": role, "resource": resource, "units": units,
                "reputation": p.reputation}

    def _navigate(self, actor: str, throttle: Any, steps: Any) -> dict[str, Any]:
        """Play a bounded deterministic courier mini-game (no ledger access).

        Semi-implicit Euler integration: v[t+1]=v[t]+a*dt,
        x[t+1]=x[t]+v[t+1]*dt, all as Q32.32 i64 integers.
        The game awards *reputation only*, never CIV balance or tokens.
        """
        p = self._player(actor)
        if type(throttle) is not int or not -2 <= throttle <= 2:
            raise GameError("courier throttle must be integer -2 through 2")
        count = positive_int(steps, "steps", 6)
        acceleration = ratio(throttle, 4)  # quarter cell/s^2 per input step
        state = Motion(p.courier_position, p.courier_velocity)
        delivered = 0
        try:
            for _ in range(count):
                state = state.advance(acceleration)
                if state.position < 0:
                    state = Motion(0, 0)
                elif state.position >= 8 * Q:
                    delivered += 1
                    state = Motion(0, 0)
                elif state.velocity > 3 * Q:
                    state = Motion(state.position, 3 * Q)
                elif state.velocity < -(3 * Q):
                    state = Motion(state.position, -(3 * Q))
        except FixedPointError as exc:
            raise GameError("courier simulation exceeded fixed-point bounds") from exc
        p.courier_position = state.position
        p.courier_velocity = state.velocity
        p.courier_deliveries += delivered
        p.reputation += delivered * 5
        return {"courier_position_q32": state.position,
                "courier_velocity_q32": state.velocity,
                "deliveries_completed": delivered,
                "total_deliveries": p.courier_deliveries,
                "reputation": p.reputation}

    def _advance(self, turns: Any) -> dict[str, Any]:
        steps = positive_int(turns, "turns", 10)
        self.tick += steps
        return {"turn": self.tick, "advanced": steps}

    def _contribute(self, actor: str, project: Any, quantity: Any) -> dict[str, Any]:
        p = self._player(actor)
        if project not in PROJECTS:
            raise GameError("unknown community project")
        units = positive_int(quantity, "quantity", 100)
        resource, target = PROJECTS[project]
        remaining = target - self.projects[project]
        if remaining == 0:
            raise GameError("project is complete")
        if units > remaining:
            raise GameError("contribution exceeds remaining project requirement")
        if p.inventory[resource] < units:
            raise GameError("not enough available game resources")
        p.inventory[resource] -= units
        self.projects[project] += units
        p.reputation += units
        complete = self.projects[project] == target
        if complete:
            p.reputation += 10
        return {"project": project, "donated": units, "resource": resource,
                "complete": complete, "reputation": p.reputation}

    def _cancel(self, actor: str, number: Any) -> dict[str, Any]:
        self._player(actor)
        number = positive_int(number, "order_id", 1_000_000_000)
        order = self.orders.get(number)
        if order is None or order.owner != actor:
            raise GameError("order not found or not owned by player")
        if order.side == "buy":
            self.players[actor].credits += order.price * order.remaining
        else:
            self.players[actor].inventory[order.resource] += order.remaining
        del self.orders[number]
        return {"cancelled": number}

    def _place(self, actor: str, side: Any, resource: Any,
               price: Any, quantity: Any) -> dict[str, Any]:
        p = self._player(actor)
        if side not in ("buy", "sell") or resource not in RESOURCES:
            raise GameError("side must be buy/sell and resource must be energy/water/alloy")
        price = positive_int(price, "price", 10_000)
        quantity = positive_int(quantity, "quantity", 100)
        if len(self.orders) >= MAX_OPEN_ORDERS:
            raise GameError("order book capacity reached")
        if sum(o.owner == actor for o in self.orders.values()) >= 40:
            raise GameError("player order quota reached")
        # A self-crossing order is rejected before any assets are reserved.
        for other in self.orders.values():
            crosses = other.price <= price if side == "buy" else other.price >= price
            if (other.owner == actor and other.resource == resource
                    and other.side != side and crosses):
                raise GameError("self-matching is not allowed")
        if side == "buy":
            cost = price * quantity
            if p.credits < cost:
                raise GameError("insufficient available game credits")
            p.credits -= cost
        else:
            if p.inventory[resource] < quantity:
                raise GameError("insufficient available game resources")
            p.inventory[resource] -= quantity
        n = self.next_order
        self.next_order += 1
        incoming = Order(n, actor, side, resource, price, quantity, n)
        matched = []
        while incoming.remaining:
            opponents = [o for o in self.orders.values()
                         if o.resource == resource and o.side != side and o.owner != actor
                         and (o.price <= price if side == "buy" else o.price >= price)]
            if not opponents:
                break
            if side == "buy":
                maker = min(opponents, key=lambda o: (o.price, o.sequence))
                buyer, seller = actor, maker.owner
            else:
                maker = max(opponents, key=lambda o: (o.price, -o.sequence))
                buyer, seller = maker.owner, actor
            units = min(incoming.remaining, maker.remaining)
            execution = maker.price  # price-time priority; maker price is authoritative
            self.players[buyer].inventory[resource] += units
            self.players[seller].credits += execution * units
            # Buyer escrow was reserved at its own bid. Refund improvement.
            bid = price if side == "buy" else maker.price
            self.players[buyer].credits += (bid - execution) * units
            incoming.remaining -= units
            maker.remaining -= units
            if maker.remaining == 0:
                del self.orders[maker.order_id]
            trade = {"trade_id": self.next_trade, "buyer": buyer, "seller": seller,
                     "resource": resource, "price": execution, "quantity": units,
                     "maker_order": maker.order_id, "taker_order": incoming.order_id,
                     "turn": self.tick}
            self.next_trade += 1
            self.trades.append(trade)
            matched.append(trade)
        if incoming.remaining:
            self.orders[n] = incoming
        return {"order_id": n, "matched": matched,
                "remaining": incoming.remaining, "filled": quantity - incoming.remaining}

    def act(self, action: str, actor: str, params: dict[str, Any]) -> dict[str, Any]:
        if action == "join":
            if params:
                raise GameError("join accepts no parameters")
            return self._join(actor)
        if action == "navigate":
            if set(params) != {"throttle", "steps"}:
                raise GameError("navigate requires throttle and steps")
            return self._navigate(actor, params["throttle"], params["steps"])
        if action == "advance":
            if set(params) != {"turns"}:
                raise GameError("advance requires turns")
            return self._advance(params["turns"])
        if action == "work":
            if set(params) != {"role"}:
                raise GameError("work requires role")
            return self._work(actor, params["role"])
        if action == "contribute":
            if set(params) != {"project", "quantity"}:
                raise GameError("contribute requires project and quantity")
            return self._contribute(actor, params["project"], params["quantity"])
        if action == "order":
            if set(params) != {"side", "resource", "price", "quantity"}:
                raise GameError("order requires side, resource, price, quantity")
            return self._place(actor, params["side"], params["resource"],
                               params["price"], params["quantity"])
        if action == "cancel":
            if set(params) != {"order_id"}:
                raise GameError("cancel requires order_id")
            return self._cancel(actor, params["order_id"])
        raise GameError("unknown action")

    def check_conservation(self) -> None:
        """Game currency cannot escape into or out of the independent TCL ledger."""
        available = sum(p.credits for p in self.players.values())
        locked = sum(o.remaining * o.price for o in self.orders.values() if o.side == "buy")
        if available < 0 or locked < 0 or available + locked != self.issued_credits:
            raise AssertionError("game credit conservation violated")
        for resource in RESOURCES:
            free = sum(p.inventory[resource] for p in self.players.values())
            held = sum(o.remaining for o in self.orders.values()
                       if o.side == "sell" and o.resource == resource)
            civic = sum(self.projects[k] for k in PROJECTS if PROJECTS[k][0] == resource)
            if (free < 0 or held < 0 or free + held + civic != self.issued_resources[resource]
                    or any(p.inventory[resource] < 0 for p in self.players.values())):
                raise AssertionError(f"{resource} conservation violated")

    def snapshot(self, actor: str = "") -> dict[str, Any]:
        projects = [{"id": k, "resource": value[0], "goal": value[1],
                     "progress": self.projects[k], "complete": self.projects[k] == value[1]}
                    for k, value in PROJECTS.items()]
        books = {}
        for resource in RESOURCES:
            buys = sorted((o for o in self.orders.values() if o.side == "buy" and
                           o.resource == resource), key=lambda o: (-o.price, o.sequence))
            sells = sorted((o for o in self.orders.values() if o.side == "sell" and
                            o.resource == resource), key=lambda o: (o.price, o.sequence))
            books[resource] = {"bids": [o.as_dict() for o in buys[:20]],
                               "asks": [o.as_dict() for o in sells[:20]]}
        districts = []
        for y in range(5):
            for x in range(9):
                h = sha256(f"{self.seed}:{x}:{y}".encode()).digest()[0]
                districts.append({"x": x, "y": y,
                                  "type": ("park", "homes", "industry", "plaza", "water")[h % 5]})
        p = self.players.get(actor)
        return {"game": "civitas99", "version": VERSION, "turn": self.tick,
                "player": None if p is None else {"id": actor, "credits": p.credits,
                    "inventory": dict(p.inventory), "reputation": p.reputation,
                    "worked_this_turn": p.last_work_tick == self.tick,
                    "courier": {"position_q32": p.courier_position,
                                "velocity_q32": p.courier_velocity,
                                "cell": p.courier_position // Q,
                                "deliveries": p.courier_deliveries}},
                "players": len(self.players), "projects": projects, "books": books,
                "trades": list(reversed(self.trades[-24:])), "districts": districts,
                "open_orders": len(self.orders), "virtual_currency": "CIV (non-redeemable)"}


@dataclass
class GameOS:
    """A capability-scoped game host and deterministic append-only action journal.

    Plugins cannot execute arbitrary Python, I/O, shell commands, or ledger calls
    through this API. A separate audited WASM/JVM execution adapter is required
    before accepting third-party bytecode. All operations stay local to the demo.
    """

    seed: int = 99
    game: Civitas99 = field(init=False)
    journal: list[dict[str, Any]] = field(default_factory=list)
    head: str = "0" * 64
    lock: Any = field(default_factory=RLock, repr=False)
    subscribers: list[Callable[[dict[str, Any]], None]] = field(default_factory=list, repr=False)
    observer_failures: int = 0
    manifest: GameManifest = field(default_factory=lambda: GameManifest(
        "civitas99", "Civitas-99 Civic Market", CAPABILITIES))

    def __post_init__(self) -> None:
        if type(self.seed) is not int or not 0 <= self.seed <= 2**31 - 1:
            raise GameError("invalid world seed")
        self.manifest.validate()
        self.game = Civitas99(seed=self.seed)

    def on_event(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register a trusted host observer; callbacks cannot change game state."""
        with self.lock:
            if not callable(callback) or len(self.subscribers) >= 16:
                raise GameError("event bus observer quota reached or invalid callback")
            self.subscribers.append(callback)

    def dispatch(self, command: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            if type(command) is not dict or set(command) != {"game", "action", "actor", "params"}:
                raise GameError("expected game, action, actor, params")
            if command["game"] != self.manifest.game_id:
                raise GameError("game not installed")
            actor = _owner(command["actor"])
            action, params = command["action"], command["params"]
            if not isinstance(action, str) or type(params) is not dict:
                raise GameError("invalid command schema")
            permissions = {"join": "world.read", "work": "world.work", "advance": "world.advance",
                           "contribute": "civic.contribute", "order": "market.trade",
                           "cancel": "market.trade", "navigate": "world.navigate"}
            if action not in permissions or permissions[action] not in self.manifest.capabilities:
                raise GameError("action not permitted by the installed game manifest")
            if len(self.journal) >= MAX_EVENTS:
                raise GameError("local event journal is full")
            # Commit atomically only after gameplay and conservation checks pass.
            draft = deepcopy(self.game)
            result = draft.act(action, actor, params)
            draft.check_conservation()
            cmd = {"game": "civitas99", "action": action, "actor": actor,
                   "params": deepcopy(params)}
            event = {"seq": len(self.journal) + 1, "prev": self.head, "command": cmd,
                     "result": result}
            digest = sha256(b"TCL-VOS-EVENT-V1\x00" + canonical(event)).hexdigest()
            event["hash"] = digest
            self.game = draft
            self.journal.append(event)
            self.head = digest
            for observer in tuple(self.subscribers):
                try:
                    observer(deepcopy(event))
                except Exception:
                    # Observer failure must not undo an already-validated game
                    # transition or be able to rewrite the canonical journal.
                    self.observer_failures += 1
            return {"ok": True, "event": event["seq"], "result": result,
                    "journal_head": self.head}

    def snapshot(self, actor: str = "") -> dict[str, Any]:
        with self.lock:
            return {**self.game.snapshot(actor), "os": {"version": VERSION,
                "installed_games": [self.manifest.game_id],
                "capabilities": sorted(self.manifest.capabilities),
                "events": len(self.journal), "head": self.head,
                "observer_failures": self.observer_failures,
                "session": "local-dev-only", "payment_rails": False,
                "external_ledger_connected": False}}

    def export(self) -> dict[str, Any]:
        with self.lock:
            return {"format": VERSION, "seed": self.seed, "head": self.head,
                    "journal": deepcopy(self.journal)}

    @classmethod
    def restore(cls, data: Any) -> GameOS:
        if not isinstance(data, dict) or set(data) != {"format", "seed", "head", "journal"}:
            raise GameError("invalid saved session")
        if data["format"] != VERSION or type(data["journal"]) is not list:
            raise GameError("unsupported saved session")
        if len(data["journal"]) > MAX_EVENTS:
            raise GameError("oversized saved journal")
        obj = cls(seed=data["seed"])
        for item in data["journal"]:
            if not isinstance(item, dict):
                raise GameError("invalid saved journal event")
            outcome = obj.dispatch(item.get("command"))
            if item != obj.journal[-1] or outcome["journal_head"] != item["hash"]:
                raise GameError("event replay mismatch; saved session rejected")
        if data["head"] != obj.head:
            raise GameError("journal hash mismatch")
        return obj

