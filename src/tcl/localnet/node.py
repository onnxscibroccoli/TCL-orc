"""In-process multi-validator localnet aligned with docs/13a and the 13b engine.

Consensus modes:
  * ``bft`` (default): one ``tcl.consensus.TendermintEngine`` per validator, Ed25519-signed
    proposals/votes, messages routed in-process by a deterministic host loop (virtual ticks; no
    sockets, no wall clock). Blocks commit only with a >2/3 precommit certificate.
  * ``fast``: PLACEHOLDER round-robin proposer + >2/3 equal-weight state-root quorum (no
    signatures on votes). Kept for speed; not a consensus protocol.

Every validator owns an independent SQLite ledger and re-executes every block. Dev txs are
Ed25519-signed (13a); the dev keys are derived from the public genesis seed and are DEV ONLY.
"""
from __future__ import annotations

import heapq
import json
import shutil
import sqlite3
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..consensus import ed25519
from ..consensus.encoding import H, merkle_root, signed_payload
from ..consensus.evidence import BadEvidence, DuplicateVoteEvidence
from ..consensus.engine import Broadcast, Decide, ScheduleTimeout, TendermintEngine
from ..consensus.types import (GENESIS_PREV, Invalid, Proposal, Vote, block_hash,
                               check_tx_shape, make_header, sign_proposal, sign_tx, sign_vote,
                               tx_id, validators_hash, verify_commit)
from ..ledger import Ledger, SQLiteStorage
from ..node.chain import ChainState, ModuleReject, evidence_args, evidence_from_args
from ..node.notes import NoteError
from ..storage.db import VAULT
from .genesis import Genesis

APP_VERSION = "localnet-0.2"
CONSENSUS_MODES = ("bft", "fast")
REJECT_CODES = frozenset({"BAD_SIG", "BAD_NONCE", "BAD_PROOF", "NULLIFIER_SPENT",
                          "INSUFFICIENT_BALANCE", "TIER_LIMIT", "PAUSED", "UNKNOWN_MODULE",
                          "UNKNOWN_METHOD", "WORK_BUDGET", "UNAUTHORIZED", "BAD_ARGS"})
# MOCK personhood tiers -> max single transfer (tiered attestations, doc 11)
TIER_LIMITS = {1: 10**10, 2: 10**11, 3: 10**12}
BFT_TICK_BUDGET = 5_000  # virtual ticks per height before giving up (no quorum)
NATIVE_MODULES = ("consensus", "notes", "governance")
TX_KEYS = ("chain_id", "module", "method", "sender", "nonce", "args", "proof", "sig", "pow_stamp")


class LocalnetError(Exception):
    pass


class _NoQuorum(Exception):
    pass


class _DryRun(Exception):
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
        return {k: getattr(self, k) for k in TX_KEYS}

    @property
    def tx_id(self) -> str:
        return tx_id(self.to_json())

    @classmethod
    def from_json(cls, d: Any) -> "Tx":
        if not isinstance(d, dict):
            raise Reject("BAD_ARGS", "tx must be an object")
        try:
            t = cls(**{k: d[k] for k in ("chain_id", "module", "method", "sender", "nonce",
                                         "args")},
                    proof=d.get("proof"), sig=d.get("sig"), pow_stamp=d.get("pow_stamp", 0))
        except (KeyError, TypeError) as e:
            raise Reject("BAD_ARGS", f"malformed tx: {e}") from None
        if not isinstance(t.args, dict) or not isinstance(t.nonce, int):
            raise Reject("BAD_ARGS", "args must be an object, nonce an int")
        return t


@dataclass
class Block:
    header: dict
    txs: list[dict]
    receipts: list[dict]
    last_commit: Optional[dict] = None
    commit: Optional[dict] = None  # certificate that finalized THIS block (bft) / signer list (fast)

    @property
    def hash(self) -> str:
        return block_hash(self.header)

    def wire(self) -> dict:
        return {"header": self.header, "txs": self.txs, "last_commit": self.last_commit}

    def to_json(self) -> dict:
        return {"hash": self.hash, **self.wire(), "receipts": self.receipts,
                "commit": self.commit}

    @classmethod
    def from_json(cls, d: dict) -> "Block":
        return cls(d["header"], d["txs"], d["receipts"], d.get("last_commit"), d.get("commit"))


def verify_tx_sig(tx: Tx) -> bool:
    try:
        body = {k: v for k, v in tx.to_json().items() if k != "sig"}
        return ed25519.verify(bytes.fromhex(tx.sender or ""),
                              signed_payload(tx.chain_id, "tx", body), bytes.fromhex(tx.sig or ""))
    except (ValueError, TypeError):
        return False


