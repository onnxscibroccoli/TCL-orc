"""Chain-level native modules for the localnet: validator registry (enrolment, epochs, evidence
slashing), notes, governance. Pure & deterministic; state is a JSON dict committed into the block
header's aux_roots. Stdlib only.

Validator-set rules (docs/13, 13a):
  * enrol: module "consensus" method "enroll", signed by the candidate's account; MOCK personhood
    tier >= MIN_TIER (2) and exactly the flat VALIDATOR_BOND, debited from the sender into escrow.
  * evidence: module "consensus" method "evidence" carries two conflicting signed votes. Applied at
    BLOCK END (after all ordinary txs): bond slashed into slashed_pool, seat removed, personhood
    nullifier banned for good (it can never re-enrol, with any key).
  * epochs: at the end of every height h with h % epoch_blocks == 0 the registry advances one
    epoch: terms expire into cooldown and seats are refilled by a seeded draw. The seed comes
    from an ECVRF beacon (RFC 9381): seed_e = SHA256(seed_{e-1} || e || beta), where beta is the
    VRF output of the epoch's leader (drawn from seed_{e-1}) over seed_{e-1}. A VRF output is
    unique per (key, input), so neither the leader nor any block proposer can grind it; the
    only residual choice is the leader withholding, which yields a fixed fallback seed and costs
    the leader 10% of its bond plus its seat.
  * liveness override: if expiry would leave fewer seats than N and there are no other eligible
    candidates, just-expired members are re-seated (cooldown waived) by the same seeded draw
    rather than halting the chain. The override is recorded in the epoch event.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

from ..consensus.encoding import H
from ..consensus.evidence import BadEvidence, DuplicateVoteEvidence
from ..consensus.types import Vote
from ..consensus.validators import (Candidate, Reject as VReject, ValidatorParams,
                                    ValidatorRegistry, draw_without_replacement)
from . import groth16
from .bn254 import R as FIELD_P
from .encoding_helpers import anon_nullifier
from .governance import GovError, Governance, GovState
from .notes import NoteError, NotesModule, NotesState

VK_DIR = Path(__file__).with_name("vk")
_VKS: dict[str, groth16.VerifyingKey] = {}


def vk(name: str) -> groth16.VerifyingKey:
    if name not in _VKS:
        _VKS[name] = groth16.VerifyingKey.load(VK_DIR / f"{name}.json")
    return _VKS[name]


class ModuleReject(Exception):
    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(f"{code}: {msg}" if msg else code)
        self.code = code


class DictPersonhood:
    """MOCK personhood view over a {nullifier: tier} dict. Verifies no ZK proof."""
    MOCK = True

    def __init__(self, tiers: dict) -> None:
        self.tiers = tiers

    def tier(self, n: str) -> int:
        return self.tiers.get(n, 0)

    def is_enrolled(self, n: str) -> bool:
        return n in self.tiers


def evidence_from_args(a: dict) -> DuplicateVoteEvidence:
    try:
        va, vb = (Vote(d["kind"], d["height"], d["round"], d["block_hash"], d["validator"],
                       d["sig"]) for d in (a["a"], a["b"]))
    except (KeyError, TypeError):
        raise ModuleReject("BAD_ARGS", "evidence needs two votes") from None
    return DuplicateVoteEvidence(va, vb)


def evidence_args(ev: DuplicateVoteEvidence) -> dict:
    return {"a": ev.a.to_dict(), "b": ev.b.to_dict()}


class ChainState:
    """Wraps the JSON state dict. `persons` maps personhood nullifier -> tier (MOCK)."""

    def __init__(self, d: dict, chain_id: str, bank, verify_cache: Optional[dict] = None,
                 dev_proofs: bool = False) -> None:
        self.d, self.chain_id, self.bank = d, chain_id, bank
        self.dev_proofs = dev_proofs
        self._cache = verify_cache if verify_cache is not None else {}
        self.ph = DictPersonhood(d["persons"])
        self.reg = self._load_reg()
        self.gov = Governance(GovState(**d["gov"]), self.ph)
        ns = NotesState(**d["notes"])
        self.notes = NotesModule(ns, bank, self._verify_note)

    # --- genesis --------------------------------------------------------------------------
    @staticmethod
    def genesis(chain_id: str, validators: list[str], persons: dict, seats: int,
                epoch_blocks: int, term: int, cooldown: int, bond: int, unit: int = 1) -> dict:
        d: dict[str, Any] = {
            "persons": dict(persons),
            "reg": {"params": {"N_VALIDATORS": seats, "EPOCH_BLOCKS": epoch_blocks,
                               "TERM_EPOCHS": term, "COOLDOWN_EPOCHS": cooldown,
                               "VALIDATOR_BOND": bond, "MIN_TIER": 2},
                    "candidates": {}, "seats": {}, "cooldown_until": {}, "banned": [],
                    "slashed_pool": 0, "evidence_seen": [], "epoch": -1, "bonded": 0},
            "gov": GovState().to_json(),
            "notes": None, "anon_nullifiers": [], "valset_hist": [], "events": []}
        # validator i is person "val-person-i" (tier 2), pre-bonded from genesis allocation
        for i, key in enumerate(validators):
            n = f"val-person-{i}"
            d["persons"][n] = max(d["persons"].get(n, 0), 2)
            d["reg"]["candidates"][n] = {"key": key, "bond": bond}
            d["reg"]["bonded"] += bond
        d["notes"] = _genesis_notes(chain_id, unit)
        st = ChainState(d, chain_id, bank=None)
        seed0 = hashlib.sha256(b"tcl-genesis-seed" + chain_id.encode()).digest()
        st._advance(0, seed0, 0)
        st._new_beacon(0, seed0)
        st.save()
        return st.d

    # --- persistence ----------------------------------------------------------------------
    def _load_reg(self) -> ValidatorRegistry:
        r = self.d["reg"]
        reg = ValidatorRegistry(self.ph, ValidatorParams(**r["params"]))
        reg.candidates = {n: Candidate(n, c["key"], c["bond"])
                          for n, c in r["candidates"].items()}
        reg.seats = dict(r["seats"])
        reg.cooldown_until = dict(r["cooldown_until"])
        reg.banned = set(r["banned"])
        reg.slashed_pool = r["slashed_pool"]
        reg._evidence_seen = {tuple(k) for k in r["evidence_seen"]}
        reg.epoch = r["epoch"]
        return reg

    def save(self) -> dict:
        reg, r = self.reg, self.d["reg"]
        r["candidates"] = {n: {"key": c.key, "bond": c.bond}
                           for n, c in sorted(reg.candidates.items())}
        r["seats"], r["cooldown_until"] = dict(sorted(reg.seats.items())), \
            dict(sorted(reg.cooldown_until.items()))
        r["banned"] = sorted(reg.banned)
        r["slashed_pool"] = reg.slashed_pool
        r["evidence_seen"] = sorted(list(k) for k in reg._evidence_seen)
        r["epoch"] = reg.epoch
        self.d["gov"] = self.gov.s.to_json()
        self.d["notes"] = self.notes.s.to_json()
        return self.d

    def aux_roots(self) -> dict:
        self.save()
        d = self.d
        return {"personhood": H({"persons": d["persons"], "reg": d["reg"]}),
                "governance": H(d["gov"]), "notes": H(d["notes"]),
                "nullifiers": H(sorted(d["anon_nullifiers"]))}

    def validator_keys(self) -> list[str]:
        return self.reg.validator_keys()

    # --- proofs (each node verifies itself; results cached per node) ----------------------
    def _groth16(self, name: str, proof: Any, pub: list) -> bool:
        if self.dev_proofs and proof == {"dev": True}:
            return True  # DEV ONLY: genesis flag dev_proofs; default False (fail-closed)
        if not isinstance(proof, dict):
            return False
        k = H([name, proof, [str(x) for x in pub]])
        if k not in self._cache:
            self._cache[k] = groth16.verify(vk(name), proof, pub)
        return self._cache[k]

    def _verify_note(self, proof: dict, pub: list) -> bool:
        return self._groth16("note_deposit", proof, pub)

    def verify_anon(self, args: dict, proof: Any) -> int:
        """Anonymous state-transition tx: Groth16 proof over public signals [newRoot, fee,
        oldRoot, newTime]; the spend nullifier is bound to the proven statement."""
        pub = args.get("public")
        if self.dev_proofs and proof == {"dev": True}:
            nul = args.get("nullifier")
        else:
            if not isinstance(pub, list) or len(pub) != 4:
                raise ModuleReject("BAD_PROOF", "public signals required")
            nul = anon_nullifier(pub)
            if args.get("nullifier") != nul:
                raise ModuleReject("BAD_PROOF", "nullifier not bound to proven statement")
            if not self._groth16("state_transition", proof, pub):
                raise ModuleReject("BAD_PROOF", "Groth16 verification failed")
        if not isinstance(nul, int) or isinstance(nul, bool) or nul < 0:
            raise ModuleReject("BAD_ARGS", "nullifier must be a non-negative int")
        return nul

    # --- txs --------------------------------------------------------------------------------
    def apply(self, module: str, method: str, sender: Optional[str], a: dict, proof: Any,
              now: int) -> list[dict]:
        try:
            if module == "consensus":
                return self._consensus(method, sender, a)
            if module == "notes":
                return self._notes(method, sender, a, proof, now)
            if module == "governance":
                return self._gov(method, sender, a, now)
        except (VReject, NoteError, GovError) as e:
            raise ModuleReject(e.code, str(e)) from None
        raise ModuleReject("UNKNOWN_MODULE", module)

    def _consensus(self, method: str, sender: Optional[str], a: dict) -> list[dict]:
        if method == "enroll":
            if sender is None:
                raise ModuleReject("UNAUTHORIZED")
            n, key, bond = a.get("nullifier"), a.get("key"), a.get("bond")
            if n != f"person:{sender}":  # MOCK binding of account -> personhood nullifier
                raise ModuleReject("UNAUTHORIZED", "nullifier not bound to sender (mock)")
            if not isinstance(key, str) or len(key) != 64 or not isinstance(bond, int):
                raise ModuleReject("BAD_ARGS")
            if n in self.reg.candidates:
                raise ModuleReject("BAD_ARGS", "already enrolled")
            self.reg.opt_in(n, key, bond)       # tier, ban, flat-bond, key-uniqueness checks
            self.bank.debit(sender, bond)       # raises -> whole tx rolls back
            self.d["reg"]["bonded"] += bond
            return [{"type": "enroll", "nullifier": n, "key": key}]
        if method == "evidence":
            return []  # applied at block end (see end_block)
        if method == "beacon":
            beta = self.check_beacon(a)
            self.d["beacon"]["beta"] = beta.hex()
            return [{"type": "beacon_reveal", "epoch": a["epoch"], "beta": beta.hex()}]
        raise ModuleReject("UNKNOWN_METHOD", method)

    # --- randomness beacon (ECVRF, RFC 9381) ----------------------------------------------
    def beacon_alpha(self, epoch: int, seed_hex: str) -> bytes:
        return b"tcl-beacon" + self.chain_id.encode() + epoch.to_bytes(8, "big") + \
            bytes.fromhex(seed_hex)

    def _new_beacon(self, epoch: int, seed: bytes) -> None:
        keys = self.validator_keys()
        leader = draw_without_replacement(seed + b"leader", keys, 1)
        self.d["beacon"] = {"epoch": epoch, "seed": seed.hex(), "beta": None,
                            "leader": leader[0] if leader else None}

    def beacon_pending(self) -> Optional[tuple]:
        b = self.d["beacon"]
        if b["beta"] is None and b["leader"]:
            return b["leader"], b["epoch"], self.beacon_alpha(b["epoch"], b["seed"])
        return None

    def check_beacon(self, a: dict) -> bytes:
        from ..consensus import vrf
        b = self.d["beacon"]
        if a.get("epoch") != b["epoch"] or b["beta"] is not None or not b["leader"]:
            raise ModuleReject("BAD_ARGS", "no beacon reveal pending for that epoch")
        try:
            pi = bytes.fromhex(a.get("pi", ""))
        except (TypeError, ValueError):
            raise ModuleReject("BAD_PROOF") from None
        beta = vrf.verify(bytes.fromhex(b["leader"]), self.beacon_alpha(b["epoch"], b["seed"]), pi)
        if beta is None:
            raise ModuleReject("BAD_PROOF", "VRF proof invalid")
        return beta

    def _next_seed(self, epoch: int) -> tuple[bytes, list]:
        """seed_{e} = SHA256(seed_{e-1} || e || beta_leader). Missed reveal: fallback seed and
        the leader is penalised (10% bond slashed, seat lost)."""
        b, ev = self.d["beacon"], []
        prev = bytes.fromhex(b["seed"])
        if b["beta"] is not None:
            return hashlib.sha256(prev + epoch.to_bytes(8, "big") +
                                  bytes.fromhex(b["beta"])).digest(), ev
        if b["leader"]:
            n = self.reg.nullifier_of(b["leader"])
            if n is not None:
                c = self.reg.candidates[n]
                cut = c.bond // 10
                c.bond -= cut
                self.reg.slashed_pool += cut
                self.reg.seats.pop(n, None)
                ev.append({"type": "beacon_missed", "validator": b["leader"], "slashed": cut})
        return hashlib.sha256(prev + epoch.to_bytes(8, "big") + b"missed").digest(), ev

    def check_evidence(self, a: dict) -> DuplicateVoteEvidence:
        ev = evidence_from_args(a)
        try:
            off = ev.verify(self.chain_id)
        except BadEvidence as e:
            raise ModuleReject("BAD_PROOF", str(e)) from None
        if ev.key() in self.reg._evidence_seen:
            raise ModuleReject("NULLIFIER_SPENT", "evidence already processed")
        if self.reg.nullifier_of(off) is None:
            raise ModuleReject("BAD_ARGS", "unknown validator")
        return ev

    def _notes(self, method: str, sender: Optional[str], a: dict, proof: Any, now: int) -> list[dict]:
        if sender is None and method != "deposit":
            raise ModuleReject("UNAUTHORIZED")
        m = self.notes
        self._sync_note_policy()
        if method == "mint":
            c, r = m.mint(sender, a.get("id"), a.get("denom"), a.get("cost", 0), now)
            return [{"type": "note_mint", "id": str(a["id"]), "cost": c, "reward": r}]
        if method == "rearm":
            c, r = m.rearm(sender, a.get("id"), a.get("cost", 0), now)
            return [{"type": "note_rearm", "id": str(a["id"]), "cost": c, "reward": r}]
        if method == "fund_pool":
            m.fund_pool(sender, a.get("amount"))
            return [{"type": "pool_funded", "amount": a["amount"]}]
        if method == "deposit":
            rcpt = a.get("recipient")
            if not isinstance(rcpt, str) or not rcpt:
                raise ModuleReject("BAD_ARGS")
            imm, lk = m.deposit(a.get("id"), a.get("nullifier"), rcpt, proof, now)
            return [{"type": "note_deposit", "id": str(a["id"]), "immediate": imm,
                     "locked": lk}]
        if method == "release":
            return [{"type": "released", "amount": m.release(sender, now)}]
        raise ModuleReject("UNKNOWN_METHOD", method)

    def _sync_note_policy(self) -> None:
        """Policy is read from the native registry each call (as NoteController.registryPolicy);
        any failure fails closed to zero credit/zero reward (pure 1:1)."""
        g, u = self.gov, self.notes.s.unit
        try:
            self.notes.set_cost_policy(g.get("COST_CAP_BPS"), g.get("COST_CAP_ABS_MILLI") * u // 1000,
                                       g.get("SMALL_BONUS_MILLI") * u // 1000,
                                       g.get("SMALL_RESERVE_BPS"), g.get("MIN_CIRCULATION"),
                                       reward_bps=g.get("MINT_REWARD_BPS"))
        except (KeyError, NoteError):
            self.notes.s.policy = {}

    def demurrage_rule(self):
        """Ledger decay rule in force for this epoch (snapshotted at the last epoch boundary)."""
        from . import demurrage
        a = self.d.get("demurrage_active") or {}
        return demurrage.make_rule(a.get("rate", demurrage.DEFAULT_PPM),
                                   a.get("threshold", demurrage.DEFAULT_THRESHOLD))

    def _gov(self, method: str, sender: Optional[str], a: dict, now: int) -> list[dict]:
        if sender is None:
            raise ModuleReject("UNAUTHORIZED")
        g, person = self.gov, f"person:{sender}"
        persons = sum(1 for t in self.ph.tiers.values() if t >= 1)
        if method == "propose":
            return [{"type": "proposed", "id": g.propose(a.get("key"), a.get("value"), now)}]
        if method == "vote":
            g.vote(a.get("id"), person, bool(a.get("against", False)), now)
            return [{"type": "voted", "id": a.get("id")}]
        if method == "withdraw":
            g.withdraw(a.get("id"), person, now)
            return [{"type": "withdrawn", "id": a.get("id")}]
        if method == "queue":
            return [{"type": "queued", "eta": g.queue(a.get("id"), now, persons)}]
        if method == "execute":
            g.execute(a.get("id"), now)
            return [{"type": "executed", "id": a.get("id")}]
        raise ModuleReject("UNKNOWN_METHOD", method)

    # --- block end --------------------------------------------------------------------------
    def end_block(self, height: int, evidence: list[dict], last_commit: Optional[dict]) -> list:
        out = []
        for a in evidence:  # already checked when the tx executed; re-check fail-closed
            try:
                ev = self.check_evidence(a)
            except ModuleReject:
                continue
            n = self.reg.submit_evidence(ev, self.chain_id)
            out.append({"type": "slashed", "nullifier": n, "validator": ev.a.validator,
                        "bond": self.reg.params.VALIDATOR_BOND, "banned": True})
        eb = self.reg.params.EPOCH_BLOCKS
        if eb > 0 and height > 0 and height % eb == 0:
            e = height // eb
            seed, missed = self._next_seed(e)
            out += missed
            out.append(self._advance(e, seed, height))
            self._new_beacon(e, seed)
            # governed demurrage takes effect from the next epoch (never mid-epoch)
            from ..ledger.demurrage_fixed import SCALE
            act = {"rate": self.gov.get("DEMURRAGE_RATE_PPM_DAY"),
                   "threshold": self.gov.get("DEMURRAGE_THRESHOLD_TCL") * SCALE}
            if act != self.d.get("demurrage_active"):
                self.d["demurrage_active"] = act
                out.append({"type": "demurrage_params", **act})
        self.save()
        keys = self.validator_keys()
        hist = self.d["valset_hist"]
        if not hist or hist[-1][1] != keys:
            hist.append([height + 1, keys])
        return out

    def _advance(self, epoch: int, seed: bytes, height: int) -> dict:
        reg = self.reg
        before = set(reg.seats)
        reg.advance_epoch(epoch, seed)
        override = []
        short = reg.params.N_VALIDATORS - len(reg.seats)
        if short > 0:
            expired = sorted(n for n in before - set(reg.seats)
                             if n not in reg.banned and n in reg.candidates)
            override = draw_without_replacement(seed + b"override", expired, short)
            for n in override:
                reg.seats[n] = epoch
                reg.cooldown_until.pop(n, None)
        self.save()
        if height == 0:
            self.d["valset_hist"] = [[0, self.validator_keys()]]
        return {"type": "epoch", "epoch": epoch, "seed": seed.hex(),
                "seated": sorted(set(reg.seats) - before),
                "rotated_out": sorted(before - set(reg.seats)), "override": override}


def _genesis_notes(chain_id: str, unit: int) -> dict:
    class _Null:
        def debit(self, *_): ...
        def credit(self, *_): ...
    ctx = int(H(["tcl-notes-context", chain_id]), 16) % FIELD_P
    m = NotesModule.genesis(unit, ctx, _Null(), lambda p, s: False)
    return m.s.to_json()


