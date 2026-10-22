import {AbiCoder,keccak256,getAddress,Interface} from 'ethers';
export const FIELD=21888242871839275222246405745257275088548364400416034343698204186575808495617n;
export const THRESHOLD=5000000n*10n**18n;
export const SCALE=10n**27n;
export const SECOND_FACTOR=999999994212962979707861764n;
export const SIGNAL_ORDER=['commitment','nullifier','value','recipient','context'];
export const controllerABI=new Interface([
 'function context() view returns(uint256)',
 'function verifier() view returns(address)',
 'function circulatoryEngine() view returns(address)',
 'function notes(bytes32) view returns(bool exists,bool locked,uint128 value)',
 'function redeemNote(bytes32 commitment,bytes32 nullifier,address recipient,bytes proof)'
]);
export function unsigned(value,max){
 if(typeof value!=='string'||! /^(0|[1-9][0-9]*)$/.test(value))throw new Error('CANONICAL_INTEGER_REQUIRED');
 const n=BigInt(value);if(n>max)throw new Error('INTEGER_RANGE');return n;
}
export function contextDigest(chainId,controller){
 const chain=unsigned(String(chainId),2n**256n-1n);if(!chain)throw new Error('CHAIN_REQUIRED');
 const address=getAddress(controller);if(BigInt(address)===0n)throw new Error('CONTROLLER_REQUIRED');
 return (BigInt(keccak256(AbiCoder.defaultAbiCoder().encode(['uint256','address'],[chain,address])))%FIELD).toString();
}
export function validatePublic(note){
 for(const k of SIGNAL_ORDER){const n=unsigned(note[k],k==='value'?2n**128n-1n:k==='recipient'?2n**160n-1n:FIELD-1n);if(k!=='context'&&!n)throw new Error('NONZERO_REQUIRED');}
 return note;
}
export function publicSignals(note){validatePublic(note);return SIGNAL_ORDER.map(k=>note[k]);}
export function estimateBalance(balance,elapsedSeconds,remainder='0',{exempt=false,initialized=true}={}){
 let b=unsigned(balance,2n**128n-1n),t=unsigned(elapsedSeconds,2n**256n-1n),r=unsigned(remainder,SCALE-1n);
 if(b<=THRESHOLD||exempt||!initialized)return[b,0n,0n];
 let factor=SCALE,base=SECOND_FACTOR;
 while(t){if(t&1n)factor=factor*base/SCALE;t>>=1n;if(t)base=base*base/SCALE;}
 const surplus=b-THRESHOLD,numerator=surplus*(SCALE-factor)+r;let fee=numerator/SCALE;r=numerator%SCALE;
 if(fee>=surplus){fee=surplus;r=0n;}return[b-fee,fee,r];
}
