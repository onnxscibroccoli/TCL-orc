"""Delta-state CRDT for NON-AUTHORITATIVE vOS cosmetic/presence metadata.

Never merge orders, balances, game credits, reputation, or consensus events via
last-writer-wins; these require authenticated total-order game state transitions.
This is a deterministic application-layer replication primitive, NOT libp2p,
QUIC, WebRTC, AutoNAT, Noise or DCUtR network transport.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from typing import Any

PROTOCOL = "tcl-vos-delta-crdt-v1"
PEER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,23}$")
KEY_RE = re.compile(r"^(presence|cosmetic):[a-z0-9_-]{2,32}$")
ITEM_RE = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
MAX_CLOCK = (1 << 53) - 1
MAX_PACKET = 32768
MAX_RECORDS = 512


class SyncError(ValueError):
    """Reject untrusted/unbounded application replication data."""


def _valid_peer(value: Any) -> str:
    if type(value) is not str or not PEER_RE.fullmatch(value):
        raise SyncError("invalid peer ID")
    return value


def _valid_clock(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_CLOCK:
        raise SyncError("invalid Lamport clock")
    return value


def _valid_value(value: Any) -> str:
    if type(value) is not str or len(value) > 120 or any(ord(c) < 32 for c in value):
        raise SyncError("cosmetic metadata must be a short printable string")
    return value


def _canonical(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (ValueError, TypeError) as exc:
        raise SyncError("invalid CRDT JSON") from exc


Dot = tuple[str, int]
Record = tuple[int, str, str]


@dataclass
class DeltaReplica:
    node: str
    clock: int = 0
    register: dict[str, Record] = field(default_factory=dict)
    adds: dict[str, set[Dot]] = field(default_factory=dict)
    tombstones: set[Dot] = field(default_factory=set)

    def __post_init__(self) -> None:
        _valid_peer(self.node)
        _valid_clock(self.clock)

    def _next(self) -> int:
        if self.clock == MAX_CLOCK:
            raise SyncError("clock exhausted")
        self.clock += 1
        return self.clock

    def _packet(self, *, maps=None, joins=None, leaves=None) -> dict[str, Any]:
        return {"protocol": PROTOCOL, "sender": self.node, "clock": self.clock,
                "maps": maps or [], "adds": joins or [], "removes": leaves or []}

    def put(self, key: str, value: str) -> dict[str, Any]:
        if type(key) is not str or not KEY_RE.fullmatch(key):
            raise SyncError("only cosmetic/presence keys can replicate")
        _valid_value(value)
        record = {"key": key, "clock": self._next(), "node": self.node, "value": value}
        self.register[key] = (record["clock"], self.node, value)
        return self._packet(maps=[record])

    def join(self, item: str) -> dict[str, Any]:
        if type(item) is not str or not ITEM_RE.fullmatch(item):
            raise SyncError("invalid presence item")
        seq = self._next()
        self.adds.setdefault(item, set()).add((self.node, seq))
        return self._packet(joins=[{"item": item, "node": self.node, "clock": seq}])

    def leave(self, item: str) -> dict[str, Any]:
        if type(item) is not str or not ITEM_RE.fullmatch(item):
            raise SyncError("invalid presence item")
        observed = sorted(self.adds.get(item, set()))
        self._next()
        dots = [{"node": node, "clock": clock} for node, clock in observed]
        self.tombstones.update(observed)
        self.adds.pop(item, None)
        return self._packet(leaves=dots)

    def full_delta(self) -> dict[str, Any]:
        """Sync a new peer using the same delta schema, including tombstones."""
        return self._packet(
            maps=[{"key": key, "clock": t, "node": node, "value": value}
                  for key, (t, node, value) in sorted(self.register.items())],
            joins=[{"item": item, "node": node, "clock": tick}
                   for item in sorted(self.adds)
                   for node, tick in sorted(self.adds[item])],
            leaves=[{"node": node, "clock": tick} for node, tick in sorted(self.tombstones)])

    def merge(self, packet: dict[str, Any]) -> bool:
        """Atomic idempotent delta merge. Returns whether materialized view changed."""
        if type(packet) is not dict or set(packet) != {
            "protocol", "sender", "clock", "maps", "adds", "removes"
        } or packet["protocol"] != PROTOCOL:
            raise SyncError("invalid delta protocol envelope")
        if len(_canonical(packet)) > MAX_PACKET:
            raise SyncError("delta payload exceeds 32 KiB")
        _valid_peer(packet["sender"])
        _valid_clock(packet["clock"])
        maps, additions, removals = packet["maps"], packet["adds"], packet["removes"]
        if any(type(items) is not list for items in (maps, additions, removals)):
            raise SyncError("delta operations must be arrays")
        if sum(map(len, (maps, additions, removals))) > MAX_RECORDS:
            raise SyncError("too many operations")
        temp = deepcopy(self)
        before = temp.snapshot()
        for op in maps:
            if type(op) is not dict or set(op) != {"key", "clock", "node", "value"}:
                raise SyncError("invalid LWW record")
            key = op["key"]
            if type(key) is not str or not KEY_RE.fullmatch(key):
                raise SyncError("attempt to replicate authoritative game state")
            tick = _valid_clock(op["clock"])
            node = _valid_peer(op["node"])
            value = _valid_value(op["value"])
            record = (tick, node, value)
            current = temp.register.get(key)
            if current is None or record[:2] > current[:2]:
                temp.register[key] = record
        for op in additions:
            if type(op) is not dict or set(op) != {"item", "node", "clock"}:
                raise SyncError("invalid observed-add operation")
            item = op["item"]
            if type(item) is not str or not ITEM_RE.fullmatch(item):
                raise SyncError("invalid observed-add item")
            dot = (_valid_peer(op["node"]), _valid_clock(op["clock"]))
            if dot not in temp.tombstones:
                temp.adds.setdefault(item, set()).add(dot)
        for op in removals:
            if type(op) is not dict or set(op) != {"node", "clock"}:
                raise SyncError("invalid observed-remove operation")
            dot = (_valid_peer(op["node"]), _valid_clock(op["clock"]))
            temp.tombstones.add(dot)
        for item in list(temp.adds):
            temp.adds[item].difference_update(temp.tombstones)
            if not temp.adds[item]:
                del temp.adds[item]
        temp.clock = max(temp.clock, packet["clock"])
        if (len(temp.register) > MAX_RECORDS or
                sum(map(len, temp.adds.values())) + len(temp.tombstones) > 4096):
            raise SyncError("replica capacity exceeded")
        changed = before != temp.snapshot()
        self.clock, self.register = temp.clock, temp.register
        self.adds, self.tombstones = temp.adds, temp.tombstones
        return changed

    def snapshot(self) -> dict[str, Any]:
        return {
            "register": {k: v[2] for k, v in sorted(self.register.items())},
            "members": sorted(self.adds),
        }

    def commitment(self) -> str:
        return sha256(b"TCL-VOS-CRDT-V1\\x00" + _canonical(self.full_delta())).hexdigest()
