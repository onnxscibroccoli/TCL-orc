"use strict";
// Cross-runtime bit-exact Q32.32 conformance. Requires only Node + Python.
const fs = require("node:fs");
const cp = require("node:child_process");
const path = require("node:path");
const source = process.env.TCL_VOS_Q32_WASM || path.join(__dirname, "q32.wasm");
const python = process.env.TCL_VOS_PYTHON || "python3";
const reference = cp.execFileSync(python, ["-m", "tcl.vos.fixedpoint"],
  {encoding: "utf8", maxBuffer: 1024 * 1024});
const cases = JSON.parse(reference);

function assertEq(actual, expected, label) {
  if (actual.toString() !== expected) throw new Error(`${label}: ${actual} != ${expected}`);
}

(async () => {
  const bytes = fs.readFileSync(source);
  if (bytes.length > 16 * 1024) throw new Error("WASM guest binary size exceeded");
  const {module, instance} = await WebAssembly.instantiate(bytes, {});
  if (WebAssembly.Module.imports(module).length) throw new Error("Host imports denied");
  for (const n of Object.keys(instance.exports)) {
    if (!["memory", "q32_add", "q32_mul", "q32_velocity", "q32_position"].includes(n)) {
      throw new Error(`Unrecognized WASM export ${n}`);
    }
  }
  for (const [idx, c] of cases.entries()) {
    const [p,v,a,dt] = [c.p,c.v,c.a,c.dt].map(BigInt);
    assertEq(instance.exports.q32_add(p, v), c.sum, `add ${idx}`);
    assertEq(instance.exports.q32_mul(p, v), c.product, `mul ${idx}`);
    assertEq(instance.exports.q32_velocity(v,a,dt), c.velocity, `velocity ${idx}`);
    assertEq(instance.exports.q32_position(p,v,a,dt), c.position, `position ${idx}`);
  }
  console.log(`Q32.32 WASM/Python parity PASS (${cases.length} vectors, zero imports)`);
})().catch(e => {console.error(e); process.exitCode = 1;});
