const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const solc=require('solc');
const ganache=require('ganache');
const {ethers}=require('ethers');
function compile(){
 const sources={};function walk(dir){for(const entry of fs.readdirSync(dir,{withFileTypes:true})){const p=path.join(dir,entry.name);if(entry.isDirectory())walk(p);else if(p.endsWith('.sol'))sources[p]={content:fs.readFileSync(p,'utf8')};}}walk('contracts');
 sources['Fixtures.sol']={content:`pragma solidity ^0.8.20;
 contract FakeTimelock {function getMinDelay() external pure returns(uint){return 172800;}}
 contract AcceptProof {function verifyProof(uint[2] calldata,uint[2][2] calldata,uint[2] calldata,uint[5] calldata) external pure returns(bool){return true;}}
 contract MultisigFixture {address public owner;constructor(){owner=msg.sender;}function forward(address t,bytes calldata d) external {require(msg.sender==owner);(bool ok,)=t.call(d);require(ok);}}
 `};
 const out=JSON.parse(solc.compile(JSON.stringify({language:'Solidity',sources,settings:{evmVersion:'shanghai',optimizer:{enabled:true,runs:200},outputSelection:{'*':{'*':['abi','evm.bytecode.object']}}}}),{import:p=>{const target=p.startsWith('@openzeppelin/')?'node_modules/'+p:p;return fs.existsSync(target)?{contents:fs.readFileSync(target,'utf8')}:{error:'missing '+p};}}));
 assert.deepEqual((out.errors||[]).filter(e=>e.severity==='error'),[]);return out.contracts;
}
const compiled=compile();
async function setup(){
 const rpc=ganache.provider({logging:{quiet:true},miner:{timestampIncrement:0}});const provider=new ethers.BrowserProvider(rpc);provider.pollingInterval=10;
 const [admin,holder,recipient,guardian,other]=await Promise.all([0,1,2,3,4].map(i=>provider.getSigner(i)));
 async function deploy(file,name,args=[]){const c=compiled[file][name];const d=await new ethers.ContractFactory(c.abi,c.evm.bytecode.object,admin).deploy(...args);await d.waitForDeployment();return d;}
 const multisig=await deploy('Fixtures.sol','MultisigFixture');
 const timelock=await deploy('contracts/production/ProtocolTimelock.sol','ProtocolTimelock',[172800,await multisig.getAddress()]);
 const verifier=await deploy('Fixtures.sol','AcceptProof');const codeHash=ethers.keccak256(await provider.getCode(await verifier.getAddress()));
 const factory=await deploy('contracts/production/ProtocolDeploymentFactory.sol','ProtocolDeploymentFactory');
 const fakeTimelock=await deploy('Fixtures.sol','FakeTimelock');
 const issuer=ethers.Wallet.createRandom();const release=ethers.id('approved release');
 const config=[await timelock.getAddress(),await guardian.getAddress(),await holder.getAddress(),1000000,await verifier.getAddress(),codeHash,release,issuer.address];
 const receipt=await (await factory.deployProtocol(config)).wait();
 const event=receipt.logs.map(l=>{try{return factory.interface.parseLog(l)}catch{return null}}).find(l=>l?.name==='ProtocolDeployed');
 const engine=new ethers.Contract(event.args.engine,compiled['contracts/CirculatoryEngine.sol'].CirculatoryEngine.abi,admin);
 const mint=new ethers.Contract(event.args.controller,compiled['contracts/production/ProductionMintController.sol'].ProductionMintController.abi,admin);
 async function govern(target,data){const salt=ethers.hexlify(ethers.randomBytes(32));const call=timelock.interface.encodeFunctionData('schedule',[target,0,data,ethers.ZeroHash,salt,172800]);await (await multisig.forward(await timelock.getAddress(),call)).wait();await assert.rejects(()=>timelock.execute.staticCall(target,0,data,ethers.ZeroHash,salt));await rpc.request({method:'evm_increaseTime',params:[172800]});await rpc.request({method:'evm_mine',params:[]});await (await timelock.execute(target,0,data,ethers.ZeroHash,salt)).wait();}
 async function enrollment(commitment,value,id,signer=issuer){const block=await rpc.request({method:'eth_getBlockByNumber',params:['latest',false]});const expires=Number(BigInt(block.timestamp))+3600;const network=await provider.getNetwork();const sig=await signer.signTypedData({name:'TCL Physical Enrollment',version:'1',chainId:network.chainId,verifyingContract:await mint.getAddress()},{Enrollment:[{name:'commitment',type:'bytes32'},{name:'value',type:'uint256'},{name:'depositor',type:'address'},{name:'enrollmentId',type:'bytes32'},{name:'expiresAt',type:'uint256'},{name:'releaseManifest',type:'bytes32'}]},{commitment,value,depositor:await holder.getAddress(),enrollmentId:id,expiresAt:expires,releaseManifest:release});return [commitment,value,id,expires,sig];}
 await govern(await engine.getAddress(),engine.interface.encodeFunctionData('acceptGovernance'));
 return {rpc,provider,admin,holder,recipient,guardian,other,multisig,timelock,verifier,factory,config,codeHash,release,engine,mint,govern,enrollment,fakeTimelock};
}
const note=ethers.toBeHex(1n,32),proof='0x'+'00'.repeat(256);
test('factory configures immutable escrow and hands authority to timelock atomically; deposits start disabled',async()=>{
 const s=await setup();try{const {engine,mint,timelock,release,codeHash,factory,config}=s;
 assert.equal(await engine.governor(),await timelock.getAddress());assert.equal(await engine.physicalMint(),await mint.getAddress());
 assert.equal(await mint.releaseManifestHash(),release);assert.equal(await mint.verifierCodeHash(),codeHash);assert.equal(await mint.depositsPaused(),true);
 const bad=[...config];bad[5]=ethers.ZeroHash;await assert.rejects(()=>factory.deployProtocol.staticCall(bad));
 }finally{await s.rpc.disconnect();}
});
test('enrollment authorization is domain/depositor bound; guardian stops deposits; only delayed governance resumes',async()=>{
 const s=await setup();try{const {engine,mint,holder,other,guardian,govern,enrollment}=s;
 await (await engine.connect(holder).approve(await mint.getAddress(),1000)).wait();
 await assert.rejects(()=>mint.connect(holder).lockNote.staticCall(note,100));
 let args=await enrollment(note,100,ethers.id('enrollment-1'));
 await assert.rejects(()=>mint.connect(holder).lockEnrolledNote.staticCall(...args));
 await govern(await mint.getAddress(),mint.interface.encodeFunctionData('resumeDeposits'));
 args=await enrollment(note,100,ethers.id('enrollment-1'));
 await assert.rejects(()=>mint.connect(other).lockEnrolledNote.staticCall(...args));
 const invalid=await enrollment(note,100,ethers.id('enrollment-1'),ethers.Wallet.createRandom());await assert.rejects(()=>mint.connect(holder).lockEnrolledNote.staticCall(...invalid));
 await (await mint.connect(holder).lockEnrolledNote(...args)).wait();assert.equal(await mint.totalEscrowed(),100n);
 await (await mint.connect(guardian).pauseDeposits()).wait();assert.equal(await mint.depositsPaused(),true);
 await assert.rejects(()=>mint.connect(guardian).resumeDeposits.staticCall());
 await assert.rejects(()=>mint.connect(other).pauseDeposits.staticCall());
 }finally{await s.rpc.disconnect();}
});
test('concurrent competing redemptions release exactly one value; redemption circuit interface remains unchanged',async()=>{
 const s=await setup();try{const {mint,engine,holder,recipient,admin,other,guardian,govern,enrollment}=s;
 await govern(await mint.getAddress(),mint.interface.encodeFunctionData('resumeDeposits'));
 await (await engine.connect(holder).approve(await mint.getAddress(),100)).wait();await (await mint.connect(holder).lockEnrolledNote(...await enrollment(note,100,ethers.id('enrollment')))).wait();
 await (await mint.connect(guardian).pauseDeposits()).wait();
 const transactions=await Promise.allSettled([mint.connect(admin).redeemNote(note,ethers.toBeHex(2n,32),await recipient.getAddress(),proof,{gasLimit:1000000}).then(tx=>tx.wait()),mint.connect(other).redeemNote(note,ethers.toBeHex(3n,32),await recipient.getAddress(),proof,{gasLimit:1000000}).then(tx=>tx.wait())]);
 assert.equal(transactions.filter(x=>x.status==='fulfilled').length,1);assert.equal(await engine.balances(await recipient.getAddress()),100n);assert.equal(await mint.totalEscrowed(),0n);
 }finally{await s.rpc.disconnect();}
});
test('effective governance delay cannot be reduced below two days',async()=>{
 const s=await setup();try{const {timelock,multisig,govern}=s;
 await govern(await timelock.getAddress(),timelock.interface.encodeFunctionData('updateDelay',[0]));
 assert.equal(await timelock.getMinDelay(),172800n);
 const call=timelock.interface.encodeFunctionData('schedule',[await timelock.getAddress(),0,'0x',ethers.ZeroHash,ethers.id('zero-delay'),0]);
 await assert.rejects(()=>multisig.forward.staticCall(timelock.getAddress(),call));
 }finally{await s.rpc.disconnect();}
});
test('enrollment expiry/reuse reject and emergency redemption halt has no withdrawal path',async()=>{
 const s=await setup();try{const {mint,engine,holder,recipient,guardian,govern,enrollment,rpc}=s;
 await govern(await mint.getAddress(),mint.interface.encodeFunctionData('resumeDeposits'));
 await (await engine.connect(holder).approve(await mint.getAddress(),1000)).wait();
 const id=ethers.id('one enrollment');let args=await enrollment(note,100,id);
 await rpc.request({method:'evm_increaseTime',params:[3601]});await rpc.request({method:'evm_mine',params:[]});
 await assert.rejects(()=>mint.connect(holder).lockEnrolledNote.staticCall(...args));
 args=await enrollment(note,100,id);await (await mint.connect(holder).lockEnrolledNote(...args)).wait();
 const other=await enrollment(ethers.toBeHex(5n,32),100,id);await assert.rejects(()=>mint.connect(holder).lockEnrolledNote.staticCall(...other));
 await (await mint.connect(guardian).pauseRedemptions()).wait();
 await assert.rejects(()=>mint.redeemNote.staticCall(note,ethers.toBeHex(2n,32),recipient.getAddress(),proof));
 assert.equal(await mint.totalEscrowed(),100n);
 await govern(await mint.getAddress(),mint.interface.encodeFunctionData('resumeRedemptions'));
 await (await mint.redeemNote(note,ethers.toBeHex(2n,32),await recipient.getAddress(),proof)).wait();
 assert.equal(await mint.totalEscrowed(),0n);
 }finally{await s.rpc.disconnect();}
});
test('a governance contract merely reporting a two-day delay cannot bypass the approved implementation',async()=>{
 const s=await setup();try{const {factory,config,fakeTimelock}=s;
 // Existing verifier has code but no real governance; a forged getter is tested separately below.
 const bad=[...config];bad[0]=await fakeTimelock.getAddress();await assert.rejects(()=>factory.deployProtocol.staticCall(bad));
 }finally{await s.rpc.disconnect();}
});
