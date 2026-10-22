"""Atomic operator journal, not a Merkle tree or an on-chain settlement proof."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ZERO='0'*64


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)


def label(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:/-]{1,256}',value):
        raise ValueError('invalid namespace or key')


class Conflict(ValueError):
    """Expected head changed or a claimed key already exists."""


class StateStore:
    def __init__(self,path):
        path=Path(path)
        path.parent.mkdir(parents=True,exist_ok=True)
        if any(Path(str(path)+suffix).is_symlink() for suffix in ('','-wal','-shm')):
            raise ValueError('database paths cannot be symlinks')
        self.db=sqlite3.connect(path,timeout=5,isolation_level=None)
        self.db.execute('PRAGMA busy_timeout=5000')
        if self.db.execute('PRAGMA journal_mode=WAL').fetchone()[0]!='wal':
            self.db.close();raise ValueError('WAL required')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS kv(namespace TEXT,key TEXT,value TEXT NOT NULL,PRIMARY KEY(namespace,key))')
        self.db.execute('CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,namespace TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,previous TEXT NOT NULL,hash TEXT NOT NULL UNIQUE)')
        version=self.db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0,1):self.db.close();raise ValueError('unsupported storage schema')
        self.db.execute('PRAGMA user_version=1')

    def __enter__(self):return self
    def __exit__(self,*args):self.db.close()
    def head(self):
        row=self.db.execute('SELECT hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
        return row[0] if row else ZERO
    def get(self,namespace,key):
        label(namespace);label(key)
        row=self.db.execute('SELECT value FROM kv WHERE namespace=? AND key=?',(namespace,key)).fetchone()
        return json.loads(row[0]) if row else None
    def snapshot(self):
        return [(n,k,json.loads(v)) for n,k,v in self.db.execute('SELECT namespace,key,value FROM kv ORDER BY namespace,key')]
    def apply(self,changes,expected_head,*,create_only=False):
        if not isinstance(expected_head,str) or not re.fullmatch('[a-f0-9]{64}',expected_head):raise ValueError('expected head required')
        if not isinstance(changes,list) or not 0<len(changes)<=10000:raise ValueError('bounded nonempty batch required')
        prepared=[];seen=set()
        for namespace,key,value in changes:
            label(namespace);label(key)
            if (namespace,key) in seen:raise ValueError('duplicate batch key')
            seen.add((namespace,key));encoded=canonical(value)
            if len(encoded.encode())>1048576:raise ValueError('state value too large')
            prepared.append((namespace,key,encoded))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            previous=self.head()
            if previous!=expected_head:raise Conflict('stale state head')
            seq=self.db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
            for namespace,key,value in prepared:
                if create_only:
                    self.db.execute('INSERT INTO kv VALUES (?,?,?)',(namespace,key,value))
                else:
                    self.db.execute('INSERT INTO kv VALUES (?,?,?) ON CONFLICT(namespace,key) DO UPDATE SET value=excluded.value',(namespace,key,value))
                seq+=1
                digest=hashlib.sha256(canonical([seq,namespace,key,value,previous]).encode()).hexdigest()
                self.db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',(seq,namespace,key,value,previous,digest))
                previous=digest
            self.db.execute('COMMIT');return previous
        except BaseException as error:
            self.db.execute('ROLLBACK')
            if isinstance(error,sqlite3.IntegrityError):raise Conflict('key already claimed') from error
            raise
    def claim(self,namespace,key,value):
        return self.apply([(namespace,key,value)],self.head(),create_only=True)
    def verified_snapshot(self):
        self.db.execute("BEGIN")
        try:
            head=self._verify()
            return {"event":"snapshot","head":head,"state":self.snapshot()}
        finally:self.db.execute("ROLLBACK")
    def verify(self,expected_head=None):
        self.db.execute("BEGIN")
        try:return self._verify(expected_head)
        finally:self.db.execute("ROLLBACK")
    def _verify(self,expected_head=None):
        if self.db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('database integrity failure')
        previous=ZERO;state={};index=0
        for seq,namespace,key,value,parent,digest in self.db.execute('SELECT * FROM events ORDER BY seq'):
            index+=1
            computed=hashlib.sha256(canonical([seq,namespace,key,value,previous]).encode()).hexdigest()
            if seq!=index or parent!=previous or digest!=computed:raise ValueError('journal hash mismatch')
            label(namespace);label(key)
            if canonical(json.loads(value))!=value:raise ValueError('noncanonical journal value')
            state[(namespace,key)]=value;previous=digest
        actual={(n,k):v for n,k,v in self.db.execute('SELECT namespace,key,value FROM kv')}
        if actual!=state or (expected_head is not None and expected_head!=previous):raise ValueError('state or trusted head mismatch')
        return previous
