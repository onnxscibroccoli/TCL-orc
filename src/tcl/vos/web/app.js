"use strict";
const $ = id => document.getElementById(id);
const formatted = n => Number(n).toLocaleString("en-US");
const node = (tag, text, cls = "") => { const e = document.createElement(tag); e.textContent = text; if(cls) e.className = cls; return e; };
let world = null;
let actor = "alice";
const notice = (message, error = false) => { $("notice").textContent = message; $("notice").classList.toggle("error", error); };
async function load() {
  try {
    const r = await fetch(`/api/state?actor=${encodeURIComponent(actor)}`, {cache:"no-store"});
    if (!r.ok) throw new Error("City server unavailable");
    world = await r.json();
    render();
  } catch (e) {notice(`Cannot load the city: ${e.message}`, true);}
}
async function action(kind, params = {}) {
  try {
    const r = await fetch("/api/commands", {method:"POST", headers:{"Content-Type":"application/json"},
      body:JSON.stringify({game:"civitas99",action:kind,actor,params})});
    const data = await r.json();
    if(!r.ok || !data.ok) throw new Error(data.error || "Action failed");
    const result = data.result;
    notice(kind === "order" ? `Order #${result.order_id} placed: ${result.filled} filled, ${result.remaining} resting.` :
      kind === "work" ? `${result.work} complete. +${result.units} ${result.resource}; reputation ${result.reputation}.` :
      kind === "contribute" ? `Project ${result.project}: +${result.donated} ${result.resource}${result.complete ? ". PROJECT COMPLETE!" : "."}` :
      kind === "join" ? `Welcome to Civitas-99, ${actor}. Your first shift is ready.` :
      kind === "advance" ? `Turn ${result.turn} is now active.` : `Order #${result.cancelled} canceled.`);
    await load();
  } catch(e){notice(e.message,true);}
}
function renderOrders(which, orders) {
  const root = $(which); root.replaceChildren();
  if (!orders.length) {root.append(node("p", "No open orders", "empty"));return;}
  for (const o of orders) {
    const row = node("div", "", "order");
    row.append(node("span", `${o.price} CIV`), node("span", `×${o.remaining}`));
    if (o.owner === actor) {
      const b = node("button", "Cancel"); b.type="button";
      b.setAttribute("aria-label", `Cancel order ${o.order_id}`);
      b.addEventListener("click", () => action("cancel", {order_id:o.order_id})); row.append(b);
    } else row.append(node("span", o.owner));
    root.append(row);
  }
}
function render() {
  if (!world) return;
  const p = world.player;
  $("turn").textContent = `TURN ${String(world.turn).padStart(3,"0")}`;
  $("population").textContent = `${world.players} CITIZEN${world.players === 1 ? "" : "S"}`;
  $("event-count").textContent = `${world.os.events} EVENTS`;
  $("credits").textContent = p ? formatted(p.credits) : "—";
  for (const r of ["energy","water","alloy"]) $(r).textContent = p ? formatted(p.inventory[r]) : "—";
  $("reputation").textContent = p ? formatted(p.reputation) : "—";
  $("shift-status").textContent = p && p.worked_this_turn ? "SHIFT COMPLETE / ADVANCE TURN" : "WORK SHIFT AVAILABLE";
  for(const b of document.querySelectorAll("[data-work]")) b.disabled = !p || p.worked_this_turn;
  const grid = $("map"); grid.replaceChildren();
  for(const tile of world.districts) {const e = node("div", "", `tile ${tile.type}`); e.title = tile.type; grid.append(e);}
  const resource = $("market-resource").value;
  $("book-heading").textContent = `${resource[0].toUpperCase()}${resource.slice(1)} market`;
  renderOrders("bids", world.books[resource].bids);
  renderOrders("asks", world.books[resource].asks);
  const projectRoot = $("projects"); projectRoot.replaceChildren();
  for(const x of world.projects) {
    const box=node("section","","project");
    const top=node("div","","project-name");
    const name=node("strong",x.id.replaceAll("-"," "));
    top.append(name,node("small",`${x.progress} / ${x.goal} ${x.resource}`)); box.append(top);
    const bar=node("div","","progress"); bar.setAttribute("role","progressbar");
    bar.setAttribute("aria-valuenow",String(x.progress));bar.setAttribute("aria-valuemin","0");bar.setAttribute("aria-valuemax",String(x.goal));
    bar.setAttribute("aria-label",`${x.id.replaceAll("-"," ")} completion`);
    const fill=node("span","");fill.style.width=`${100*x.progress/x.goal}%`;bar.append(fill);box.append(bar);
    const row=node("div","","contribute-row");
    const amt=node("input","");amt.type="number";amt.min="1";amt.max=String(x.goal-x.progress);amt.value="2";
    amt.setAttribute("aria-label", `Units of ${x.resource} to contribute to ${x.id}`);
    const donate=node("button",x.complete ? "Completed ✓" : "Contribute ↗");
    donate.disabled=!p||x.complete;donate.type="button";
    donate.addEventListener("click",()=>action("contribute",{project:x.id,quantity:Number(amt.value)}));
    row.append(amt,donate);box.append(row);projectRoot.append(box);
  }
  const tape = $("tape");tape.replaceChildren();
  if (!world.trades.length) tape.append(node("li", "No trades yet. Invite a second avatar and trade.","empty"));
  for(const t of world.trades) tape.append(node("li",`T${t.turn} · ${t.quantity} ${t.resource} @ ${t.price} CIV · ${t.buyer} ↔ ${t.seller}`));
}
$("identity-form").addEventListener("submit", async e => {
  e.preventDefault(); actor = $("player-id").value.trim().toLowerCase();
  $("player-id").value=actor;localStorage.setItem("tcl-vos-actor",actor);
  await load();
  if(world && world.player){ notice(`Welcome back to Civitas-99, ${actor}.`);return; }
  await action("join");
});
$("trade-form").addEventListener("submit", e => {e.preventDefault();const f=new FormData(e.currentTarget);
  action("order",{side:f.get("side"),resource:f.get("resource"),price:Number(f.get("price")),quantity:Number(f.get("quantity"))});});
