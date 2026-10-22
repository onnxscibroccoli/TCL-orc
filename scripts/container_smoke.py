#!/usr/bin/env python3
"""Actual rootless-container acceptance on a disposable CI host; no production keys."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from production.executor import canonical, execute

PROBE = '''import os, socket
from pathlib import Path
assert os.getuid() == 65532
status = Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\\t1' in status
assert int(next(x.split(':',1)[1] for x in status.splitlines() if x.startswith('CapEff:')),16) == 0
assert not Path('/var/run/docker.sock').exists()
for path in ['/workspace/forbidden','/forbidden']:
    try: Path(path).write_text('not allowed')
    except OSError: pass
    else: raise AssertionError('filesystem was writable: '+path)
s = socket.socket(); s.settimeout(0.5)
assert s.connect_ex(('1.1.1.1',443)) != 0
s.close()
assert Path('/run/secrets/probe').read_bytes() == b'synthetic-ci-secret'
assert int(Path('/sys/fs/cgroup/memory.max').read_text()) <= 128*1024*1024
assert int(Path('/sys/fs/cgroup/pids.max').read_text()) <= 32
quota,period=map(int,Path('/sys/fs/cgroup/cpu.max').read_text().split())
assert quota/period <= 1
'''


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='tcl-rootless-smoke-') as tmp:
        root=Path(tmp); source=root/'source';source.mkdir()
        (source/'probe.py').write_text(PROBE)
        def git(*argv):
            return subprocess.check_output(['git','-C',str(source),*argv],stderr=subprocess.DEVNULL)
        git('init');git('add','.');git('-c','user.name=CI','-c','user.email=ci@example.invalid','commit','-m','isolation fixture')
        key=Ed25519PrivateKey.generate(); secret=root/'secret';secret.write_bytes(b'synthetic-ci-secret');secret.chmod(0o444)
        plan={'version':1,'source_sha256':hashlib.sha256(git('archive','--format=tar','HEAD')).hexdigest(),'image':args.image,'resources':{'memory_mb':128,'cpus':1,'pids':32,'tmpfs_mb':16},'tasks':[{'id':'isolation','command':['python3','/workspace/probe.py'],'timeout':30,'secrets':['probe']}]}
        policy={'keys':{'ci':base64.b64encode(key.public_key().public_bytes_raw()).decode()},'images':[args.image],'commands':[['python3','/workspace/probe.py'],['python3','-c','import time; time.sleep(60)']],'secrets':{'probe':str(secret)},'limits':{'memory_mb':128,'cpus':1,'pids':32,'tmpfs_mb':16,'tasks':2,'timeout':30,'total_timeout':60}}
        def signed():
            return {'key_id':'ci','plan':plan,'signature':base64.b64encode(key.sign(b'TCL-EXECUTION-v1\n'+canonical(plan))).decode()}
        envelope=signed();report=execute(envelope,policy,source,root/'success.sqlite')
        assert report['provenance']['records'][0]['status']=='succeeded',report
        try: execute(envelope,policy,source,root/'success.sqlite')
        except ValueError: pass
        else: raise AssertionError('completed task replayed')
        plan['tasks']=[{'id':'timeout','command':['python3','-c','import time; time.sleep(60)'],'timeout':1}]
        timed=execute(signed(),policy,source,root/'timeout.sqlite')
        assert timed['provenance']['records'][0]['status']=='uncertain',timed
        assert not subprocess.check_output(['docker','ps','-aq','--filter','name=tcl-']).strip(),'container survived cleanup'
        # Secret location and value must not enter persisted provenance.
        final={'purpose':'CI isolation acceptance only','executor_sha256':hashlib.sha256((Path(__file__).resolve().parents[1]/'production/executor.py').read_bytes()).hexdigest(),'repository_revision':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),'docker_version':subprocess.check_output(['docker','version','--format','{{.Server.Version}}'],text=True).strip(),'success':report,'timeout':timed,'replay_rejected':True}
        encoded=json.dumps(final,sort_keys=True,indent=2)
        assert str(secret) not in encoded and 'synthetic-ci-secret' not in encoded
        output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(encoded+'\n')
        print('Rootless isolation, resources, secret mount, replay rejection and timeout cleanup verified.')

if __name__=='__main__':
    main()
