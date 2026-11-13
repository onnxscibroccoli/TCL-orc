"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../../../");
const adapterSource = fs.readFileSync(path.join(root, "android/vos/app/src/main/assets/host.js"), "utf8");
const ledgerSource = fs.readFileSync(path.join(root, "android/vos/app/src/main/java/org/tclorc/vos/GameKernel.java"), "utf8");

function createSandbox(native) {
  const sandbox = {};
  if (native) sandbox.TCLNative = native;
  vm.runInNewContext(adapterSource, sandbox, {filename:"host.js", timeout:2000});
  return sandbox;
}

function mockTransport() {
  const calls = [];
  return {
    calls,
    state() { calls.push(["state"]); return JSON.stringify({ok:true,state:{wallet:{credits:500}}}); },
    command(game,action,arg,id) {
      calls.push(["command",game,action,arg,id]);
      return JSON.stringify({ok:true,state:{wallet:{credits:484}}});
    },
    selectProfile(profile) {calls.push(["selectProfile",profile]);return JSON.stringify({ok:true,state:{profile}});},
    observeInteraction() {calls.push(["observeInteraction"]); }
  };
}

test("the same web client works with any conforming host, not just a Java bridge", () => {
  const android = mockTransport();
  const desktop = mockTransport();
  const embedded = createSandbox(android);
  assert.equal(embedded.VOSHost.version, "tcl-vos-host-v1");
  assert.equal(embedded.VOSHost.connect().invoke("state").state.wallet.credits, 500);
  const port = createSandbox().VOSHost.create(desktop);
  assert.equal(port.invoke("command","civitas","shift",0,"request_001").state.wallet.credits,484);
  port.observeInteraction();
  assert.deepEqual(desktop.calls.at(-1), ["observeInteraction"]);
  assert.equal(createSandbox().VOSHost.connect(), null);
});

test("reject unknown capabilities, malformed command envelopes, and oversized host responses", () => {
  const port = createSandbox().VOSHost.create(mockTransport());
  assert.throws(() => port.invoke("mint",1000), /capability/);
  assert.throws(() => port.invoke("command","nightdrive","tune",0,"bad"), /envelope/);
  assert.throws(() => port.invoke("command","nightdrive","move",1.5,"request_002"), /envelope/);
  assert.throws(() => port.invoke("selectProfile","../bad"), /profile/);
  assert.throws(() => port.invoke("state",1), /no arguments/);
  assert.throws(() => createSandbox().VOSHost.create({state(){}}), /unavailable/);
  const bad = mockTransport();
  bad.state = () => "x".repeat(150000);
  assert.throws(() => createSandbox().VOSHost.create(bad).invoke("state"), /oversized/);
});

test("game registration is engine-neutral and maps only to host-validated verbs", () => {
  const filenames = ["civitas.game.json","nightdrive.game.json"];
  const ids = new Set();
  for (const filename of filenames) {
    const manifest = JSON.parse(fs.readFileSync(path.join(__dirname,"../v1",filename),"utf8"));
    assert.equal(manifest.schema, "tcl-vos.game.v1");
    assert.equal(manifest.currency, "TCL-vOS-tau");
    assert.equal(manifest.wallet_scope, "host");
    assert.equal(manifest.accounting, "host-authoritative");
    assert.equal(manifest.telemetry, false);
    assert.equal(manifest.external_network, false);
    assert.ok(!ids.has(manifest.id));
    ids.add(manifest.id);
    assert.ok(ledgerSource.includes(`"${manifest.id}".equals(game)`), manifest.id);
    for (const action of manifest.actions) {
      assert.ok(ledgerSource.includes(`case "${action}":`), action);
    }
    assert.ok(manifest.permissions.includes("wallet.dispatch"));
    assert.equal(typeof manifest.engine, "string");
  }
  assert.deepEqual([...ids].sort(), ["civitas","nightdrive"]);
});

test("game assets load the portable adapter before the interface code", () => {
  const html = fs.readFileSync(path.join(root,"android/vos/app/src/main/assets/index.html"),"utf8");
  assert.ok(html.indexOf('src="host.js"') > -1);
  assert.ok(html.indexOf('src="host.js"') < html.indexOf('src="app.js"'));
  const android = fs.readFileSync(path.join(root,"android/vos/app/src/main/java/org/tclorc/vos/MainActivity.java"),"utf8");
  assert.ok(android.includes('"host.js"'));
  const ui = fs.readFileSync(path.join(root,"android/vos/app/src/main/assets/app.js"),"utf8");
  assert.ok(ui.includes("VOSHost.connect()"));
  assert.ok(!ui.includes("TCLNative[method]"));
});
