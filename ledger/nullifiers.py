"""Local nullifier index; contract commitment consumption remains authoritative.

Pass an operator database path and deployment domain for restart durability.
No proof verification or balance release is performed by this index.
"""
from src.tcl.storage.db import StateStore, Conflict, label

FIELD=21888242871839275222246405745257275088548364400416034343698204186575808495617


class NullifierSet:
    def __init__(self,path=None,*,domain='local-test'):
        label(domain)
        self._namespace='nullifiers:'+domain
        label(self._namespace)
        self._spent=set()
        self._store=StateStore(path) if path is not None else None
        if self._store:self._store.verify()
    @staticmethod
    def _key(nullifier):
        if type(nullifier) is not int or not 0<=nullifier<FIELD:raise ValueError('canonical field element required')
        return str(nullifier)
    def is_spent(self,nullifier):
        key=self._key(nullifier)
        return self._store.get(self._namespace,key) is not None if self._store else key in self._spent
    def mark_spent(self,nullifier):
        key=self._key(nullifier)
        if self._store:
            self._store.claim(self._namespace,key,True)
        else:
            if key in self._spent:raise Conflict('nullifier already spent')
            self._spent.add(key)
    def close(self):
        if self._store:self._store.db.close()