class Validator:
    def __init__(self, index: int, secret: bytes, db: Path) -> None:
        self.index = index
        self.secret = secret
        self.id = ed25519.public_key(secret).hex()
        self.db = db
        self.online = True
        self.byzantine = False
        self.height = 0
        self.open()

    def open(self) -> None:
        self.storage = SQLiteStorage(self.db)
        self.ledger = Ledger(self.storage, mode="fixed")
        self.storage._conn().execute(
            "CREATE TABLE IF NOT EXISTS localnet_kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)")

    def close(self) -> None:
        self.storage.close()

    def chain(self, now: int) -> ChainState:
        if not hasattr(self, "_vcache"):
            self._vcache: dict = {}  # this node's own Groth16 verification results
        return ChainState(self.meta("chain", None), self.chain_id, _LedgerBank(self.ledger, now),
                          self._vcache, self.dev_proofs)

    def chain_keys(self) -> list[str]:
        return ChainState(self.meta("chain", None), self.chain_id, None).validator_keys()

    def backup(self, dst: Path) -> None:
        out = sqlite3.connect(dst)
        try:
            self.storage._conn().backup(out)
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

    # --- deterministic state machine (module "circulatory") -----------------
    def apply_tx(self, tx: Tx, now: int, tiers: dict[str, int], st: ChainState,
                 evidence: list) -> list[dict]:
        L = self.ledger
        a = tx.args
        if tx.module == "consensus" and tx.method == "beacon":
            # unsigned: self-authenticating ECVRF proof by the epoch's beacon leader
            return st.apply("consensus", "beacon", None, a, None, now)
        if tx.module == "consensus" and tx.method == "evidence":
            # unsigned: self-authenticating (two Ed25519-signed conflicting votes)
            ev = st.check_evidence(a)
            if any(e == a for e in evidence):
                raise Reject("NULLIFIER_SPENT", "duplicate evidence in block")
            evidence.append(a)
            return [{"type": "evidence", "validator": ev.a.validator}]
        if tx.method == "faucet" and tx.module == "circulatory":
            # dev-only mint; admitted only by a --dev node (RPC/send_tx gate). Unsigned.
            to, n = a.get("to"), a.get("amount")
            if not isinstance(to, str) or not to or to == VAULT or not _pos_int(n):
                raise Reject("BAD_ARGS")
            L.deposit(to, n, now)
            return [{"type": "faucet", "to": to, "amount": n}]
        try:
            check_tx_shape(tx.to_json())
        except Invalid as e:
            raise Reject(str(e).split(":")[0], str(e)) from None
        if tx.module not in ("circulatory",) + NATIVE_MODULES:
            raise Reject("UNKNOWN_MODULE", tx.module)
        if tx.sender is not None:
            if not verify_tx_sig(tx):
                raise Reject("BAD_SIG")
            nonces = self.meta("nonces", {})
            if tx.nonce != nonces.get(tx.sender, 0):
                raise Reject("BAD_NONCE", f"expected {nonces.get(tx.sender, 0)}")

        def amt(k: str = "amount") -> int:
            v = a.get(k)
            if not _pos_int(v):
                raise Reject("BAD_ARGS", f"{k} must be a positive int")
            return v

        ev: list[dict]
        if tx.module in NATIVE_MODULES:
            ev = st.apply(tx.module, tx.method, tx.sender, a, tx.proof, now)
        elif tx.method == "transfer":
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
        elif tx.method == "settle":  # anyone may poke lazy demurrage on any account
            acct = a.get("account") or tx.sender
            if not isinstance(acct, str) or not acct:
                raise Reject("BAD_ARGS")
            ev = [{"type": "settle", "account": acct, "balance": L.settle(acct, now)}]
        elif tx.method == "spend_nullifier":
            # Groth16 (BN254) proof verified in-node by every validator (tcl.node.groth16)
            nul = st.verify_anon(a, tx.proof)
            if self.storage.is_nullifier_spent(nul):
                raise Reject("NULLIFIER_SPENT")
            L.spend_nullifier(nul, now)
            st.d["anon_nullifiers"].append(str(nul))
            ev = [{"type": "nullifier", "nullifier": str(nul)}]
        else:
            raise Reject("UNKNOWN_METHOD", tx.method)
        if tx.sender is not None:
            nonces[tx.sender] = nonces.get(tx.sender, 0) + 1
            self.set_meta("nonces", nonces)
        return ev

    def execute_block(self, txs: list[Tx], now: int, tiers: dict[str, int],
                      last_commit: Optional[dict] = None) -> tuple[str, list]:
        """Apply txs (each in a SAVEPOINT), then block-end evidence slashing + epoch rotation.
        Caller owns the outer transaction. Sets self.last_aux / self.last_next_vals."""
        receipts = []
        st = self.chain(now)
        self.ledger.decay_override = st.demurrage_rule()  # epoch-scoped governed demurrage
        evidence: list = []
        for tx in txs:
            snap = json.dumps(st.save())
            st.bank.now = now
            try:
                with self.storage.transaction():
                    ev = self.apply_tx(tx, now, tiers, st, evidence)
                receipts.append({"tx_id": tx.tx_id, "ok": True, "events": ev})
            except (Reject, ModuleReject, NoteError) as r:
                st = ChainState(json.loads(snap), self.chain_id, _LedgerBank(self.ledger, now),
                                self._vcache, self.dev_proofs)
                receipts.append({"tx_id": tx.tx_id, "ok": False, "code": r.code,
                                 "error": str(r)})
        self.height += 1
        end = st.end_block(self.height, evidence, last_commit)
        if end:
            receipts.append({"tx_id": "block-end", "ok": True, "events": end})
        self.set_meta("chain", st.save())
        self.set_meta("height", self.height)
        self.last_aux = st.aux_roots()
        self.last_next_vals = st.validator_keys()
        return str(self.ledger.root()), receipts

    def dry_run(self, txs: list[Tx], now: int, tiers: dict[str, int],
                last_commit: Optional[dict] = None) -> tuple[str, list]:
        """Execute and roll back; returns (post-state root, receipts)."""
        h = self.height
        out: tuple[str, list] = ("", [])
        try:
            with self.storage.transaction():
                out = self.execute_block(txs, now, tiers, last_commit)
                raise _DryRun
        except _DryRun:
            pass
        finally:
            self.height = h
        return out


