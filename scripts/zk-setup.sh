#!/usr/bin/env bash
# LOCAL DEVELOPMENT ONLY: fresh single-party ceremony, never production parameters.
set -Eeuo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/zk
./node_modules/.bin/circom2 circuits/puf_verify.circom --r1cs --wasm --sym -l node_modules -o build/zk
SNARK=./node_modules/.bin/snarkjs
"$SNARK" powersoftau new bn128 11 build/zk/pot0.ptau
"$SNARK" powersoftau contribute build/zk/pot0.ptau build/zk/pot1.ptau --name='LOCAL TEST ONLY' -e="$(node -e 'process.stdout.write(require("crypto").randomBytes(64).toString("hex"))')"
"$SNARK" powersoftau prepare phase2 build/zk/pot1.ptau build/zk/pot-final.ptau
"$SNARK" groth16 setup build/zk/puf_verify.r1cs build/zk/pot-final.ptau build/zk/circuit0.zkey
"$SNARK" zkey contribute build/zk/circuit0.zkey build/zk/circuit.zkey --name='LOCAL TEST ONLY' -e="$(node -e 'process.stdout.write(require("crypto").randomBytes(64).toString("hex"))')"
"$SNARK" zkey verify build/zk/puf_verify.r1cs build/zk/pot-final.ptau build/zk/circuit.zkey
"$SNARK" zkey export verificationkey build/zk/circuit.zkey build/zk/verification_key.json
"$SNARK" zkey export solidityverifier build/zk/circuit.zkey build/zk/PufVerifier.sol
node scripts/zk-manifest.cjs
