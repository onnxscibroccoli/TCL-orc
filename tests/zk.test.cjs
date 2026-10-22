const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {execFileSync}=require('node:child_process');
const {buildPoseidon}=require('circomlibjs');
const solc=require('solc');
const ganache=require('ganache');
const {ethers}=require('ethers');
const base='build/zk/';
function cli(args){return execFileSync(process.execPath,['node_modules/snarkjs/build/cli.cjs',...args],{encoding:'utf8',timeout:120000});}
function witness(input,path){fs.writeFileSync(base+'input.json',JSON.stringify(input));return execFileSync(process.execPath,[base+'puf_verify_js/generate_witness.js',base+'puf_verify_js/puf_verify.wasm',base+'input.json',path],{encoding:'utf8',stdio:'pipe'});}

test('real Groth16 proof redeems escrow; mutated signals, witnesses and proof fail', {timeout:240000},async()=>{
 const rpc=ganache.provider({logging:{quiet:true},miner:{timestampIncrement:0}});
 try{
 const provider=new ethers.BrowserProvider(rpc);provider.pollingInterval=10;
 const gov=await provider.getSigner(0),alice=await provider.getSigner(1),bob=await provider.getSigner(2);
 const sources=Object.fromEntries(['CirculatoryEngine.sol','PhysicalMintController.sol'].map(x=>[x,{content:fs.readFileSync('contracts/'+x,'utf8')}]));
 sources['PufVerifier.sol']={content:fs.readFileSync(base+'PufVerifier.sol','utf8')};
 const compiled=JSON.parse(solc.compile(JSON.stringify({language:'Solidity',sources,settings:{evmVersion:'shanghai',optimizer:{enabled:true,runs:200},outputSelection:{'*':{'*':['abi','evm.bytecode.object']}}}})));
 assert.deepEqual((compiled.errors||[]).filter(x=>x.severity==='error'),[]);
 async function deploy(file,name,args=[]){const c=compiled.contracts[file][name];const d=await new ethers.ContractFactory(c.abi,c.evm.bytecode.object,gov).deploy(...args);await d.waitForDeployment();return d;}
 const engine=await deploy('CirculatoryEngine.sol','CirculatoryEngine',[await gov.getAddress(),await alice.getAddress(),ethers.parseEther('1000000')]);
 const verifier=await deploy('PufVerifier.sol','Groth16Verifier');
 const mint=await deploy('PhysicalMintController.sol','PhysicalMintController',[await engine.getAddress(),await verifier.getAddress()]);
 await (await engine.setPhysicalMint(await mint.getAddress())).wait();
 const poseidon=await buildPoseidon();const hash=values=>poseidon.F.toObject(poseidon(values)).toString();
 const secret='123456789',blinding='987654321',value='1000',context=(await mint.context()).toString(),recipient=BigInt(await bob.getAddress()).toString();
 const commitment=hash([1,secret,blinding,value,context]);
 const nullifier=hash([2,secret,commitment,context,recipient]);
 const input={secret,blinding,commitment,nullifier,value,recipient,context};
 witness(input,base+'witness.wtns');
 cli(['wtns','check',base+'puf_verify.r1cs',base+'witness.wtns']);
 for(const [key,val] of Object.entries({secret:'9',blinding:'9',value:'1001',recipient:'1',context:'1',nullifier:'1',commitment:'1'})){
   assert.throws(()=>witness({...input,[key]:val},base+'bad.wtns'),undefined,'changed '+key+' must fail');
 }
 cli(['groth16','prove',base+'circuit.zkey',base+'witness.wtns',base+'proof.json',base+'public.json']);
 assert.match(cli(['groth16','verify',base+'verification_key.json',base+'public.json',base+'proof.json']),/OK/);
 const publicSignals=JSON.parse(fs.readFileSync(base+'public.json'));
 assert.deepEqual(publicSignals,[commitment,nullifier,value,recipient,context]);
 const calldata=JSON.parse('['+cli(['zkey','export','soliditycalldata',base+'public.json',base+'proof.json'])+']');
 const [a,b,c,signals]=calldata;
 assert.equal(await verifier.verifyProof(a,b,c,signals),true);
 for(let i=0;i<signals.length;i++){const changed=[...signals];changed[i]='0x'+(BigInt(changed[i])+1n).toString(16);assert.equal(await verifier.verifyProof(a,b,c,changed),false,'mutated signal '+i);}
 const corrupted=[...a];corrupted[0]=ethers.ZeroHash;assert.equal(await verifier.verifyProof(corrupted,b,c,signals),false);
 const proof=ethers.AbiCoder.defaultAbiCoder().encode(['uint256[2]','uint256[2][2]','uint256[2]'],[a,b,c]);
 const note=ethers.toBeHex(BigInt(commitment),32),spent=ethers.toBeHex(BigInt(nullifier),32);
 await (await engine.connect(alice).approve(await mint.getAddress(),value)).wait();
 await (await mint.connect(alice).lockNote(note,value)).wait();
 await assert.rejects(()=>mint.redeemNote.staticCall(note,spent,awaitAddress(alice),proof));
 await (await mint.redeemNote(note,spent,await bob.getAddress(),proof)).wait();
 assert.equal(await engine.balances(await bob.getAddress()),1000n);
 assert.equal(await mint.totalEscrowed(),0n);
 await assert.rejects(()=>mint.redeemNote.staticCall(note,spent,awaitAddress(bob),proof));
 }finally{await rpc.disconnect();}
});
function awaitAddress(s){return s.getAddress();}
