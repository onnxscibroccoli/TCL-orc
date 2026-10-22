#!/usr/bin/env bash
# Synthetic operational acceptance only. No production secrets or signing keys.
set -Eeuo pipefail
python3 - <<'PY'
import os, socket
from pathlib import Path
assert os.getuid() == 65532
status=Path('/proc/self/status').read_text()
assert 'NoNewPrivs:\t1' in status
assert int(next(x.split(':',1)[1] for x in status.splitlines() if x.startswith('CapEff:')),16)==0
assert not Path('/var/run/docker.sock').exists()
try: Path('/forbidden').write_text('forbidden')
except OSError: pass
else: raise AssertionError('root filesystem is writable')
s=socket.socket();s.settimeout(.5)
assert s.connect_ex(('1.1.1.1',443))!=0
s.close()
assert int(Path('/sys/fs/cgroup/memory.max').read_text())<=4*1024**3
assert int(Path('/sys/fs/cgroup/pids.max').read_text())<=512
quota,period=map(int,Path('/sys/fs/cgroup/cpu.max').read_text().split())
assert quota/period<=2
print('Actual protocol-container isolation and resource probes passed.',flush=True)
PY
cp -R /opt/protocol/. /work/
cd /work
npm run check
cp build/audit/contracts.json /evidence/contracts.json
cp build/zk/manifest.json /evidence/zk-manifest.json
cp build/zk/verification_key.json /evidence/verification_key.json
cp build/zk/PufVerifier.sol /evidence/PufVerifier.sol
cp build/zk/circuit.zkey /evidence/LOCAL-TEST-ONLY.zkey
cp build/zk/pot-final.ptau /evidence/LOCAL-TEST-ONLY.ptau
cp build/zk/puf_verify.r1cs /evidence/puf_verify.r1cs
cp build/zk/puf_verify_js/puf_verify.wasm /evidence/puf_verify.wasm
pip freeze > /evidence/python-packages.txt
node --version > /evidence/node-version.txt