$("market-resource").addEventListener("change",render);
$("next-turn").addEventListener("click",()=>action("advance",{turns:1}));
for(const b of document.querySelectorAll("[data-work]"))b.addEventListener("click",()=>action("work",{role:b.dataset.work}));
const previous=localStorage.getItem("tcl-vos-actor");
if(previous && /^[a-z][a-z0-9_-]{1,23}$/.test(previous)){actor=previous;$("player-id").value=actor;}
$("player-id").addEventListener("change",()=>{actor=$("player-id").value.trim().toLowerCase();load();});
load();

// Pure i32 score(x) = 2*x; a compact no-import WASM test module.
const WASM_SCORE_DEMO = [0,97,115,109,1,0,0,0,1,6,1,96,1,127,1,127,3,2,1,0,7,9,1,5,115,99,111,114,101,0,0,10,9,1,7,0,32,0,65,2,108,11];
$("wasm-run").addEventListener("click", () => {
  $("wasm-run").disabled = true;
  $("wasm-output").textContent = "Worker executing…";
  const worker = new Worker("/guest_worker.js");
  let finished = false;
  const close = (msg) => { if(finished)return;finished=true;clearTimeout(timeout);worker.terminate();
    $("wasm-run").disabled=false;$("wasm-output").textContent=msg; };
  const timeout = setTimeout(()=>close("Guest budget exceeded; terminated"),500);
  worker.onmessage = e => close(e.data.ok ? `Guest result: ${e.data.score} (7 × 2)` : `Guest rejected: ${e.data.error}`);
  worker.onerror = () => close("Guest worker unavailable");
  worker.postMessage({op:"score",bytes:WASM_SCORE_DEMO,input:7});
});

