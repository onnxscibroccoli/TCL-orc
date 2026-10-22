import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from src.tcl.storage.db import StateStore, Conflict


class StoreTests(unittest.TestCase):
    def test_atomic_batch_and_reopen(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.db'
            with StateStore(p) as store:
                root=store.apply([('balance','alice',{'amount':'1000'}),('balance','bob',{'amount':'0'})],store.head())
                self.assertEqual(store.verify(),root)
                with self.assertRaises(ValueError):store.apply([('balance','alice',1),('balance','alice',2)],root)
                self.assertEqual(store.get('balance','alice'),{'amount':'1000'})
            with StateStore(p) as store:self.assertEqual(store.verify(),root)

    def test_stale_writer_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.db'
            with StateStore(p) as s:head=s.head()
            barrier=threading.Barrier(2);results=[]
            def write(key):
                with StateStore(p) as s:
                    barrier.wait()
                    try:s.apply([('tasks',key,1)],head);results.append('ok')
                    except Conflict:results.append('conflict')
            threads=[threading.Thread(target=write,args=(str(i),)) for i in range(2)]
            for t in threads:t.start()
            for t in threads:t.join()
            self.assertCountEqual(results,['ok','conflict'])

    def test_corruption_detected(self):
        with tempfile.TemporaryDirectory() as d:
            with StateStore(Path(d)/'state.db') as s:
                s.apply([('notes','a',1)],s.head())
                s.db.execute("UPDATE kv SET value='2'");s.db.commit()
                with self.assertRaises(ValueError):s.verify()

    def test_sigkill_recovers_thousand_committed_mutations(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.db'
            program="from src.tcl.storage.db import StateStore;import os,signal;from pathlib import Path;s=StateStore(Path(__import__('sys').argv[1]));h=s.apply([('balances',str(i),{'amount':str(i)}) for i in range(1000)],s.head());print(h,flush=True);os.kill(os.getpid(),signal.SIGKILL)"
            result=subprocess.run([sys.executable,'-c',program,str(p)],capture_output=True,text=True)
            self.assertEqual(result.returncode,-signal.SIGKILL)
            with StateStore(p) as s:
                self.assertEqual(s.verify(),result.stdout.strip())
                self.assertEqual(s.get('balances','999'),{'amount':'999'})
                self.assertEqual(len(s.snapshot()),1000)

    def test_claim_is_persistent_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.db'
            with StateStore(p) as s:s.claim('nullifiers','4',True)
            with StateStore(p) as s:
                with self.assertRaises(Conflict):s.claim('nullifiers','4',True)
                self.assertTrue(s.get('nullifiers','4'));s.verify()

    def test_persistent_nullifier_domains_and_canonical_inputs(self):
        from ledger.nullifiers import NullifierSet, FIELD
        with self.assertRaises(ValueError):NullifierSet(domain='x'*246)
        NullifierSet(domain='x'*245).close()
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.db';s=NullifierSet(p,domain='chain:1:controller:abc');s.mark_spent(4);s.close()
            s=NullifierSet(p,domain='chain:1:controller:abc')
            self.assertTrue(s.is_spent(4))
            with self.assertRaises(ValueError):s.mark_spent(4)
            for bad in [-1,FIELD,True,'4']:
                with self.assertRaises(ValueError):s.mark_spent(bad)
            s.close();s=NullifierSet(p,domain='chain:2:controller:abc');self.assertFalse(s.is_spent(4));s.close()

    def test_cli_atomic_apply_and_trusted_head(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.db';batch=Path(d)/'batch.json';batch.write_text(json.dumps([['tasks','a',{'status':'PENDING'}]]))
            command=[sys.executable,'bin/tcl-cli','--state',str(p)]
            initial=json.loads(subprocess.check_output(command+['health'],text=True))['head']
            result=json.loads(subprocess.check_output(command+['apply','--batch',str(batch),'--expected-head',initial],text=True))
            snapshot=json.loads(subprocess.check_output(command+['snapshot'],text=True));self.assertEqual(snapshot['head'],result['head'])
            self.assertEqual(snapshot['state'],[['tasks','a',{'status':'PENDING'}]])
            failure=subprocess.run(command+['verify','--expected-head',initial],capture_output=True,text=True)
            self.assertEqual(failure.returncode,2);self.assertEqual(json.loads(failure.stdout)['event'],'rejected')
            bad=subprocess.run(command+['invalid-command'],capture_output=True,text=True)
            self.assertEqual(bad.returncode,2);self.assertEqual(json.loads(bad.stdout)['event'],'rejected')

    def test_sigkill_rolls_back_uncommitted_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'state.db'
            with StateStore(p) as s:head=s.apply([('balance','a',1)],s.head())
            program="from src.tcl.storage.db import StateStore;import os,signal,sys;s=StateStore(sys.argv[1]);s.db.execute('BEGIN IMMEDIATE');s.db.execute(\"UPDATE kv SET value='999'\");os.kill(os.getpid(),signal.SIGKILL)"
            result=subprocess.run([sys.executable,'-c',program,str(p)])
            self.assertEqual(result.returncode,-signal.SIGKILL)
            with StateStore(p) as s:self.assertEqual(s.verify(head),head);self.assertEqual(s.get('balance','a'),1)
