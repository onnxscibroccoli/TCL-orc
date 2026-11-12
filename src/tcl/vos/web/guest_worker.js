"use strict";
/*
 * WebAssembly guest execution plane for demonstrative, pure computation only.
 * No WASI, network APIs, filesystem, camera, ledger, or host function imports.
 * The browser host terminates this worker on a 500ms deadline; these controls
 * do not constitute a reviewed sandbox for arbitrary community game code.
 */
self.onmessage = async event => {
  const req = event.data;
  try {
    if (!req || req.op !== "score" || !Array.isArray(req.bytes) || req.bytes.length > 65536 ||
        !Number.isInteger(req.input) || req.input < 0 || req.input > 10000) {
      throw new Error("Invalid bounded guest request");
    }
    const bytes = Uint8Array.from(req.bytes);
    const module = await WebAssembly.compile(bytes);
    if (WebAssembly.Module.imports(module).length !== 0) {
      throw new Error("Imports denied: guest has no host capabilities");
    }
    const guest = await WebAssembly.instantiate(module, {});
    if (Object.keys(guest.exports).some(name => name !== "score")) {
      throw new Error("Guest may export only score()");
    }
    if (typeof guest.exports.score !== "function") {
      throw new Error("Guest must export score(i32)->i32");
    }
    const output = guest.exports.score(req.input);
    if (!Number.isInteger(output)) throw new Error("Guest output must be integer");
    self.postMessage({ok:true,score:output});
  } catch (err) {self.postMessage({ok:false,error:String(err.message || err)});}
};

