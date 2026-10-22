import {AbiCoder,getAddress,keccak256,Interface,toBeHex,toQuantity} from 'ethers';
import {controllerABI,contextDigest,publicSignals,validatePublic,estimateBalance} from './protocol.mjs';
export {estimateBalance} from './protocol.mjs';
export class WalletClient {
 #provider;#policy;#epoch=0;#active;#proof;#closed=false;#changed;
 #state='idle';
 get state(){return this.#state;}
 constructor(provider,policy){
  this.#provider=provider;this.#policy=structuredClone(policy);
  this.#policy.controller=getAddress(policy.controller);this.#policy.verifier=getAddress(policy.verifier);
  for(const hash of [policy.controllerCodeHash,policy.verifierCodeHash])if(!/^0x[a-f0-9]{64}$/.test(hash))throw Error('CODE_PIN_REQUIRED');
  this.#changed=()=>this.invalidate();provider.on?.('chainChanged',this.#changed);provider.on?.('accountsChanged',this.#changed);
 }
 async #request(args){
  const timeout=this.#policy.rpcTimeoutMs??10000;
  if(!Number.isSafeInteger(timeout)||timeout<1||timeout>120000)throw Error('RPC_TIMEOUT_POLICY');
  let timer;
  try{return await Promise.race([this.#provider.request(args),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('RPC_TIMEOUT')),timeout);})]);}
  finally{clearTimeout(timer);}
 }
 async binding(){
  if(this.#closed)throw Error('CLIENT_CLOSED');
  const p=this.#policy,request=args=>this.#request(args);
  const chainId=BigInt(await request({method:'eth_chainId'})).toString();if(chainId!==p.chainId)throw Error('INVALID_GROTH16_CONTEXT');
  for(const [address,hash] of [[p.controller,p.controllerCodeHash],[p.verifier,p.verifierCodeHash]]){
   const code=await request({method:'eth_getCode',params:[address,'latest']});if(code==='0x'||keccak256(code)!==hash)throw Error('INVALID_GROTH16_CONTEXT');
  }
  const view=async name=>controllerABI.decodeFunctionResult(name,await request({method:'eth_call',params:[{to:p.controller,data:controllerABI.encodeFunctionData(name)},'latest']}))[0];
  const context=(await view('context')).toString();
  if(context!==contextDigest(chainId,p.controller)||getAddress(await view('verifier'))!==p.verifier)throw Error('INVALID_GROTH16_CONTEXT');
  return{chainId,controller:p.controller,context};
 }
 invalidate(){
  this.#epoch++;this.#proof=undefined;this.#state=['uncertain','submitting'].includes(this.#state)?'uncertain':'needs-regeneration';
  const active=this.#active;this.#active=undefined;
  if(active){clearTimeout(active.timer);active.worker.terminate();active.reject(Error('REGENERATE_REQUIRED'));}
 }
 async prove(privateBytes,note,recipient,{timeoutMs=4500}={}){
  if(this.#state==='uncertain')throw Error('RECONCILE_TRANSACTION_FIRST');
  if(this.#closed||this.#active||['proving','submitting'].includes(this.#state))throw Error('CLIENT_UNAVAILABLE');
  if(!(privateBytes instanceof ArrayBuffer)||privateBytes.byteLength!==64||!Number.isSafeInteger(timeoutMs)||timeoutMs<1||timeoutMs>120000)throw Error('INVALID_PROOF_REQUEST');
  this.invalidate();const epoch=this.#epoch;this.#state='proving';
  try{
   const binding=await this.binding();if(epoch!==this.#epoch||note.context!==binding.context)throw Error('INVALID_GROTH16_CONTEXT');
   const expected={commitment:note.commitment,nullifier:'1',value:note.value,recipient:BigInt(getAddress(recipient)).toString(),context:binding.context};validatePublic(expected);
   const workerStarted=performance.now();
   const worker=new Worker(new URL('./prover.worker.js',import.meta.url),{type:'module'});
   const result=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{this.#active=undefined;worker.terminate();this.#epoch++;this.#state='needs-regeneration';reject(Error('PROOF_TIMEOUT'));},timeoutMs);
    this.#active={worker,reject,timer};
    worker.onmessage=({data})=>data?.ok?resolve(data):reject(Error('PROOF_FAILED'));
    worker.onerror=()=>reject(Error('PROOF_WORKER_FAILED'));worker.onmessageerror=()=>reject(Error('PROOF_WORKER_FAILED'));
    worker.postMessage({privateBytes,note:expected,assets:this.#policy.assets},[privateBytes]);
   }).finally(()=>{worker.terminate();if(this.#active?.worker===worker){clearTimeout(this.#active.timer);this.#active=undefined;}});
   const elapsedMs=performance.now()-workerStarted;
   expected.nullifier=result.signals?.[1];
   if(JSON.stringify(result.signals)!==JSON.stringify(publicSignals(expected))||JSON.stringify(result.calldata?.[3].map(x=>BigInt(x).toString()))!==JSON.stringify(result.signals))throw Error('INVALID_GROTH16_CONTEXT');
   const fresh=await this.binding();if(epoch!==this.#epoch||JSON.stringify(fresh)!==JSON.stringify(binding))throw Error('INVALID_GROTH16_CONTEXT');
   const [a,b,c]=result.calldata;
   const proof=AbiCoder.defaultAbiCoder().encode(['uint256[2]','uint256[2][2]','uint256[2]'],[a,b,c]);
   this.#proof={binding,signals:result.signals,proof,epoch};this.#state='ready';
   return{signals:[...result.signals],proof,elapsedMs,proverMs:result.elapsedMs};
  }catch(error){if(!this.#closed&&epoch===this.#epoch){this.#proof=undefined;if(this.#state!=='uncertain')this.#state='needs-regeneration';}throw error;}
 }
 async readBalance(account){
  const epoch=this.#epoch;await this.binding();
  const block=await this.#request({method:'eth_getBlockByNumber',params:['latest',false]});
  if(!/^0x[a-f0-9]{64}$/i.test(block?.hash))throw Error('INVALID_BLOCK');
  const view=async(address,abi,name,args=[])=>abi.decodeFunctionResult(name,await this.#request({method:'eth_call',params:[{to:address,data:abi.encodeFunctionData(name,args)},block.number]}))[0];
  const ledger=getAddress(await view(this.#policy.controller,controllerABI,'circulatoryEngine'));
  const ledgerABI=new Interface(['function balances(address) view returns(uint256)','function lastSettled(address) view returns(uint256)','function physicalMint() view returns(address)']);
  if(!/^0x[a-f0-9]{64}$/.test(this.#policy.ledgerCodeHash??'')||keccak256(await this.#request({method:'eth_getCode',params:[ledger,block.number]}))!==this.#policy.ledgerCodeHash)throw Error('LEDGER_PIN_REQUIRED');
  const address=getAddress(account),committed=(await view(ledger,ledgerABI,'balances',[address])).toString();
  const lastSettled=(await view(ledger,ledgerABI,'lastSettled',[address])).toString();
  const exempt=getAddress(await view(ledger,ledgerABI,'physicalMint'))===address;
  const fresh=await this.#request({method:'eth_getBlockByNumber',params:[block.number,false]});await this.binding();
  if(epoch!==this.#epoch||fresh.hash!==block.hash)throw Error('STALE_CHAIN_SNAPSHOT');
  return{committed,lastSettled,exempt,initialized:lastSettled!=='0',blockTimestamp:BigInt(block.timestamp).toString(),blockNumber:BigInt(block.number).toString(),blockHash:block.hash};
 }
 async submit(from){
  const saved=this.#proof;if(this.#state!=='ready'||!saved)throw Error('REGENERATE_REQUIRED');
  this.#state='submitting';this.#proof=undefined;
  try{
   const current=await this.binding();if(saved.epoch!==this.#epoch||JSON.stringify(current)!==JSON.stringify(saved.binding))throw Error('INVALID_GROTH16_CONTEXT');
   const [commitment,nullifier,,recipient]=saved.signals;
   const tx={from:getAddress(from),to:current.controller,chainId:toQuantity(BigInt(current.chainId)),data:controllerABI.encodeFunctionData('redeemNote',[toBeHex(BigInt(commitment),32),toBeHex(BigInt(nullifier),32),toBeHex(BigInt(recipient),20),saved.proof])};
   await this.#request({method:'eth_estimateGas',params:[tx]});
   if(saved.epoch!==this.#epoch)throw Error('INVALID_GROTH16_CONTEXT');
   const hash=await this.#request({method:'eth_sendTransaction',params:[tx]});
   if(this.#closed||saved.epoch!==this.#epoch)throw Error('CONTEXT_CHANGED_DURING_SEND');
   if(!/^0x[a-fA-F0-9]{64}$/.test(hash))throw Error('RPC_RESULT_INVALID');this.#state='submitted';return hash;
  }catch{this.#proof=undefined;if(!this.#closed)this.#state='uncertain';throw Error('RPC_REJECTED_OR_UNCERTAIN_RECONCILE_FIRST');}
 }
 close(){this.invalidate();this.#closed=true;this.#provider.removeListener?.('chainChanged',this.#changed);this.#provider.removeListener?.('accountsChanged',this.#changed);this.#state='closed';}
}
