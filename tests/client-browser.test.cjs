const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const http=require('node:http');
const path=require('node:path');
const {chromium}=require('playwright');
const ganache=require('ganache');
const solc=require('solc');
const {ethers}=require('ethers');
const {buildPoseidon}=require('circomlibjs');

test('real browser worker proves and redeems; context, artifact and RPC faults invalidate sessions', {timeout:240000},async()=>{
 const rpc=ganache.provider({logging:{quiet:true},miner:{timestampIncrement:0}});
 const requests=[];let browser,server;
 try{
  const provider=new ethers.BrowserProvider(rpc);provider.pollingInterval=10;
  const gov=await provider.getSigner(0),alice=await provider.getSigner(1),bob=await provider.getSigner(2);
  const sources=Object.fromEntries(['CirculatoryEngine.sol','PhysicalMintController.sol'].map(n=>[n,{content:fs.readFileSync('contracts/'+n,'utf8')}]));
  sources['PufVerifier.sol']={content:fs.readFileSync('build/zk/PufVerifier.sol','utf8')};
  const compiled=JSON.parse(solc.compile(JSON.stringify({language:'Solidity',sources,settings:{evmVersion:'shanghai',optimizer:{enabled:true,runs:200},outputSelection:{'*':{'*':['abi','evm.bytecode.object']}}}})));
  assert.deepEqual((compiled.errors||[]).filter(e=>e.severity==='error'),[]);
  async function deploy(file,name,args=[]){const c=compiled.contracts[file][name];const v=await new ethers.ContractFactory(c.abi,c.evm.bytecode.object,gov).deploy(...args);await v.waitForDeployment();return v;}
  const engine=await deploy('CirculatoryEngine.sol','CirculatoryEngine',[await gov.getAddress(),await alice.getAddress(),ethers.parseEther('10000000')]);
  const verifier=await deploy('PufVerifier.sol','Groth16Verifier'),mint=await deploy('PhysicalMintController.sol','PhysicalMintController',[await engine.getAddress(),await verifier.getAddress()]);
  await(await engine.setPhysicalMint(await mint.getAddress())).wait();
  const context=(await mint.context()).toString(),recipient=await bob.getAddress();
  const poseidon=await buildPoseidon(),hash=a=>poseidon.F.toObject(poseidon(a)).toString();
  const secret='123456789',blinding='987654321',value='1000',commitment=hash([1,secret,blinding,value,context]);
  const commitment2=hash([1,secret,(BigInt(blinding)+1n).toString(),value,context]);
  await(await engine.connect(alice).approve(await mint.getAddress(),2000)).wait();
  for(const cm of [commitment,commitment2])await(await mint.connect(alice).lockNote(ethers.toBeHex(BigInt(cm),32),value)).wait();
  await rpc.request({method:'evm_increaseTime',params:[86400]});await rpc.request({method:'evm_mine',params:[]});
  const policy={ledgerCodeHash:ethers.keccak256(await provider.getCode(await engine.getAddress())),chainId:(await provider.getNetwork()).chainId.toString(),controller:await mint.getAddress(),controllerCodeHash:ethers.keccak256(await provider.getCode(await mint.getAddress())),verifier:await verifier.getAddress(),verifierCodeHash:ethers.keccak256(await provider.getCode(await verifier.getAddress())),assets:JSON.parse(fs.readFileSync('build/client/assets.json'))};
  server=http.createServer(async(req,res)=>{
   const entry={method:req.method,url:req.url};requests.push(entry);
   res.setHeader('Cache-Control','no-store');
   if(req.url==='/rpc'&&req.method==='POST'){
    let text='';for await(const b of req){text+=b;if(text.length>20000){res.writeHead(413);res.end();return;}}
    entry.body=text;
    try{const args=JSON.parse(text);if(!['eth_chainId','eth_getCode','eth_call','eth_getBlockByNumber','eth_estimateGas','eth_sendTransaction'].includes(args.method))throw Error();const result=await rpc.request(args);res.setHeader('Content-Type','application/json');res.end(JSON.stringify({result}));}
    catch{res.setHeader('Content-Type','application/json');res.end(JSON.stringify({error:'RPC_REJECTED'}));}return;
   }
   const file=path.resolve('build/client','.'+req.url.split('?')[0]);
   if(!file.startsWith(path.resolve('build/client')+path.sep)){res.writeHead(403);res.end();return;}
   try{res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.json')?'application/json':file.endsWith('.html')?'text/html':'application/octet-stream');res.end(fs.readFileSync(file));}catch{res.writeHead(404);res.end();}
  });await new Promise(r=>server.listen(0,'127.0.0.1',r));
  browser=await chromium.launch({executablePath:process.env.TCL_CHROMIUM||'/usr/bin/chromium',headless:true,args:['--disable-dev-shm-usage']});
  const page=await browser.newPage();const origin='http://127.0.0.1:'+server.address().port;
  await page.goto(origin+'/index.html');
  const result=await page.evaluate(async({policy,note,note2,recipient,secret,blinding,holder})=>{
   const {WalletClient}=await import('./wallet.js');const listeners={};
   const provider={on:(event,fn)=>(listeners[event]??=[]).push(fn),removeListener:()=>{},request:async args=>{const r=await(await fetch('/rpc',{method:'POST',body:JSON.stringify(args)})).json();if(r.error)throw Error(r.error);return r.result;}};
   const bytes=(blind=blinding)=>{const b=new Uint8Array(64);b.set(Uint8Array.from(BigInt(secret).toString(16).padStart(64,'0').match(/../g),h=>parseInt(h,16)));b.set(Uint8Array.from(BigInt(blind).toString(16).padStart(64,'0').match(/../g),h=>parseInt(h,16)),32);return b.buffer;};
   const client=new WalletClient(provider,policy);const snapshot=await client.readBalance(holder);
   const {renderBalance}=await import('./view.js');const displayed=renderBalance(document,snapshot,0);
   if(document.getElementById('committed').textContent===document.getElementById('estimated').textContent||BigInt(displayed.estimate)>=BigInt(displayed.committed))throw Error('committed and unsettled balances not distinguished');
   const buffer=bytes();let ticks=0;const timer=setInterval(()=>ticks++,5);
   const proof=await client.prove(buffer,note,recipient);clearInterval(timer);
   if(buffer.byteLength!==0)throw Error('witness buffer not transferred');
   if(ticks<1)throw Error('main event loop blocked');
   const submission=client.submit(recipient);let concurrentBlocked=false,inflightProofBlocked=false;try{await client.submit(recipient);}catch{concurrentBlocked=true;}try{await client.prove(bytes(),note,recipient);}catch{inflightProofBlocked=true;}const tx=await submission;let repeatBlocked=false;
   try{await client.submit(recipient);}catch{repeatBlocked=true;}
   client.close();
   const badPolicy=structuredClone(policy);badPolicy.assets.wasm.sha256='0'.repeat(64);const corrupt=new WalletClient(provider,badPolicy);let artifactRejected=false;
   try{await corrupt.prove(bytes(),note,recipient,{timeoutMs:30000});}catch{artifactRejected=true;}corrupt.close();
   const timeoutClient=new WalletClient(provider,policy);let timeoutRejected=false;
   try{await timeoutClient.prove(bytes(),note,recipient,{timeoutMs:1});}catch{timeoutRejected=true;}timeoutClient.close();
   const stale=new WalletClient(provider,policy);const pending=stale.prove(bytes(),note,recipient,{timeoutMs:30000});
   for(const fn of listeners.chainChanged??[])fn('0x2');let chainRejected=false;
   try{await pending;}catch{chainRejected=true;}stale.close();
   const rejected=new WalletClient({...provider,request:async args=>{
    if(args.method==='eth_estimateGas'){
     args=structuredClone(args);const raw=args.params[0].data;
     args.params[0].data=raw.slice(0,-1)+(parseInt(raw.at(-1),16)^1).toString(16);
    }
    return provider.request(args);
   }},policy);
   const rejectedProof=await rejected.prove(bytes((BigInt(blinding)+1n).toString()),note2,recipient,{timeoutMs:30000});let rpcRejected=false;
   try{await rejected.submit(recipient);}catch{rpcRejected=true;}const state=rejected.state;
   let regenerationRequired=false;try{await rejected.prove(bytes(),note,recipient);}catch{regenerationRequired=true;}rejected.close();
   return{tx,proof,concurrentBlocked,inflightProofBlocked,rejectedSignals:rejectedProof.signals,repeatBlocked,artifactRejected,timeoutRejected,chainRejected,rpcRejected,regenerationRequired,state,ticks};
  },{policy,note:{commitment,value,context},note2:{commitment:commitment2,value,context},recipient,secret,blinding,holder:await alice.getAddress()});
  for(const k of ['concurrentBlocked','inflightProofBlocked','repeatBlocked','artifactRejected','timeoutRejected','chainRejected','rpcRejected','regenerationRequired'])assert.equal(result[k],true,k);
  assert.equal(result.state,'uncertain');assert.equal(await engine.balances(recipient),1000n);assert.equal(await mint.totalEscrowed(),1000n);
  assert.equal((await mint.notes(ethers.toBeHex(BigInt(commitment2),32))).locked,true);
  assert.equal(await mint.usedNullifiers(ethers.toBeHex(BigInt(result.rejectedSignals[1]),32)),false);
  assert.equal((await provider.getTransactionReceipt(result.tx)).status,1);
  assert.equal(requests.filter(r=>r.body&&JSON.parse(r.body).method==='eth_sendTransaction').length,1,'no automatic transaction resubmission');
  for(const req of requests){assert.ok(!req.body?.includes(secret)&&!req.body?.includes(blinding),'private witness leaked to RPC');assert.ok(req.url!=='/private');}
  const metrics={purpose:'synthetic browser-worker acceptance; not PUF or production custody qualification',elapsedMs:result.proof.elapsedMs,latencyTargetMs:4500,latencyTargetPassed:result.proof.elapsedMs<4500,mainThreadTicks:result.ticks,browserVersion:await browser.version(),browserSandbox:'Playwright CI default; outer rootless OCI isolation is required',faults:{artifact:true,timeout:true,chain:true,rpc:true},networkRequests:requests.length,proofSha256:require('node:crypto').createHash('sha256').update(result.proof.proof).digest('hex'),workerMemoryLimit:'Browser API supplies no hard per-worker RAM cap; 256 MiB target not certified'};
  for(const [key,file] of Object.entries({containerMemoryMax:'/sys/fs/cgroup/memory.max',containerMemoryPeak:'/sys/fs/cgroup/memory.peak',containerCpuMax:'/sys/fs/cgroup/cpu.max'}))if(fs.existsSync(file))metrics[key]=fs.readFileSync(file,'utf8').trim();
  if(process.env.TCL_CONSTRAINED==='1'){assert.equal(metrics.containerMemoryMax,String(2*1024**3),'actual two-GiB cgroup');const [quota,period]=metrics.containerCpuMax.split(' ').map(Number);assert.ok(quota/period<=2,'actual CPU quota');}
  fs.mkdirSync('build/audit',{recursive:true});fs.writeFileSync('build/audit/client-browser.json',JSON.stringify(metrics,null,2)+'\n');
  assert.equal(metrics.latencyTargetPassed,true,'requested 4500ms cold worker latency target');
 }finally{await browser?.close();await new Promise(r=>server?server.close(r):r());await rpc.disconnect();}
});
