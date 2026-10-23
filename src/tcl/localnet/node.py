"""In-process multi-validator localnet aligned with docs/13a_consensus_interfaces.md (v0).

PLACEHOLDER CONSENSUS (pending the sovereign-chain consensus design, doc 13/13a):
round-robin proposer (height mod N) and a >2/3 equal-weight quorum on the
post-state root among simulated validators. No networking, no real signatures
(Ed25519 is not in the stdlib; dev txs are accepted unsigned and that is
documented as dev-only). Every validator owns an independent SQLite ledger and
re-executes every block, so state divergence is detected, not assumed away.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..ledger import Ledger, SQLiteStorage
from ..storage.db import VAULT
from .genesis import Genesis

APP_VERSION = "localnet-0.1"
REJECT_CODES = frozenset({"BAD_SIG", "BAD_NONCE", "BAD_PROOF", "NULLIFIER_SPENT",
                          "INSUFFICIENT_BALANCE", "TIER_LIMIT", "PAUSED", "UNKNOWN_MODULE",
                          "UNKNOWN_METHOD", "WORK_BUDGET", "UNAUTHORIZED", "BAD_ARGS"})
# MOCK personhood tiers -> max single transfer (tiered attestations, doc 11)
TIER_LIMITS = {1: 10**10, 2: 10**11, 3: 10**12}


def canon(x: Any) -> bytes:
    return json.dumps(x, sort_keys=True, separators=(",", ":")).encode()


def H(x: Any) -> str:
    return hashlib.sha256(canon(x)).hexdigest()


class LocalnetError(Exception):
    pass


class _NoQuorum(Exception):
    pass


class Reject(Exception):
    def __init__(self, code: str, msg: str = "") -> None:
        assert code in REJECT_CODES, code
        super().__init__(f"{code}: {msg}" if msg else code)
        self.code = code


@dataclass
class Tx:
    chain_id: str
    module: str
    method: str
    sender: Optional[str]
    nonce: int
    args: dict
    proof: Optional[dict] = None
    sig: Optional[str] = None
    pow_stamp: int = 0

    def to_json(self) -> dict:
        return {k: getattr(self, k) for k in ("chain_id", "module", "method", "sender", "nonce",
                                              "args", "proof", "sig", "pow_stamp")}

    @property
    def tx_id(self) -> str:
        d = self.to_json()
        d.pop("sig")
        return H(d)

    @classmethod
    def from_json(cls, d: dict) -> "Tx":
        try:
            return cls(**{k: d[k] for k in ("chain_id", "module", "method", "sender", "nonce",
                                            "args")},
                       proof=d.get("proof"), sig=d.get("sig"), pow_stamp=d.get("pow_stamp", 0))
        except (KeyError, TypeError) as e:
            raise Reject("BAD_ARGS", f"malformed tx: {e}") from None


@dataclass
class Block:
    header: dict
    txs: list[dict]
    receipts: list[dict]
    last_commit: dict = field(default_factory=dict)

    @property
    def hash(self) -> str:
        return H(self.header)

    def to_json(self) -> dict:
        return {"hash": self.hash, "header": self.header, "txs": self.txs,
                "receipts": self.receipts, "last_commit": self.last_commit}


def tx_root(ids: list[str]) -> str:
    layer = [bytes.fromhex(i) for i in ids] or [b"\x00" * 32]
    while len(layer) > 1:
        if len(layer) % 2:
            layer.append(layer[-1])
        layer = [hashlib.sha256(layer[i] + layer[i + 1]).digest() for i in range(0, len(layer), 2)]
    return layer[0].hex()


class Validator:
    def __init__(self, vid: str, db: Path) -> None:
        self.id = vid
        self.db = db
        self.online = True
        self.byzantine = False
        self.height = 0
        self.open()

    def open(self) -> None:
        self.storage = SQLiteStorage(self.db)
        self.ledger = Ledger(self.storage, mode="fixed")

    def close(self) -> None:
        self.storage.close()

    def backup(self, dst: Path) -> None:
        src = self.storage._conn()
        out = sqlite3.connect(dst)
        try:
            src.backup(out)
        finally:
            out.close()

    def meta(self, key: str, default: Any) -> Any:
        row = self.storage._conn().execute(
            "SELECT v FROM localnet_kv WHERE k=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_meta(self, key: str, val: Any) -> None:
        self.storage._conn().execute(
            "INSERT INTO localnet_kv VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
            (key, json.dumps(val)))

    def ensure_meta_table(self) -> None:
        self.storage._conn().execute(
            "CREATE TABLE IF NOT EXISTS localnet_kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")

    # --- deterministic state machine (module "circulatory") -----------------
    def apply_tx(self, tx: Tx, now: int, tiers: dict[str, int]) -> list[dict]:
        L = self.ledger
        a = tx.args
        if tx.module != "circulatory":
            raise Reject("UNKNOWN_MODULE", tx.module)
        nonces = self.meta("nonces", {})
        if tx.sender is not None and tx.nonce != nonces.get(tx.sender, 0):
            raise Reject("BAD_NONCE", f"expected {nonces.get(tx.sender, 0)}")

        def amt(k: str = "amount") -> int:
            v = a.get(k)
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise Reject("BAD_ARGS", f"{k} must be a positive int")
            return v

        ev: list[dict]
        if tx.method == "transfer":
            to, n = a.get("to"), amt()
            if not tx.sender or not isinstance(to, str) or not to or to == VAULT:
                raise Reject("BAD_ARGS", "sender and to required")
            if n > TIER_LIMITS.get(tiers.get(tx.sender, 0), 0):
                raise Reject("TIER_LIMIT", "transfer exceeds personhood tier limit")
            bal = L.settle(tx.sender, now)
            if bal < n:
                raise Reject("INSUFFICIENT_BALANCE", f"{bal} < {n}")
            L._put(tx.sender, bal - n, now)
            L.deposit(to, n, now)
            ev = [{"type": "transfer", "from": tx.sender, "to": to, "amount": n}]
        elif tx.method == "settle":
            acct = a.get("account") or tx.sender
            ev = [{"type": "settle", "account": acct, "balance": L.settle(acct, now)}]
        elif tx.method == "redistribute":
            acct, n = a.get("account"), amt()
            if not isinstance(acct, str) or n > L.vault():
                raise Reject("INSUFFICIENT_BALANCE" if isinstance(acct, str) else "BAD_ARGS")
            L.redistribute(acct, n, now)
            ev = [{"type": "redistribute", "account": acct, "amount": n}]
        elif tx.method == "spend_nullifier":
            nul = a.get("nullifier")
            if not isinstance(nul, int) or nul < 0:
                raise Reject("BAD_ARGS", "nullifier must be a non-negative int")
            if self.storage.is_nullifier_spent(nul):
                raise Reject("NULLIFIER_SPENT")
            L.spend_nullifier(nul, now)
            ev = [{"type": "nullifier", "nullifier": str(nul)}]
        elif tx.method == "faucet":  # dev-only genesis-style mint, gated by the RPC layer
            to, n = a.get("to"), amt()
            if not isinstance(to, str) or not to or to == VAULT:
                raise Reject("BAD_ARGS")
            L.deposit(to, n, now)
            ev = [{"type": "faucet", "to": to, "amount": n}]
        else:
            raise Reject("UNKNOWN_METHOD", tx.method)
        if tx.sender is not None:
            nonces[tx.sender] = nonces.get(tx.sender, 0) + 1
            self.set_meta("nonces", nonces)
        return ev

    def execute_block(self, txs: list[Tx], now: int, tiers: dict[str, int]) -> tuple[str, list]:
        """Apply txs atomically per tx (savepoint). Caller owns the outer transaction."""
        receipts = []
        for tx in txs:
            try:
                with self.storage.transaction():  # nested -> SAVEPOINT; Reject rolls back
                    ev = self.apply_tx(tx, now, tiers)
                receipts.append({"tx_id": tx.tx_id, "ok": True, "events": ev})
            except Reject as r:
                receipts.append({"tx_id": tx.tx_id, "ok": False, "code": r.code,
                                 "error": str(r)})
        self.height += 1
        self.set_meta("height", self.height)
        root = str(self.ledger.root())
        if self.byzantine:  # fault injection: report a corrupted root
            root = str(int(root) ^ 1)
        return root, receipts


class Localnet:
    """N simulated validators in one process. Not thread-safe: callers serialize."""

    def __init__(self, data_dir: str | Path, genesis: Optional[Genesis] = None,
                 dev: bool = True) -> None:
        self.dir = Path(data_dir)
        self.dev = dev
        gpath = self.dir / "genesis.json"
        if gpath.exists():
            g = Genesis.from_json(json.loads(gpath.read_text()))
            if genesis is not None and genesis.hash() != g.hash():
                raise LocalnetError("data dir holds a different genesis; use a new --data-dir")
            self.genesis = g
            fresh = False
        else:
            self.genesis = genesis or Genesis()
            self.dir.mkdir(parents=True, exist_ok=True)
            gpath.write_text(json.dumps(self.genesis.to_json(), indent=2, sort_keys=True))
            fresh = True
        self.chain_id = self.genesis.chain_id
        self.tiers = {a.name: a.personhood_tier for a in self.genesis.dev_account_list()}
        self.validators = [Validator(v, self.dir / f"{v.split(':')[0]}.sqlite3")
                           for v in self.genesis.validator_ids()]
        for v in self.validators:
            v.ensure_meta_table()
        self.blocks_path = self.dir / "blocks.jsonl"
        self.mempool: list[Tx] = []
        self.blocks: list[Block] = []
        if fresh:
            self._init_genesis()
        else:
            self._load()

    # --- genesis / persistence -------------------------------------------
    def _init_genesis(self) -> None:
        t = self.genesis.genesis_time
        for v in self.validators:
            with v.storage.transaction():
                for a in self.genesis.dev_account_list():
                    v.ledger.deposit(a.name, a.balance, t)
                v.set_meta("height", 0)
                v.set_meta("notes", [{"id": i, "face": f} for i, f in enumerate(self.genesis.notes)])
        roots = {str(v.ledger.root()) for v in self.validators}
        assert len(roots) == 1, "non-deterministic genesis"
        hdr = self._header(0, t, "00" * 32, [], roots.pop(), proposer="genesis")
        self.time = t
        self._append(Block(hdr, [], []))

    def _load(self) -> None:
        for line in self.blocks_path.read_text().splitlines():
            d = json.loads(line)
            self.blocks.append(Block(d["header"], d["txs"], d["receipts"], d["last_commit"]))
        st = json.loads((self.dir / "clock.json").read_text())
        self.time = st["time"]
        for v in self.validators:
            v.height = v.meta("height", 0)

    def _append(self, b: Block) -> None:
        self.blocks.append(b)
        with self.blocks_path.open("a") as f:
            f.write(json.dumps(b.to_json(), sort_keys=True) + "\n")
        self._save_clock()

    def _save_clock(self) -> None:
        (self.dir / "clock.json").write_text(json.dumps({"time": self.time}))

    def _header(self, height: int, t: int, prev: str, ids: list[str], root: str,
                proposer: str) -> dict:
        vh = H(self.genesis.validator_ids())
        return {"chain_id": self.chain_id, "height": height, "time": t, "prev_hash": prev,
                "tx_root": tx_root(ids), "state_root": root,
                "aux_roots": {"notes": "0", "nullifiers": "0", "personhood": "0",
                              "governance": "0"},
                "validators_hash": vh, "next_validators_hash": vh, "proposer": proposer,
                "app_version": APP_VERSION, "module_code_hash": "00" * 32}

    def close(self) -> None:
        for v in self.validators:
            v.close()

    # --- queries ------------------------------------------------------------
    @property
    def height(self) -> int:
        return self.blocks[-1].header["height"]

    def status(self) -> dict:
        head = self.blocks[-1]
        return {"chain_id": self.chain_id, "height": self.height, "time": self.time,
                "head": head.hash, "state_root": head.header["state_root"],
                "mempool": len(self.mempool), "dev": self.dev,
                "consensus": "PLACEHOLDER round-robin + 2/3 root quorum",
                "validators": [{"id": v.id, "online": v.online, "byzantine": v.byzantine,
                                "height": v.height} for v in self.validators]}

    def _ref(self) -> Validator:
        for v in self.validators:
            if v.online and not v.byzantine and v.height == self.height:
                return v
        raise LocalnetError("no honest synced validator")

    def get_account(self, addr: str) -> dict:
        v = self._ref()
        a = v.storage.get_account(addr)
        return {"address": addr, "balance": a[0] if a else 0, "last_update": a[1] if a else None,
                "nonce": v.meta("nonces", {}).get(addr, 0), "tier": self.tiers.get(addr, 0)}

    def get_proof(self, addr: str) -> dict:
        return self._ref().ledger.proof(addr).to_json()

    def get_block(self, ref: int | str) -> dict:
        for b in self.blocks:
            if b.header["height"] == ref or b.hash == ref:
                return b.to_json()
        raise LocalnetError(f"unknown block {ref!r}")

    def get_tx(self, tx_id: str) -> dict:
        for b in self.blocks:
            for t, r in zip(b.txs, b.receipts):
                if r["tx_id"] == tx_id:
                    return {"height": b.header["height"], "tx": t, "receipt": r}
        raise LocalnetError(f"unknown tx {tx_id}")

    # --- tx / block production ----------------------------------------------
    def send_tx(self, d: dict) -> str:
        tx = Tx.from_json(d)
        if tx.chain_id != self.chain_id:
            raise Reject("BAD_ARGS", "wrong chain_id")
        if tx.method == "faucet" and not self.dev:
            raise Reject("UNAUTHORIZED", "faucet is dev-only")
        self.mempool.append(tx)
        return tx.tx_id

    def faucet(self, addr: str, amount: int) -> str:
        return self.send_tx({"chain_id": self.chain_id, "module": "circulatory",
                             "method": "faucet", "sender": None, "nonce": 0,
                             "args": {"to": addr, "amount": amount}})

    def mine(self, n: int = 1) -> list[dict]:
        if n < 1 or n > 10_000:
            raise LocalnetError("n must be in [1, 10000]")
        return [self._produce() for _ in range(n)]

    def _produce(self) -> dict:
        height = self.height + 1
        self.time += self.genesis.block_time
        prop = self.validators[height % len(self.validators)]  # PLACEHOLDER round-robin
        txs, self.mempool = self.mempool, []
        votes: dict[str, list[str]] = {}
        receipts = None
        need = (2 * len(self.validators)) // 3 + 1
        live = [v for v in self.validators if v.online]
        for v in live:
            self._catch_up(v)
        try:
            with ExitStack() as stack:
                for v in live:
                    stack.enter_context(v.storage.transaction())
                    root, rc = v.execute_block(txs, self.time, self.tiers)
                    votes.setdefault(root, []).append(v.id)
                    if not v.byzantine and receipts is None:
                        receipts = rc
                root, signers = (max(votes.items(), key=lambda kv: len(kv[1]))
                                 if votes else ("", []))
                if len(signers) < need:
                    raise _NoQuorum(f"no quorum at height {height}: {len(signers)}/{need} "
                                    f"({len(votes)} distinct roots)")
        except _NoQuorum as e:  # every validator's block transaction was rolled back
            for v in live:
                v.height = v.meta("height", 0)
            self.time -= self.genesis.block_time
            self.mempool = txs + self.mempool
            raise LocalnetError(str(e)) from None
        if prop.id not in signers:  # placeholder: proposer must be online+honest, else skip
            prop = next(v for v in self.validators if v.id in signers)
        prev = self.blocks[-1].hash
        hdr = self._header(height, self.time, prev, [t.tx_id for t in txs], root, prop.id)
        commit = {"height": height, "round": 0, "block_hash": H(hdr),
                  "sigs": [{"validator": s, "sig": None} for s in signers]}
        b = Block(hdr, [t.to_json() for t in txs], receipts or [], commit)
        self._append(b)
        return {"height": height, "hash": b.hash, "txs": len(txs), "proposer": prop.id,
                "signers": len(signers)}

    def _catch_up(self, v: Validator) -> None:
        """Replay committed blocks a recovered validator missed."""
        while v.height < self.height:
            b = self.blocks[v.height + 1]
            with v.storage.transaction():
                root, _ = v.execute_block([Tx.from_json(t) for t in b.txs], b.header["time"],
                                          self.tiers)
            if not v.byzantine and root != b.header["state_root"]:
                raise LocalnetError(f"{v.id} diverged replaying height {b.header['height']}")

    # --- dev controls ---------------------------------------------------------
    def _need_dev(self) -> None:
        if not self.dev:
            raise LocalnetError("UNAUTHORIZED: dev-only method (start with --dev)")

    def warp(self, seconds: int) -> int:
        """Advance chain time; next block's time = time + block_time."""
        self._need_dev()
        if not isinstance(seconds, int) or seconds < 0:
            raise LocalnetError("seconds must be a non-negative int (time never goes backwards)")
        self.time += seconds
        self._save_clock()
        return self.time

    def set_time(self, ts: int) -> int:
        self._need_dev()
        if ts < self.time:
            raise LocalnetError(f"cannot set time backwards ({ts} < {self.time}); use revert")
        return self.warp(ts - self.time)

    def snapshot(self) -> str:
        self._need_dev()
        sid = f"{self.height}-{H([self.blocks[-1].hash, self.time, len(self.mempool)])[:12]}"
        d = self.dir / "snapshots" / sid
        d.mkdir(parents=True, exist_ok=True)
        for v in self.validators:
            v.backup(d / v.db.name)
        shutil.copy(self.blocks_path, d / "blocks.jsonl")
        (d / "state.json").write_text(json.dumps({
            "time": self.time, "mempool": [t.to_json() for t in self.mempool],
            "validators": [[v.online, v.byzantine, v.height] for v in self.validators]}))
        return sid

    def snapshots(self) -> list[str]:
        p = self.dir / "snapshots"
        return sorted(x.name for x in p.iterdir()) if p.exists() else []

    def revert(self, sid: str) -> dict:
        self._need_dev()
        d = self.dir / "snapshots" / sid
        if "/" in sid or ".." in sid or not (d / "state.json").exists():
            raise LocalnetError(f"unknown snapshot {sid!r}")
        st = json.loads((d / "state.json").read_text())
        for v, (on, byz, h) in zip(self.validators, st["validators"]):
            v.close()
            for suf in ("", "-wal", "-shm"):
                Path(str(v.db) + suf).unlink(missing_ok=True)
            shutil.copy(d / v.db.name, v.db)
            v.open()
            v.online, v.byzantine, v.height = on, byz, h
        shutil.copy(d / "blocks.jsonl", self.blocks_path)
        self.blocks = []
        self.time = 0
        self._load_blocks_only()
        self.time = st["time"]
        self._save_clock()
        self.mempool = [Tx.from_json(t) for t in st["mempool"]]
        return self.status()

    def _load_blocks_only(self) -> None:
        for line in self.blocks_path.read_text().splitlines():
            d = json.loads(line)
            self.blocks.append(Block(d["header"], d["txs"], d["receipts"], d["last_commit"]))

    def inject_fault(self, spec: dict) -> dict:
        """spec = {"validator": index, "kind": "offline"|"online"|"byzantine"|"honest"}."""
        self._need_dev()
        try:
            v = self.validators[int(spec["validator"])]
            kind = spec["kind"]
        except (KeyError, IndexError, ValueError, TypeError):
            raise LocalnetError("spec needs validator index and kind") from None
        if kind == "offline":
            v.online = False
        elif kind == "online":
            v.online = True
        elif kind == "byzantine":
            v.byzantine = True
        elif kind == "honest":
            v.byzantine = False
        else:
            raise LocalnetError(f"unknown fault kind {kind!r}")
        return {"id": v.id, "online": v.online, "byzantine": v.byzantine}

    def verify(self) -> bool:
        """Every honest synced validator's ledger root matches head and its tree verifies."""
        head = self.blocks[-1].header["state_root"]
        return all(str(v.ledger.root()) == head and v.ledger.verify_state()
                   for v in self.validators if v.online and not v.byzantine
                   and v.height == self.height)
