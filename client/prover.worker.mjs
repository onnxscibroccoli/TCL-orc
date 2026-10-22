import {groth16} from 'snarkjs';
import {buildPoseidon} from '../node_modules/circomlibjs/src/poseidon_wasm.js';
import {FIELD,publicSignals,unsigned} from './protocol.mjs';

async function asset(spec){
 if(!spec||!Number.isSafeInteger(spec.bytes)||spec.bytes<1||spec.bytes>32*1024*1024||!/^[a-f0-9]{64}$/.test(spec.sha256))throw Error();
 const url=new URL(spec.url,self.location.href);if(url.origin!==self.location.origin)throw Error();
 const response=await fetch(url,{credentials:'omit',redirect:'error',cache:'no-store'});if(!response.ok)throw Error();
 const reader=response.body.getReader(),chunks=[];let length=0;
 try{for(;;){const {done,value}=await reader.read();if(done)break;length+=value.byteLength;if(length>spec.bytes)throw Error();chunks.push(value);}}
 finally{await reader.cancel();}
 if(length!==spec.bytes)throw Error();const bytes=new Uint8Array(length);let offset=0;
 for(const chunk of chunks){bytes.set(chunk,offset);offset+=chunk.byteLength;}
 const hash=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),b=>b.toString(16).padStart(2,'0')).join('');
 if(hash!==spec.sha256)throw Error();return bytes;
}
let used=false;
self.onmessage=async({data})=>{
 if(used)return;used=true;let privateBytes;
 try{
  const start=performance.now();privateBytes=new Uint8Array(data.privateBytes);if(privateBytes.byteLength!==64)throw Error();
  const scalar=part=>BigInt('0x'+Array.from(part,b=>b.toString(16).padStart(2,'0')).join(''));
  const secret=scalar(privateBytes.subarray(0,32)),blinding=scalar(privateBytes.subarray(32));
  if(!secret||secret>=FIELD||blinding>=FIELD)throw Error();
  const note={...data.note};unsigned(note.value,2n**128n-1n);unsigned(note.recipient,2n**160n-1n);unsigned(note.context,FIELD-1n);
  const poseidon=await buildPoseidon(),hash=a=>poseidon.F.toObject(poseidon(a)).toString();
  if(note.commitment!==hash([1,secret,blinding,note.value,note.context]))throw Error();
  note.nullifier=hash([2,secret,note.commitment,note.context,note.recipient]);const expected=publicSignals(note);
  const [wasm,zkey,vkBytes]=await Promise.all([asset(data.assets.wasm),asset(data.assets.zkey),asset(data.assets.vk)]);
  const {proof,publicSignals:signals}=await groth16.fullProve({...note,secret:secret.toString(),blinding:blinding.toString()},wasm,zkey,undefined,{memorySize:256},{singleThread:true});
  if(JSON.stringify(signals)!==JSON.stringify(expected)||!await groth16.verify(JSON.parse(new TextDecoder().decode(vkBytes)),signals,proof))throw Error();
  const calldata=JSON.parse('['+await groth16.exportSolidityCallData(proof,signals)+']');
  self.postMessage({ok:true,signals,calldata,elapsedMs:performance.now()-start});
 }catch{self.postMessage({ok:false,error:'PROOF_FAILED'});}
 finally{privateBytes?.fill(0);self.onmessage=null;}
};
