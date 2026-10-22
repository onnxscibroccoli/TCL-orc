const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const solc=require('solc');
const ganache=require('ganache');
const {ethers}=require('ethers');
const sources=Object.fromEntries(fs.readdirSync('contracts').filter(x=>x.endsWith('.sol')).map(x=>[x,{content:fs.readFileSync('contracts/'+x,'utf8')} ]));
sources['TestVerifier.sol']={content:`pragma solidity ^0.8.20;
contract TestVerifier {
 bool public accepted = true;
 function setAccepted(bool flag) external { accepted = flag; }
 function verifyProof(uint[2] calldata,uint[2][2] calldata,uint[2] calldata,uint[5] calldata) external view returns(bool){return accepted;}
}`};
const compiled=JSON.parse(solc.compile(JSON.stringify({language:'Solidity',sources,settings:{evmVersion:'shanghai',optimizer:{enabled:true,runs:200},outputSelection:{'*':{'*':['abi','evm.bytecode.object']}}}})));
assert.deepEqual((compiled.errors||[]).filter(x=>x.severity==='error'),[]);
async function setup(){
 const rpc=ganache.provider({logging:{quiet:true},miner:{timestampIncrement:0},wallet:{totalAccounts:4}});
 const provider=new ethers.BrowserProvider(rpc); provider.pollingInterval=10;
 const [gov,alice,bob,attacker]=await Promise.all([0,1,2,3].map(i=>provider.getSigner(i)));
 async function deploy(file,name,args=[]){const c=compiled.contracts[file][name];const d=await new ethers.ContractFactory(c.abi,c.evm.bytecode.object,gov).deploy(...args);await d.waitForDeployment();return d;}
 const engine=await deploy('CirculatoryEngine.sol','CirculatoryEngine',[await gov.getAddress(),await alice.getAddress(),ethers.parseEther('10000000')]);
 const verifier=await deploy('TestVerifier.sol','TestVerifier');
 const mint=await deploy('PhysicalMintController.sol','PhysicalMintController',[await engine.getAddress(),await verifier.getAddress()]);
 await (await engine.setPhysicalMint(await mint.getAddress())).wait();
 return {rpc,provider,gov,alice,bob,attacker,engine,verifier,mint};
}
const proof='0x'+'00'.repeat(256);
const commitment=ethers.zeroPadValue('0x01',32), nullifier=ethers.zeroPadValue('0x02',32);
async function rejects(action){await assert.rejects(async()=>{const tx=await action();if(tx.wait)await tx.wait();});}
test('ledger conserves issued supply through escrow, redemption, demurrage and redistribution',async()=>{
 const s=await setup();try{
 const {engine,mint,alice,bob,gov,rpc}=s;const amount=ethers.parseEther('100');
 await (await engine.connect(alice).approve(await mint.getAddress(),amount)).wait();
 await (await mint.connect(alice).lockNote(commitment,amount)).wait();
 assert.equal(await mint.totalEscrowed(),amount);
 await rpc.request({method:'evm_increaseTime',params:[86400]});await rpc.request({method:'evm_mine',params:[]});
 await (await engine.applyDemurrage(await alice.getAddress())).wait();
 assert.ok(await engine.vaultBalance()>0n);
 await (await engine.applyDemurrage(await mint.getAddress())).wait();assert.equal(await engine.balances(await mint.getAddress()),amount);
 await (await mint.connect(bob).redeemNote(commitment,nullifier,await bob.getAddress(),proof)).wait();
 assert.equal(await engine.balances(await bob.getAddress()),amount);assert.equal(await mint.totalEscrowed(),0n);
 const vault=await engine.vaultBalance();await (await engine.connect(gov).redistribute(await bob.getAddress(),vault)).wait();
 const total=await engine.balances(await alice.getAddress())+await engine.balances(await bob.getAddress())+await engine.balances(await mint.getAddress())+await engine.vaultBalance();
 assert.equal(total,await engine.totalSupply());
 await rejects(async()=>mint.connect(bob).redeemNote(commitment,nullifier,await bob.getAddress(),proof));
 await rejects(async()=>mint.connect(alice).lockNote(commitment,amount));
 }finally{await s.rpc.disconnect();}
});
test('rejects unauthorized configuration, vault theft, unbacked mint and invalid proof',async()=>{
 const s=await setup();try{const {engine,mint,alice,bob,attacker,verifier}=s;
 await rejects(async()=>engine.connect(attacker).redistribute(awaitAddress(attacker),1));
 await rejects(async()=>engine.connect(attacker).setPhysicalMint(awaitAddress(attacker)));
 await rejects(async()=>engine.setPhysicalMint(awaitAddress(mint)));
 await rejects(async()=>mint.connect(attacker).lockNote(commitment,1));
 await rejects(async()=>mint.connect(alice).lockNote(ethers.ZeroHash,1));
 await rejects(async()=>mint.connect(alice).lockNote(commitment,0));
 await (await engine.connect(alice).approve(await mint.getAddress(),100)).wait();await (await mint.connect(alice).lockNote(commitment,100)).wait();
 await (await verifier.setAccepted(false)).wait();
 await rejects(async()=>mint.connect(bob).redeemNote(commitment,nullifier,awaitAddress(bob),proof));
 assert.equal(await mint.totalEscrowed(),100n);assert.equal(await mint.usedNullifiers(nullifier),false);
 await rejects(async()=>mint.connect(bob).redeemNote(commitment,nullifier,awaitAddress(bob),'0x'));
 }finally{await s.rpc.disconnect();}
});
function awaitAddress(s){return s.getAddress();}
test('elapsed time is chain-derived, immunity is preserved and long intervals cap at surplus',async()=>{
 const s=await setup();try{const {engine,alice,bob,rpc}=s;
 await (await engine.connect(alice).transfer(await bob.getAddress(),ethers.parseEther('5000000'))).wait();
 await rpc.request({method:'evm_increaseTime',params:[86400*5000]});await rpc.request({method:'evm_mine',params:[]});
 await (await engine.applyDemurrage(await alice.getAddress())).wait();await (await engine.applyDemurrage(await bob.getAddress())).wait();
 assert.ok(await engine.balances(await alice.getAddress())>=await engine.T());
 assert.equal(await engine.balances(await bob.getAddress()),await engine.T());
 }finally{await s.rpc.disconnect();}
});
test('demurrage follows exponential decay and frequent public settlement cannot erase fees',async()=>{
 const s=await setup();try{const {engine,alice,rpc}=s;
 const start=await engine.balances(await alice.getAddress());const threshold=await engine.T();
 const snapshot=await rpc.request({method:'evm_snapshot',params:[]});
 await rpc.request({method:'evm_increaseTime',params:[86400]});await rpc.request({method:'evm_mine',params:[]});
 await (await engine.applyDemurrage(await alice.getAddress())).wait();const once=await engine.balances(await alice.getAddress());
 const expected=Number(start-threshold)*Math.exp(-0.0005)+Number(threshold);
 assert.ok(Math.abs(Number(once)-expected)/expected<1e-12,'continuous daily decay');
 await rpc.request({method:'evm_revert',params:[snapshot]});
 for(let i=0;i<24;i++){await rpc.request({method:'evm_increaseTime',params:[3600]});await rpc.request({method:'evm_mine',params:[]});await (await engine.applyDemurrage(await alice.getAddress())).wait();}
 const frequent=await engine.balances(await alice.getAddress());
 assert.ok(once>frequent?once-frequent<100n:frequent-once<100n,'settlement frequency changes only rounding dust');
 }finally{await s.rpc.disconnect();}
});
test('governor transfer requires nominee acceptance and revokes prior authority',async()=>{
 const s=await setup();try{const {engine,gov,bob,attacker}=s;
 await (await engine.proposeGovernor(await bob.getAddress())).wait();
 await rejects(async()=>engine.connect(attacker).acceptGovernance());
 await (await engine.connect(bob).acceptGovernance()).wait();
 assert.equal(await engine.governor(),await bob.getAddress());
 await rejects(async()=>engine.connect(gov).redistribute(await gov.getAddress(),0));
 await (await engine.connect(bob).redistribute(await gov.getAddress(),0)).wait();
 }finally{await s.rpc.disconnect();}
});
test('bounded randomized transfers preserve supply and fail atomically on insufficient balance',async()=>{
 const s=await setup();try{const {engine,alice,bob,attacker,rpc}=s;const signers=[alice,bob,attacker];let seed=73;
 for(let i=0;i<30;i++){
  seed=(seed*1664525+1013904223)>>>0;const from=signers[seed%3],to=signers[(seed>>>8)%3];
  await rpc.request({method:'evm_increaseTime',params:[seed%3600]});await rpc.request({method:'evm_mine',params:[]});
  await (await engine.applyDemurrage(await from.getAddress())).wait();
  const balance=await engine.balances(await from.getAddress());const amount=balance===0n?0n:balance/BigInt(2+(seed%5));
  await (await engine.connect(from).transfer(await to.getAddress(),amount)).wait();
  const sum=(await Promise.all(signers.map(x=>engine.balances(awaitAddress(x))))).reduce((a,b)=>a+b,0n)+await engine.vaultBalance();
  assert.equal(sum,await engine.totalSupply());
 }
 const before=await engine.balances(await bob.getAddress());
 await rejects(async()=>engine.connect(bob).transfer(await alice.getAddress(),await engine.totalSupply()+1n));
 assert.equal(await engine.balances(await bob.getAddress()),before);
 }finally{await s.rpc.disconnect();}
});
test('Python demurrage reference matches deployed EVM settlement at every sampled interval',async()=>{
 const {execFileSync}=require('node:child_process');
 const s=await setup();try{
 const account=await s.alice.getAddress();let remainder=0n;
 for(const elapsed of [0,1,59,3600,86400,86400*365,86400*1000000]){
  const before=await s.engine.balances(account),oldVault=await s.engine.vaultBalance();
  const previous=await s.engine.lastSettled(account);
  await s.rpc.request({method:'evm_increaseTime',params:[elapsed]});
  await s.rpc.request({method:'evm_mine',params:[]});
  await (await s.engine.applyDemurrage(account,{gasLimit:500000})).wait();
  const actualElapsed=(await s.engine.lastSettled(account))-previous;
  const script='import json,sys; from ledger.demurrage import settle; print(json.dumps([str(x) for x in settle(*map(int,sys.argv[1:]))]))';
  const [balance,fee,nextRemainder]=JSON.parse(execFileSync(process.env.PYTHON||'python3',['-c',script,String(before),String(actualElapsed),String(remainder)],{encoding:'utf8'})).map(BigInt);
  assert.equal(await s.engine.balances(account),balance);
  assert.equal((await s.engine.vaultBalance())-oldVault,fee);
  assert.equal(balance+fee,before);remainder=nextRemainder;
 }
 }finally{await s.rpc.disconnect();}
});
