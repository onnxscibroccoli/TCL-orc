import {test} from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import {contextDigest,publicSignals,estimateBalance,validatePublic,FIELD} from '../client/protocol.mjs';

test('canonical statement uses the existing five signals and rejects substitutions',()=>{
 const note={commitment:'3',nullifier:'4',value:'5',recipient:'6',context:'7'};
 assert.deepEqual(publicSignals(note),['3','4','5','6','7']);
 assert.throws(()=>validatePublic({...note,value:'0'}));
 assert.throws(()=>validatePublic({...note,recipient:(2n**160n).toString()}));
 assert.throws(()=>validatePublic({...note,context:FIELD.toString()}));
 assert.throws(()=>validatePublic({...note,value:5}));
 assert.throws(()=>validatePublic({...note,commitment:'03'}));
});
test('context binds chain and controller independently',()=>{
 const a='0x'+'11'.repeat(20),b='0x'+'22'.repeat(20);
 assert.notEqual(contextDigest(1n,a),contextDigest(2n,a));
 assert.notEqual(contextDigest(1n,a),contextDigest(1n,b));
});
test('integer display matches Python reference across boundaries and retained rounding',()=>{
 const vectors=[];
 for(const balance of [0n,5000000n*10n**18n,5000000n*10n**18n+1n,10n**25n]){
  for(const elapsed of [0n,1n,86400n,31536000n,2n**128n])vectors.push([balance.toString(),elapsed.toString(),'12345']);
 }
 const python='import json,sys;from ledger.demurrage import settle;print(json.dumps([[str(x) for x in settle(*map(int,v))] for v in json.loads(sys.argv[1])]))';
 const expected=JSON.parse(execFileSync('python3',['-c',python,JSON.stringify(vectors)],{encoding:'utf8'}));
 vectors.forEach((v,i)=>assert.deepEqual(estimateBalance(...v).map(String),expected[i]));
 assert.throws(()=>estimateBalance('1','-1','0'));
});

test('wallet invalidates a proof when the chain changes and never auto-resubmits',async()=>{
 const {WalletClient}=await import('../client/wallet.mjs');
 const {EventEmitter}=await import('node:events');const {keccak256,Interface}=await import('ethers');
 const provider=new EventEmitter();provider.removeListener=provider.removeListener.bind(provider);
 const controller='0x'+'11'.repeat(20),verifier='0x'+'22'.repeat(20),ctx=contextDigest(1n,controller),abi=new Interface(['function context() view returns(uint256)','function verifier() view returns(address)']);
 let chain='0x1',sent=0;
 provider.request=async({method,params})=>{
  if(method==='eth_chainId')return chain;
  if(method==='eth_getCode')return '0x6000';
  if(method==='eth_call'){if(params[0].data===abi.encodeFunctionData('context'))return abi.encodeFunctionResult('context',[ctx]);return abi.encodeFunctionResult('verifier',[verifier]);}
  if(method==='eth_sendTransaction'){sent++;throw Error('uncertain RPC failure');}
  throw Error('unexpected RPC');
 };
 const client=new WalletClient(provider,{chainId:'1',controller,controllerCodeHash:keccak256('0x6000'),verifier,verifierCodeHash:keccak256('0x6000')});
 assert.equal((await client.binding()).context,ctx);
 chain='0x2';provider.emit('chainChanged',chain);
 await assert.rejects(()=>client.binding(),/INVALID_GROTH16_CONTEXT/);
 await assert.rejects(()=>client.submit('0x'+'33'.repeat(20)),/REGENERATE/);assert.equal(sent,0);client.close();
});

test('unresponsive RPC reads time out rather than leave proof generation stuck',{timeout:300},async()=>{
 const {WalletClient}=await import('../client/wallet.mjs');const {keccak256}=await import('ethers');
 const client=new WalletClient({request:()=>new Promise(()=>{})},{chainId:'1',controller:'0x'+'11'.repeat(20),verifier:'0x'+'22'.repeat(20),controllerCodeHash:keccak256('0x6000'),verifierCodeHash:keccak256('0x6000'),rpcTimeoutMs:5});
 await assert.rejects(()=>client.binding(),/RPC_TIMEOUT/);client.close();
});

test('balance view keeps committed state separate from a stale local estimate',async()=>{
 const {balanceDisplay}=await import('../client/view.mjs');
 const snapshot={committed:'10000000000000000000000000',lastSettled:'1000',blockTimestamp:'87400',initialized:true,exempt:false};
 const view=balanceDisplay(snapshot,0);
 assert.equal(view.committed,snapshot.committed);assert.ok(BigInt(view.estimate)<BigInt(view.committed));assert.equal(view.roundingUncertaintyWei,'1');
 assert.equal(balanceDisplay(snapshot,61).stale,true);
 assert.equal(balanceDisplay({...snapshot,exempt:true},0).estimate,snapshot.committed);
});

test('pending context lookup cannot start concurrent proving or reopen a closed client',async()=>{
 const {WalletClient}=await import('../client/wallet.mjs');const {keccak256}=await import('ethers');let resolve;
 const provider={request:()=>new Promise(r=>{resolve=r;})};
 const client=new WalletClient(provider,{chainId:'1',controller:'0x'+'11'.repeat(20),verifier:'0x'+'22'.repeat(20),controllerCodeHash:keccak256('0x6000'),verifierCodeHash:keccak256('0x6000')});
 const pending=client.prove(new ArrayBuffer(64),{},'0x'+'33'.repeat(20));
 await assert.rejects(()=>client.prove(new ArrayBuffer(64),{},'0x'+'33'.repeat(20)),/CLIENT_UNAVAILABLE/);
 client.close();resolve('0x2');await assert.rejects(()=>pending);assert.equal(client.state,'closed');
});
