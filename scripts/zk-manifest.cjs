const fs=require('node:fs');
const crypto=require('node:crypto');
const files=['circuits/puf_verify.circom','package-lock.json','build/zk/puf_verify.r1cs','build/zk/puf_verify_js/puf_verify.wasm','build/zk/circuit.zkey','build/zk/verification_key.json','build/zk/PufVerifier.sol'];
const manifest={purpose:'LOCAL TEST ONLY — NOT PRODUCTION CEREMONY',publicSignals:['commitment','nullifier','value','recipient','context'],sha256:Object.fromEntries(files.map(path=>[path,crypto.createHash('sha256').update(fs.readFileSync(path)).digest('hex')]))};
fs.writeFileSync('build/zk/manifest.json',JSON.stringify(manifest,null,2)+'\n');