class _LedgerBank:
    """notes/consensus modules' view of the ledger (debits settle demurrage first)."""

    def __init__(self, ledger: Ledger, now: int) -> None:
        self.L, self.now = ledger, now

    def debit(self, acct: str, amount: int) -> None:
        if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0:
            raise Reject("BAD_ARGS", "amount")
        bal = self.L.settle(acct, self.now)
        if bal < amount:
            raise Reject("INSUFFICIENT_BALANCE", f"{bal} < {amount}")
        self.L._put(acct, bal - amount, self.now)

    def credit(self, acct: str, amount: int) -> None:
        if amount:
            self.L.deposit(acct, amount, self.now)


def _pos_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


class _HostApp:
    """13b ``Application`` for one validator, backed by its own ledger."""

    def __init__(self, net: "Localnet", v: Validator) -> None:
        self.net, self.v = net, v
        self.chain_id = net.chain_id
        self.decided: Optional[tuple[dict, dict, list]] = None
        self._alt = 0  # byzantine proposers bump this to make a conflicting block

    def validators(self, height: int) -> list[str]:
        return self.net.vals_sorted

    def build_block(self, height: int, rnd: int, proposer: str) -> dict:
        n = self.net
        txs = n._round_txs
        t = n._round_time + self._alt
        lc = n.blocks[-1].commit if height > 1 else None
        root, _ = self.v.dry_run([Tx.from_json(x) for x in txs], t, n.tiers, lc)
        hdr = n._header(height, t, n.blocks[-1].hash, [tx_id(x) for x in txs], root, proposer,
                        self.v.last_aux, self.v.last_next_vals)
        return {"header": hdr, "txs": txs, "last_commit": lc}

    def validate_block(self, block: dict, height: int) -> bool:
        n = self.net
        try:
            hd, txs = block["header"], block["txs"]
            parent = n.blocks[-1]
            if hd["chain_id"] != n.chain_id or hd["height"] != height or \
                    hd["prev_hash"] != parent.hash or hd["time"] <= parent.header["time"]:
                return False
            if hd["validators_hash"] != validators_hash(self.v.chain_keys()) or \
                    hd["tx_root"] != merkle_root([tx_id(x) for x in txs]):
                return False
            if height > 1:
                lc = block["last_commit"]
                if lc is None or lc["block_hash"] != parent.hash or \
                        not verify_commit(lc, n.chain_id, n.valset_at(height - 1)):
                    return False
            root, _ = self.v.dry_run([Tx.from_json(x) for x in txs], hd["time"], n.tiers,
                                     block["last_commit"])
            return root == hd["state_root"] and hd["aux_roots"] == self.v.last_aux and \
                hd["next_validators_hash"] == validators_hash(self.v.last_next_vals)
        except (KeyError, TypeError, Reject):
            return False

    def on_decide(self, block: dict, commit: dict) -> None:
        txs = [Tx.from_json(x) for x in block["txs"]]
        with self.v.storage.transaction():
            root, rc = self.v.execute_block(txs, block["header"]["time"], self.net.tiers,
                                            block["last_commit"])
            if root != block["header"]["state_root"]:
                raise LocalnetError(f"{self.v.id[:12]} state root mismatch on decide")
        self.decided = (block, commit, rc)


