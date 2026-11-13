"use strict";
/* Fixed-asset WASM pilot only: no third-party guest APIs or general WASI.
 * Budget is enforced by the browser host terminating this Worker.
 * Market and ledger transitions are never executed by guest bytecode.
 */
const Q = 1n << 32n;
const ERR = -(1n << 63n);

async function score(req) {
  if (!Array.isArray(req.bytes) || req.bytes.length > 65536 ||
      !Number.isInteger(req.input) || req.input < 0 || req.input > 10000) {
    throw new Error("Invalid bounded score request");
  }
  const mod = await WebAssembly.compile(Uint8Array.from(req.bytes));
  if (WebAssembly.Module.imports(mod).length) throw new Error("Guest imports denied");
  const wasm = await WebAssembly.instantiate(mod, {});
  if (Object.keys(wasm.exports).some(k => k !== "score") ||
      typeof wasm.exports.score !== "function") {
    throw new Error("Score guest exports only score()");
  }
  const output = wasm.exports.score(req.input);
  if (!Number.isInteger(output)) throw new Error("Integer result required");
  return {score: output};
}

async function motion(req) {
  if (!Number.isInteger(req.throttle) || req.throttle < -2 || req.throttle > 2 ||
      !Number.isInteger(req.steps) || req.steps < 1 || req.steps > 6 ||
      typeof req.position !== "string" || typeof req.velocity !== "string" ||
      !/^-?\d{1,19}$/.test(req.position) || !/^-?\d{1,19}$/.test(req.velocity)) {
    throw new Error("Invalid Q32.32 guest input");
  }
  const response = await fetch("/q32.wasm", {cache:"no-store"});
  if (!response.ok) throw new Error("Fixed-point guest not installed");
  const bytes = await response.arrayBuffer();
  if (bytes.byteLength > 16384) throw new Error("Guest binary size exceeded");
  const mod = await WebAssembly.compile(bytes);
  if (WebAssembly.Module.imports(mod).length !== 0) throw new Error("Guest imports denied");
  const guest = await WebAssembly.instantiate(mod, {});
  const allowed = ["memory","q32_add","q32_mul","q32_velocity","q32_position"];
  if (Object.keys(guest.exports).some(key => !allowed.includes(key))) {
    throw new Error("Unrecognized Q32 guest export");
  }
  for (const method of allowed.slice(1)) {
    if (typeof guest.exports[method] !== "function") {
      throw new Error("Incomplete Q32 guest ABI");
    }
  }
  let p = BigInt(req.position);
  let v = BigInt(req.velocity);
  const a = (BigInt(req.throttle) * Q) / 4n;
  const limit = 8n * Q;
  let deliveries = 0;
  for (let i=0; i<req.steps; i++) {
    const nextV = guest.exports.q32_velocity(v, a, Q);
    if (nextV === ERR) throw new Error("Q32 overflow: velocity");
    const nextP = guest.exports.q32_position(p, v, a, Q);
    if (nextP === ERR) throw new Error("Q32 overflow: position");
    p = nextP; v = nextV;
    if (p < 0n) {p=0n;v=0n;}
    else if (p >= limit) {p=0n;v=0n;deliveries++;}
    else if (v > 3n * Q) v=3n * Q;
    else if (v < -3n * Q) v=-3n * Q;
  }
  return {position_q32:p.toString(), velocity_q32:v.toString(), deliveries};
}

self.onmessage = async event => {
  const req = event.data;
  try {
    if (!req || typeof req !== "object") throw new Error("Invalid guest request");
    const result = req.op === "score" ? await score(req) :
      req.op === "motion" ? await motion(req) : null;
    if (!result) throw new Error("Unknown guest capability");
    self.postMessage({ok:true, ...result});
  } catch (err) {
    self.postMessage({ok:false,error:String(err.message || err)});
  }
};
