"use strict";
// No network APIs, analytics, external scripts, ads or wallet mint functions.
const $ = id => document.getElementById(id);
let state = null, activePage = "home", toastTimer = 0, driving = false;
const labels = ["Energy", "Water", "Alloy"], icons = ["⚡", "◈", "⬡"];
const canvas = $("city-canvas"), ctx = canvas.getContext("2d", {alpha:false});
let visualX = 2, visualY = 2, screenW = 960, screenH = 430;

function nativeCall(method, ...args) {
  try {
    const host = window.VOSHost && window.VOSHost.connect();
    if (!host) {
      throw new Error("No vOS host wallet is attached. Open in the Android app or another compatible vOS host.");
    }
    const response = host.invoke(method, ...args);
    if (!response.ok) throw new Error(response.error || "Game action rejected");
    if (response.state) { state = response.state; render(); }
    return response;
  } catch (err) {notify(String(err.message || err), true); return null;}
}

function newRequestId() {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
}
function command(game, action, arg=0) {
  const result = nativeCall("command", game, action, arg, newRequestId());
  if (result) notify(state.message + (state.delta ? ` (${state.delta>0?"+":""}${state.delta} τ)` : ""));
  return result;
}
function notify(message, error=false) {
  const el = $("notice"); el.textContent = message;
  el.classList.toggle("error", error); el.classList.add("visible");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => el.classList.remove("visible"), 3600);
}
function text(id,value) {$(id).textContent = String(value);}
function toPage(page) {
  if (!["home","civitas","nightdrive","wallet"].includes(page)) return;
  activePage = page;
  document.querySelectorAll(".page").forEach(el => el.classList.toggle("active",el.id==="page-"+page));
  document.querySelectorAll("[data-page].nav-tab").forEach(el => {
    const active = el.dataset.page===page;
    el.classList.toggle("active",active);
    if(active) el.setAttribute("aria-current","page");
    else el.removeAttribute("aria-current");
  });
  if(page==="nightdrive") resizeCanvas();
  window.scrollTo(0,0);
}
function renderMarket() {
  const list = $("market-list"); list.replaceChildren();
  const w = state.civitas;
  labels.forEach((label,index)=>{
    const row=document.createElement("div"); row.className="market-row";
    const name=document.createElement("div"); name.className="resource-title";
    const icon=document.createElement("span"); icon.className="resource-icon";icon.textContent=icons[index];
    const labelBox=document.createElement("span");
    const title=document.createElement("strong"); title.textContent=label;
    const stock=document.createElement("small"); stock.textContent="INVENTORY · "+w.inventory[index]+" UNITS";
    labelBox.append(title,stock);name.append(icon,labelBox);
    const prices=document.createElement("div");prices.className="market-prices";
    const buy=document.createElement("small"); buy.textContent="ASK / "+w.buy[index]+" τ";
    const sell=document.createElement("small"); sell.textContent="BID / "+w.sell[index]+" τ";
    prices.append(buy,sell);
    const actions=document.createElement("div");actions.className="market-actions";
    const buyButton=document.createElement("button");buyButton.className="primary";
    buyButton.textContent="BUY 1";buyButton.disabled=state.wallet.credits<w.buy[index];
    buyButton.setAttribute("aria-label","Buy one unit of "+label+" for "+w.buy[index]+" TCL credits");
    buyButton.addEventListener("click",()=>command("civitas","buy",index));
    const sellButton=document.createElement("button");sellButton.className="outline";
    sellButton.textContent="SELL 1";sellButton.disabled=w.inventory[index]===0;
    sellButton.setAttribute("aria-label","Sell one unit of "+label+" for "+w.sell[index]+" TCL credits");
    sellButton.addEventListener("click",()=>command("civitas","sell",index));
    actions.append(buyButton,sellButton);row.append(name,prices,actions);list.append(row);
  });
}
function render() {
  if (!state) return;
  const w=state.wallet,c=state.civitas,d=state.nightdrive;
  text("wallet-credits", w.credits.toLocaleString()+" τ");
  text("wallet-big",w.credits.toLocaleString()+" τ");
  text("current-profile",state.profile);
  text("civ-wallet",w.credits); text("civ-turn",String(c.turn).padStart(2,"0"));
  text("civ-shift",c.shiftAvailable?"AVAILABLE":"COMPLETE");
  $("work-shift").disabled=!c.shiftAvailable;
  renderMarket();
  text("drive-pos",String(d.x).padStart(2,"0")+", "+String(d.y).padStart(2,"0"));
  text("drive-goal",String(d.goalX).padStart(2,"0")+", "+String(d.goalY).padStart(2,"0"));
  text("drive-fuel",d.fuel+" / 60");text("drive-deliveries",d.deliveries);
  text("turbo-status",d.turbo?"✓ INSTALLED":"UNLOCK FOR 100 τ");
  text("paint-status",d.paint?"✓ INSTALLED":"UNLOCK FOR 55 τ");
  $("upgrade-turbo").disabled=d.turbo||w.credits<100;
  $("upgrade-paint").disabled=d.paint||w.credits<55;
  $("drive-refuel").disabled=d.fuel===60||w.credits<20;
  text("issued",w.issued);text("sunk",w.sunk);text("event-count",state.events);
  text("wallet-hash",state.digest);
  text("entropy-label",state.interaction.label);
  text("entropy-details",state.interaction.samples+" recent intervals · normalized Shannon entropy "+state.interaction.normalizedEntropy+" · advisory only");
  const history=$("wallet-history");history.replaceChildren();
  if(!state.history.length){const li=document.createElement("li");li.textContent="No transactions yet. Start a game to create a local journal event.";history.append(li);}
  state.history.slice().reverse().forEach(line=>{const li=document.createElement("li");li.textContent=line;history.append(li);});
}
function drive(dir) {
  if(driving||activePage!=="nightdrive")return;
  driving=true;
  try { command("nightdrive","move",dir); }
  finally {driving=false;}
}
function resizeCanvas() {
  const rect=canvas.getBoundingClientRect();
  if(rect.width<2||rect.height<2)return;
  const ratio=Math.min(window.devicePixelRatio||1,2);
  screenW=rect.width;screenH=rect.height;
  const width=Math.floor(rect.width*ratio),height=Math.floor(rect.height*ratio);
  if(canvas.width!==width||canvas.height!==height){canvas.width=width;canvas.height=height;}
  ctx.setTransform(ratio,0,0,ratio,0,0);
}
function roundedRect(x,y,w,h,r,color) {
  ctx.fillStyle=color;ctx.beginPath();
  ctx.moveTo(x+r,y);ctx.lineTo(x+w-r,y);ctx.quadraticCurveTo(x+w,y,x+w,y+r);
  ctx.lineTo(x+w,y+h-r);ctx.quadraticCurveTo(x+w,y+h,x+w-r,y+h);
  ctx.lineTo(x+r,y+h);ctx.quadraticCurveTo(x,y+h,x,y+h-r);
  ctx.lineTo(x,y+r);ctx.quadraticCurveTo(x,y,x+r,y);ctx.closePath();ctx.fill();
}
function poly(points,fill){ctx.fillStyle=fill;ctx.beginPath();ctx.moveTo(...points[0]);for(let i=1;i<points.length;i++)ctx.lineTo(...points[i]);ctx.closePath();ctx.fill();}
function renderCity(t) {
  requestAnimationFrame(renderCity);
  if(activePage!=="nightdrive"||!state) return;
  const w=screenW,h=screenH, d=state.nightdrive;
  visualX+=(d.x-visualX)*.13;visualY+=(d.y-visualY)*.13;
  const tile=Math.max(37,Math.min(61,w/12));
  ctx.fillStyle="#071827";ctx.fillRect(0,0,w,h);
  const gridX=Math.floor(visualX-w/(2*tile))-2;
  const gridY=Math.floor(visualY-h/(2*tile))-2;
  const nx=Math.ceil(w/tile)+5, ny=Math.ceil(h/tile)+5;
  const sx=x=>(x-visualX)*tile+w/2;
  const sy=y=>(y-visualY)*tile+h/2;
  for(let y=gridY;y<gridY+ny;y++)for(let x=gridX;x<gridX+nx;x++) {
    const X=sx(x),Y=sy(y);
    if(x<0||y<0||x>=24||y>=24){ctx.fillStyle="#050e18";ctx.fillRect(X,Y,tile+1,tile+1);continue;}
    const road=x%5===2||y%5===2;
    if(road) {
      ctx.fillStyle=((x+y)%2===0)?"#26394a":"#2b4151";ctx.fillRect(X,Y,tile+1,tile+1);
      ctx.strokeStyle="#3f5663";ctx.lineWidth=1;ctx.strokeRect(X+1,Y+1,tile-2,tile-2);
      ctx.fillStyle="#b2b6a570";
      if(x%5===2&&y%5!==2)ctx.fillRect(X+tile*.46,Y+tile*.11,tile*.06,tile*.30);
      else if(y%5===2&&x%5!==2)ctx.fillRect(X+tile*.11,Y+tile*.46,tile*.30,tile*.06);
      if(x%5===2&&y%5===2){ctx.fillStyle="#f6ca656b";ctx.fillRect(X+tile*.12,Y+tile*.12,3,3);}
    } else {
      const n=((x*37+y*71+x*y*11)%7+7)%7;
      ctx.fillStyle="#142c32";ctx.fillRect(X,Y,tile+1,tile+1);
      ctx.fillStyle="#335459";ctx.fillRect(X+3,Y+3,tile-6,tile-6);
      const z=9+n*2.8;
      ctx.fillStyle="#070f1eb0";ctx.fillRect(X+9,Y+10,tile-10,tile-8);
      poly([[X+5,Y+5-z],[X+tile-5,Y+5-z],[X+tile-5,Y+tile-5-z],[X+5,Y+tile-5-z]],n%2?"#366b77":"#3d6670");
      poly([[X+5,Y+tile-5-z],[X+tile-5,Y+tile-5-z],[X+tile-5,Y+tile-5],[X+5,Y+tile-5]],"#172f43");
      poly([[X+tile-5,Y+5-z],[X+tile-5,Y+tile-5-z],[X+tile-5,Y+tile-5],[X+tile-5,Y+5]],"#234252");
      ctx.fillStyle="#a4e4cb42";ctx.fillRect(X+tile*.29,Y+tile*.18-z,tile*.25,4);
      if(n%3===0){ctx.fillStyle="#65ddae";ctx.fillRect(X+tile-13,Y+tile-12-z,4,4);}
    }
  }
  // Decorative cars / passing traffic: display only, not authority for physics.
  for(let i=0;i<7;i++) {
    const y=(i*5+2)%24, x=((t/3800*(i%2?1:-1)+i*7)%24+24)%24;
    if(y%5!==2)continue;
    const X=sx(x),Y=sy(y);
    if(X<-25||X>w+25||Y<-25||Y>h+25)continue;
    roundedRect(X+tile*.20,Y+tile*.28,tile*.58,tile*.32,3,i%2?"#cf7390":"#d5a95f");
    ctx.fillStyle="#9de2e7";ctx.fillRect(X+tile*.38,Y+tile*.30,tile*.18,tile*.10);
  }
  const gx=sx(d.goalX)+tile/2,gy=sy(d.goalY)+tile/2;
  const pulse=10+Math.sin(t/300)*4;
  ctx.strokeStyle="#ffdf83";ctx.lineWidth=3;ctx.beginPath();ctx.arc(gx,gy,pulse,0,Math.PI*2);ctx.stroke();
  roundedRect(gx-28,gy-33,56,15,3,"#614d29");ctx.fillStyle="#ffdf83";ctx.font="bold 9px sans-serif";ctx.textAlign="center";ctx.fillText("DROP OFF",gx,gy-22);
  // Player is always rendered at screen centre; authoritative grid controls position.
  const cx=sx(visualX)+tile/2,cy=sy(visualY)+tile/2;
  ctx.save();ctx.translate(cx,cy);ctx.rotate(d.heading*Math.PI/2);
  ctx.fillStyle="#00000085";ctx.beginPath();ctx.ellipse(4,6,14,22,0,0,Math.PI*2);ctx.fill();
  roundedRect(-12,-20,24,40,6,d.paint?"#f659b8":"#64e5d0");
  roundedRect(-9,-13,18,12,2,"#174154");
  roundedRect(-9,4,18,7,2,"#286d6c");
  ctx.fillStyle="#ffe0a0";ctx.fillRect(-11,-19,5,4);ctx.fillRect(6,-19,5,4);
  ctx.fillStyle="#ec6993";ctx.fillRect(-11,15,4,4);ctx.fillRect(7,15,4,4);
  ctx.restore();
  // In-game minimap, 24x24; purely visual reference, no tracking.
  const ms=Math.min(103,Math.floor(w*.23)), bx=w-ms-12,by=h-ms-10;
  ctx.fillStyle="#091624d8";ctx.fillRect(bx-5,by-5,ms+10,ms+10);
  const unit=ms/24;
  for(let y=0;y<24;y++)for(let x=0;x<24;x++){ctx.fillStyle=(x%5===2||y%5===2)?"#466777":"#1d3745";ctx.fillRect(bx+x*unit,by+y*unit,unit+.2,unit+.2);}
  ctx.fillStyle="#ffcb67";ctx.fillRect(bx+d.goalX*unit-1,by+d.goalY*unit-1,Math.max(4,unit),Math.max(4,unit));
  ctx.fillStyle="#70ffe0";ctx.beginPath();ctx.arc(bx+d.x*unit+unit/2,by+d.y*unit+unit/2,4,0,Math.PI*2);ctx.fill();
}
function startDpad() {
  document.querySelectorAll(".dpad button[data-dir]").forEach(button=>{
    let held=null;
    const stop=()=>{if(held!==null){clearInterval(held);held=null;}};
    button.addEventListener("pointerdown",e=>{
      e.preventDefault();stop();const dir=Number(button.dataset.dir);drive(dir);
      held=setInterval(()=>drive(dir),175);
      button.setPointerCapture(e.pointerId);
    });
    button.addEventListener("pointerup",stop);
    button.addEventListener("pointercancel",stop);
    button.addEventListener("lostpointercapture",stop);
  });
}
function boot() {
  document.querySelectorAll("[data-page]").forEach(button=>button.addEventListener("click",()=>toPage(button.dataset.page)));
  $("profile-form").addEventListener("submit",e=>{e.preventDefault();const profile=$("profile-name").value.trim();const result=nativeCall("selectProfile",profile);if(result)notify("Local profile loaded · "+state.profile);});
  $("work-shift").addEventListener("click",()=>command("civitas","shift",Number($("work-role").value)));
  $("civ-next").addEventListener("click",()=>command("civitas","next-turn",0));
  $("upgrade-turbo").addEventListener("click",()=>command("nightdrive","tune",0));
  $("upgrade-paint").addEventListener("click",()=>command("nightdrive","tune",1));
  $("drive-refuel").addEventListener("click",()=>command("nightdrive","refuel",0));
  document.addEventListener("pointerdown",()=>{if(window.VOSHost){const h=window.VOSHost.connect();if(h)h.observeInteraction();}},{passive:true});
  document.addEventListener("keydown",e=>{
    if(window.VOSHost){const h=window.VOSHost.connect();if(h)h.observeInteraction();}
    if(["INPUT","SELECT","TEXTAREA"].includes(document.activeElement.tagName))return;
    const directions={ArrowUp:0,KeyW:0,ArrowRight:1,KeyD:1,ArrowDown:2,KeyS:2,ArrowLeft:3,KeyA:3};
    if(Object.prototype.hasOwnProperty.call(directions,e.code)&&activePage==="nightdrive"){
      e.preventDefault();drive(directions[e.code]);
    }
  });
  startDpad();
  nativeCall("state");
  window.addEventListener("resize",resizeCanvas);
  requestAnimationFrame(renderCity);
  notify("TCL vOS ready · shared τ game wallet loaded");
}
document.addEventListener("DOMContentLoaded",boot);
