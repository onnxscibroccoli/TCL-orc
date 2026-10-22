import {WalletClient} from './wallet.mjs';
import {renderBalance} from './view.mjs';
let client,timer,generation=0;
function changed(){
 generation++;client?.close();clearInterval(timer);
 for(const id of ['committed','estimated'])document.getElementById(id).textContent='Unavailable';
 document.getElementById('status').textContent='Wallet context changed. Refresh the snapshot.';
 document.getElementById('estimate-status').textContent='Previous snapshot invalidated.';
}
window.ethereum?.on?.('chainChanged',changed);window.ethereum?.on?.('accountsChanged',changed);
document.getElementById('connect').addEventListener('click',async()=>{
 const current=++generation;const status=document.getElementById('status');
 try{
  if(!window.ethereum)throw Error('An injected EIP-1193 wallet is required.');
  const response=await fetch('./deployment.json',{cache:'no-store'});if(!response.ok)throw Error('Install the independently verified deployment policy first.');
  const policy=await response.json();if(current!==generation)return;client?.close();clearInterval(timer);client=new WalletClient(window.ethereum,policy);
  const accounts=await window.ethereum.request({method:'eth_requestAccounts'});if(current!==generation)return;const snapshot=await client.readBalance(accounts[0]);
  if(current!==generation)return;const started=performance.now();const render=()=>renderBalance(document,snapshot,Math.floor((performance.now()-started)/1000));render();timer=setInterval(render,1000);
  status.textContent='Connected. Read-only view; no transaction has been requested.';
 }catch(error){if(current!==generation)return;client?.close();clearInterval(timer);status.textContent=error.message;}
});