class Localnet:
    """N simulated validators in one process. Not thread-safe: callers serialize."""

    def __init__(self, data_dir: str | Path, genesis: Optional[Genesis] = None,
                 dev: bool = True, consensus: str = "bft") -> None:
        if consensus not in CONSENSUS_MODES:
            raise LocalnetError(f"consensus must be one of {CONSENSUS_MODES}")
        self.consensus = consensus
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
        self.dev_keys = self.genesis.dev_keys() if dev else {}
        self.tiers = {a.name: a.personhood_tier for a in self.genesis.dev_account_list()}
        self.validators = [Validator(i, self.genesis.validator_secret(i),
                                     self.dir / f"val{i}.sqlite3")
                           for i in range(self.genesis.validators)]
        for v in self.validators:
            v.chain_id, v.dev_proofs = self.chain_id, self.genesis.dev_proofs
        # gossip layer: each validator's pool of verified double-sign evidence (in memory)
        self.evidence_pool: dict[str, dict] = {v.id: {} for v in self.validators}
        self._seen: dict[str, dict] = {}
        self.blocks_path = self.dir / "blocks.jsonl"
        self.mempool: list[Tx] = []
        self.blocks: list[Block] = []
        self.evidence_log: list[dict] = []
        self._round_txs: list[dict] = []
        self._round_time = 0
        if fresh:
            self._init_genesis()
        else:
            self._load()

    # --- genesis / persistence -------------------------------------------
    def _init_genesis(self) -> None:
        t = self.genesis.genesis_time
        g = self.genesis
        persons = {f"person:{a.name}": a.personhood_tier for a in g.dev_account_list()}
        chain0 = ChainState.genesis(self.chain_id, g.validator_ids(), persons,
                                    g.seats or g.validators, g.epoch_blocks, g.term_epochs,
                                    g.cooldown_epochs, g.validator_bond, g.note_unit)
        for v in self.validators:
            with v.storage.transaction():
                v.set_meta("chain", chain0)
                for a in self.genesis.dev_account_list():
                    v.ledger.deposit(a.name, a.balance, t)
                v.set_meta("height", 0)
                v.set_meta("notes", [{"id": i, "face": f}
                                     for i, f in enumerate(self.genesis.notes)])
        roots = {str(v.ledger.root()) for v in self.validators}
        assert len(roots) == 1, "non-deterministic genesis"
        hdr = make_header(chain_id=self.chain_id, height=0, time=t, prev_hash=GENESIS_PREV,
                          tx_root=merkle_root([]), state_root=roots.pop(),
                          validators_hash=validators_hash(self.vals_sorted),
                          next_validators_hash=validators_hash(self.vals_sorted),
                          proposer="00" * 32, app_version=APP_VERSION,
                          aux_roots=ChainState(chain0, self.chain_id, None).aux_roots())
        self.time = t
        self.blocks_path.write_text("")
        self._append(Block(hdr, [], []))

    def _load(self) -> None:
        self._load_blocks()
        self.time = json.loads((self.dir / "clock.json").read_text())["time"]
        for v in self.validators:
            v.height = v.meta("height", 0)

    def _load_blocks(self) -> None:
        self.blocks = [Block.from_json(json.loads(line))
                       for line in self.blocks_path.read_text().splitlines()]

    def _append(self, b: Block) -> None:
        self.blocks.append(b)
        with self.blocks_path.open("a") as f:
            f.write(json.dumps(b.to_json(), sort_keys=True) + "\n")
        self._save_clock()

    def _save_clock(self) -> None:
        (self.dir / "clock.json").write_text(json.dumps({"time": self.time}))

    def _header(self, height: int, t: int, prev: str, ids: list[str], root: str,
                proposer: str, aux: dict, next_vals: list[str]) -> dict:
        vh = validators_hash(self.valset_at(height))
        return make_header(chain_id=self.chain_id, height=height, time=t, prev_hash=prev,
                           tx_root=merkle_root(ids), state_root=root, validators_hash=vh,
                           next_validators_hash=validators_hash(next_vals), proposer=proposer,
                           app_version=APP_VERSION, aux_roots=aux)

    # --- validator set (committed chain state) ---------------------------------
    def _chain_ref(self) -> Validator:
        for v in self.validators:
            if v.height == self.height and not v.byzantine:
                return v
        return max(self.validators, key=lambda v: v.height)

    def valset_at(self, height: int) -> list[str]:
        hist = self._chain_ref().meta("chain", None)["valset_hist"]
        keys = hist[0][1]
        for start, ks in hist:
            if start <= height:
                keys = ks
        return keys

    @property
    def vals_sorted(self) -> list[str]:
        """Validator set that signs the NEXT block."""
        return self.valset_at(self.height + 1) if self.blocks else \
            self.validators[0].chain_keys()

    def chain_state(self) -> dict:
        return self._chain_ref().meta("chain", None)

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
                "mempool": len(self.mempool), "dev": self.dev, "consensus": self.consensus,
                "validators": [{"index": v.index, "id": v.id, "online": v.online,
                                "byzantine": v.byzantine, "height": v.height}
                               for v in self.validators]}

    def _ref(self) -> Validator:
        for v in self.validators:
            if v.online and not v.byzantine and v.height == self.height:
                return v
        raise LocalnetError("no honest synced validator")

    def get_account(self, addr: str) -> dict:
        v = self._ref()
        a = v.storage.get_account(addr)
        return {"address": addr, "balance": a[0] if a else 0,
                "last_update": a[1] if a else None,
                "nonce": v.meta("nonces", {}).get(addr, 0), "tier": self.tiers.get(addr, 0)}

    def get_proof(self, addr: str) -> dict:
        return self._ref().ledger.proof(addr).to_json()

    def get_block(self, ref: int | str) -> dict:
        for b in self.blocks:
            if b.header["height"] == ref or b.hash == ref:
                return b.to_json()
        raise LocalnetError(f"unknown block {ref!r}")

    def get_tx(self, txid: str) -> dict:
        for b in self.blocks:
            for t, r in zip(b.txs, b.receipts):
                if r["tx_id"] == txid:
                    return {"height": b.header["height"], "tx": t, "receipt": r}
        raise LocalnetError(f"unknown tx {txid}")

    def dev_accounts(self) -> list[str]:
        self._need_dev()
        return list(self.dev_keys)

    # --- tx admission ---------------------------------------------------------
    def send_tx(self, d: Any) -> str:
        tx = Tx.from_json(d)
        if tx.chain_id != self.chain_id:
            raise Reject("BAD_ARGS", "wrong chain_id")
        if tx.method == "faucet":
            if not self.dev:
                raise Reject("UNAUTHORIZED", "faucet is dev-only")
        elif tx.sender is not None and not verify_tx_sig(tx):
            raise Reject("BAD_SIG")  # fail fast; validators re-check at execution
        self.mempool.append(tx)
        return tx.tx_id

    def faucet(self, addr: str, amount: int) -> str:
        return self.send_tx({"chain_id": self.chain_id, "module": "circulatory",
                             "method": "faucet", "sender": None, "nonce": 0,
                             "args": {"to": addr, "amount": amount}})

    def sign_dev_tx(self, sender: str, method: str, args: dict,
                    nonce: Optional[int] = None, module: str = "circulatory") -> dict:
        """DEV ONLY: build + Ed25519-sign a tx for a genesis dev account."""
        self._need_dev()
        sk = self.dev_keys.get(sender)
        if sk is None:
            raise LocalnetError("not a dev account")
        if nonce is None:
            nonce = self.get_account(sender)["nonce"] + sum(
                1 for t in self.mempool if t.sender == sender)
        tx = {"chain_id": self.chain_id, "module": module, "method": method,
              "sender": sender, "nonce": nonce, "args": args, "proof": None, "sig": None,
              "pow_stamp": 0}
        return sign_tx(tx, sk)

    def dev_send(self, sender: str, method: str, args: dict,
                 module: str = "circulatory") -> str:
        return self.send_tx(self.sign_dev_tx(sender, method, args, module=module))

    # --- block production -----------------------------------------------------
    def mine(self, n: int = 1) -> list[dict]:
        if not isinstance(n, int) or n < 1 or n > 10_000:
            raise LocalnetError("n must be in [1, 10000]")
        return [self._produce() for _ in range(n)]

    def _produce(self) -> dict:
        height = self.height + 1
        txs, self.mempool = self.mempool, []
        user_txs = list(txs)
        txs = txs + self._evidence_txs()  # gossiped double-sign evidence, applied at block end
        txs = txs + self._beacon_txs()
        self.time += self.genesis.block_time
        live = [v for v in self.validators if v.online]
        try:
            for v in live:
                self._catch_up(v)
            if self.consensus == "bft":
                return self._produce_bft(height, txs, live)
            return self._produce_fast(height, txs, live)
        except _NoQuorum as e:
            self.time -= self.genesis.block_time
            self.mempool = user_txs + self.mempool
            raise LocalnetError(str(e)) from None

    def _produce_fast(self, height: int, txs: list[Tx], live: list[Validator]) -> dict:
        """PLACEHOLDER consensus: round-robin proposer, >2/3 matching post-state roots."""
        votes: dict[str, list[str]] = {}
        receipts = None
        honest_v = None
        lc = self.blocks[-1].commit
        need = (2 * len(self.validators)) // 3 + 1
        try:
            with ExitStack() as stack:
                for v in live:
                    stack.enter_context(v.storage.transaction())
                    root, rc = v.execute_block(txs, self.time, self.tiers, lc)
                    if v.byzantine:  # fault injection: report a corrupted root
                        root = str(int(root) ^ 1)
                    votes.setdefault(root, []).append(v.id)
                    if not v.byzantine and receipts is None:
                        receipts, honest_v = rc, v
                root, signers = (max(votes.items(), key=lambda kv: len(kv[1]))
                                 if votes else ("", []))
                if len(signers) < need:
                    raise _NoQuorum(f"no quorum at height {height}: {len(signers)}/{need} "
                                    f"({len(votes)} distinct roots)")
        except _NoQuorum:
            for v in live:
                v.height = v.meta("height", 0)
            raise
        prop = self.validators[height % len(self.validators)]
        if prop.id not in signers:
            prop = next(v for v in self.validators if v.id in signers)
        hv = honest_v or live[0]
        hdr = self._header(height, self.time, self.blocks[-1].hash, [t.tx_id for t in txs],
                           root, prop.id, hv.last_aux, hv.last_next_vals)
        commit = {"height": height, "round": 0, "block_hash": block_hash(hdr),
                  "sigs": [{"validator": s, "sig": None} for s in sorted(signers)],
                  "placeholder": True}
        b = Block(hdr, [t.to_json() for t in txs], receipts or [],
                  lc, commit)
        self._append(b)
        return {"height": height, "hash": b.hash, "txs": len(txs), "proposer": prop.id,
                "signers": len(signers), "round": 0}

    def _produce_bft(self, height: int, txs: list[Tx], live: list[Validator]) -> dict:
        """Run TendermintEngine instances to a decision, routing actions in-process."""
        self._round_txs = [t.to_json() for t in txs]
        self._round_time = self.time
        self._seen, self._gossip_height = {}, height
        seated = set(self.vals_sorted)
        followers = [v for v in live if v.id not in seated]  # follow via catch-up
        live = [v for v in live if v.id in seated]
        apps = {v.id: _HostApp(self, v) for v in live}
        engines = {v.id: TendermintEngine(apps[v.id], v.secret) for v in live}
        byz = {v.id for v in live if v.byzantine}
        q: list[tuple[int, int, str, str, Any]] = []  # (tick, seq, kind, dst, payload)
        seq = [0]

        def push(t: int, kind: str, dst: str, payload: Any) -> None:
            seq[0] += 1
            heapq.heappush(q, (t, seq[0], kind, dst, payload))

        def route(src: str, actions: list, now: int) -> None:
            for a in actions:
                if isinstance(a, ScheduleTimeout):
                    push(now + a.delay, "timeout", src, (a.height, a.round, a.step))
                elif isinstance(a, Broadcast):
                    if src in byz:
                        self._byz_broadcast(src, a.msg, apps[src], engines, push, now)
                    else:
                        for dst in engines:
                            if dst != src:
                                push(now + 1, "msg", dst, a.msg)
                elif isinstance(a, Decide):
                    pass  # recorded by _HostApp.on_decide

        try:
            with ExitStack() as stack:  # roll everyone back if a decide raises midway
                for v in live:
                    stack.enter_context(v.storage.transaction())
                for vid in sorted(engines):
                    route(vid, engines[vid].start(height), 0)
                honest = [vid for vid in engines if vid not in byz]
                now = 0
                while q and now <= BFT_TICK_BUDGET:
                    if all(apps[h].decided for h in honest):
                        break
                    now, _, kind, dst, payload = heapq.heappop(q)
                    if kind == "evidence":
                        self._gossip_in(dst, payload, engines, byz, push, now)
                        continue
                    if kind == "vote-gossip":
                        self._observe_vote(dst, payload, engines, byz, push, now)
                        continue
                    if kind == "msg" and isinstance(payload, Vote) and dst not in byz:
                        self._observe_vote(dst, payload, engines, byz, push, now)
                    eng = engines[dst]
                    if eng.height != height:
                        continue  # already decided this height
                    if kind == "msg":
                        route(dst, eng.on_message(payload), now)
                    else:
                        route(dst, eng.on_timeout(*payload), now)
                while q:  # drain in-flight gossip so every honest pool converges
                    now, _, kind, dst, payload = heapq.heappop(q)
                    if kind == "evidence":
                        self._gossip_in(dst, payload, engines, byz, push, now)
                    elif kind == "vote-gossip" or (kind == "msg" and isinstance(payload, Vote)
                                                   and dst not in byz):
                        self._observe_vote(dst, payload, engines, byz, push, now)
                decided = {vid: a.decided for vid, a in apps.items() if a.decided}
                hashes = {block_hash(d[0]["header"]) for d in decided.values()}
                if len(hashes) > 1:
                    raise LocalnetError(f"FORK at height {height}: {sorted(hashes)}")
                if not decided:
                    raise _NoQuorum(f"no quorum at height {height} after {now} ticks "
                                    f"({len(live)}/{len(self.validators)} online, "
                                    f"{len(byz)} byzantine)")
        except _NoQuorum:
            for v in live:
                v.height = v.meta("height", 0)
            raise
        blk, commit, rc = next(iter(decided.values()))
        # honest receipts preferred (all honest ones are identical by determinism)
        for vid in sorted(decided):
            if vid not in byz:
                blk, commit, rc = decided[vid]
                break
        b = Block(blk["header"], blk["txs"], rc, blk["last_commit"], commit)
        self._append(b)
        for v in live + followers:  # undecided / non-seated nodes replay the commit
            self._catch_up(v)
        return {"height": height, "hash": b.hash, "txs": len(txs),
                "proposer": blk["header"]["proposer"], "signers": len(commit["sigs"]),
                "round": commit["round"], "decided_by": len(decided)}

    # --- double-sign evidence gossip ------------------------------------------------
    def _observe_vote(self, dst: str, vote: Vote, engines: dict, byz: set, push: Any,
                      now: int) -> None:
        if vote.height != self._gossip_height:
            return  # engines run ahead into h+1 inside one host loop; those votes are discarded
        seen = self._seen.setdefault(dst, {})
        k = (vote.validator, vote.kind, vote.height, vote.round)
        prev = seen.get(k)
        if prev is None:
            seen[k] = vote
            # relay first-seen votes on the evidence-gossip channel (not fed to the engine,
            # so consensus behaviour is unchanged); this is how a peer that only ever saw one
            # side of an equivocation learns about the other side.
            for peer in engines:
                if peer != dst and peer not in byz:
                    push(now + 1, "vote-gossip", peer, vote)
        elif prev.block_hash != vote.block_hash:
            self._gossip_in(dst, evidence_args(DuplicateVoteEvidence(prev, vote)), engines,
                            byz, push, now)

    def _gossip_in(self, dst: str, args: dict, engines: dict, byz: set, push: Any,
                   now: int) -> None:
        """Receive evidence: verify (fail-closed), add to pool, re-gossip to peers once."""
        try:
            ev = evidence_from_args(args)
            off = ev.verify(self.chain_id)
        except (BadEvidence, ModuleReject, KeyError, TypeError, ValueError):
            return
        if off not in set(self.vals_sorted):
            return
        pool = self.evidence_pool.setdefault(dst, {})
        key = H(list(ev.key()))
        if key in pool:
            return
        pool[key] = args
        for peer in engines:
            if peer != dst and peer not in byz:
                push(now + 1, "evidence", peer, args)

    def _beacon_txs(self) -> list[Tx]:
        """Validator behaviour: the epoch's beacon leader (if it is a live, honest local node)
        publishes its ECVRF proof. A byzantine/offline leader withholds -> penalised."""
        if not self.blocks:
            return []
        st = ChainState(self.chain_state(), self.chain_id, None)
        pend = st.beacon_pending()
        if pend is None:
            return []
        leader, epoch, alpha = pend
        v = next((x for x in self.validators if x.id == leader), None)
        if v is None or not v.online or v.byzantine:
            return []
        from ..consensus import vrf
        return [Tx(self.chain_id, "consensus", "beacon", None, 0,
                   {"epoch": epoch, "pi": vrf.prove(v.secret, alpha).hex()})]

    def _evidence_txs(self) -> list[Tx]:
        """Proposer-side inclusion: verified pooled evidence not yet processed on-chain."""
        if not self.blocks:
            return []
        seen = {H(k) for k in self.chain_state()["reg"]["evidence_seen"]}
        out: dict[str, dict] = {}
        for vid, pool in sorted(self.evidence_pool.items()):
            for key in list(pool):
                if key in seen:
                    del pool[key]
                else:
                    out.setdefault(key, pool[key])
        return [Tx(self.chain_id, "consensus", "evidence", None, 0, a) for _, a in
                sorted(out.items())]

    def _byz_broadcast(self, src: str, msg: Any, app: _HostApp, engines: dict, push: Any,
                       now: int) -> None:
        """Equivocate: half the peers get a conflicting signed vote/proposal (cf. consensus.sim)."""
        v = next(x for x in self.validators if x.id == src)
        peers = sorted(d for d in engines if d != src)
        half = set(peers[: len(peers) // 2])
        alt: Any = msg
        if isinstance(msg, Vote):
            fake = H(["equivocate", msg.kind, msg.height, msg.round, msg.block_hash])
            alt = sign_vote(msg.kind, self.chain_id, msg.height, msg.round, fake, v.secret)
        elif isinstance(msg, Proposal):
            app._alt += 1
            blk = app.build_block(msg.height, msg.round, msg.proposer)
            app._alt -= 1
            alt = sign_proposal(self.chain_id, msg.height, msg.round, msg.pol_round, blk,
                                v.secret)
        else:
            return  # byzantine nodes don't help others catch up
        for d in peers:
            push(now + 1, "msg", d, alt if d in half else msg)

    def _catch_up(self, v: Validator) -> None:
        """Replay committed blocks a validator missed (offline, or no local decision)."""
        while v.height < self.height:
            b = self.blocks[v.height + 1]
            if self.consensus == "bft" and b.commit and not b.commit.get("placeholder") and \
                    not verify_commit(b.commit, self.chain_id, v.chain_keys()):
                raise LocalnetError(f"invalid commit certificate at {b.header['height']}")
            with v.storage.transaction():
                root, _ = v.execute_block([Tx.from_json(t) for t in b.txs], b.header["time"],
                                          self.tiers, b.last_commit)
            if root != b.header["state_root"]:
                raise LocalnetError(f"{v.id[:12]} diverged replaying height "
                                    f"{b.header['height']}")

    # --- dev controls ---------------------------------------------------------
    def _need_dev(self) -> None:
        if not self.dev:
            raise LocalnetError("UNAUTHORIZED: dev-only method (start with --dev)")

    def warp(self, seconds: int) -> int:
        """Advance chain time; next block's time = time + block_time."""
        self._need_dev()
        if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds < 0:
            raise LocalnetError("seconds must be a non-negative int (time never goes backwards)")
        self.time += seconds
        self._save_clock()
        return self.time

    def set_time(self, ts: int) -> int:
        self._need_dev()
        if not isinstance(ts, int) or ts < self.time:
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
        if not isinstance(sid, str) or "/" in sid or ".." in sid or \
                not (self.dir / "snapshots" / sid / "state.json").exists():
            raise LocalnetError(f"unknown snapshot {sid!r}")
        d = self.dir / "snapshots" / sid
        st = json.loads((d / "state.json").read_text())
        for v, (on, byz, h) in zip(self.validators, st["validators"]):
            v.close()
            for suf in ("", "-wal", "-shm"):
                Path(str(v.db) + suf).unlink(missing_ok=True)
            shutil.copy(d / v.db.name, v.db)
            v.open()
            v.online, v.byzantine, v.height = on, byz, h
        shutil.copy(d / "blocks.jsonl", self.blocks_path)
        self._load_blocks()
        self.time = st["time"]
        self._save_clock()
        self.mempool = [Tx.from_json(t) for t in st["mempool"]]
        return self.status()

    def inject_fault(self, spec: dict) -> dict:
        """spec = {"validator": index, "kind": "offline"|"online"|"byzantine"|"honest"}."""
        self._need_dev()
        try:
            v = self.validators[int(spec["validator"])]
            kind = spec["kind"]
        except (KeyError, IndexError, ValueError, TypeError):
            raise LocalnetError("spec needs validator index and kind") from None
        if kind not in ("offline", "online", "byzantine", "honest"):
            raise LocalnetError(f"unknown fault kind {kind!r}")
        if kind in ("offline", "online"):
            v.online = kind == "online"
        else:
            v.byzantine = kind == "byzantine"
        return {"index": v.index, "id": v.id, "online": v.online, "byzantine": v.byzantine}

    def verify(self) -> bool:
        """Every honest synced validator's root matches head, its tree verifies, and (bft)
        every block carries a valid >2/3 commit certificate."""
        head = self.blocks[-1].header["state_root"]
        ok = all(str(v.ledger.root()) == head and v.ledger.verify_state()
                 for v in self.validators if v.online and not v.byzantine
                 and v.height == self.height)
        if self.consensus == "bft":
            for prev, b in zip(self.blocks, self.blocks[1:]):
                ok = ok and b.header["prev_hash"] == prev.hash
                if b.commit and not b.commit.get("placeholder"):
                    ok = ok and b.commit["block_hash"] == b.hash and verify_commit(
                        b.commit, self.chain_id, self.valset_at(b.header["height"]))
        return ok
