import {estimateBalance,THRESHOLD} from './protocol.mjs';
export function balanceDisplay(snapshot,ageSeconds){
 if(!Number.isSafeInteger(ageSeconds)||ageSeconds<0)throw Error('INVALID_DISPLAY_AGE');
 const age=BigInt(Math.min(ageSeconds,60));
 const elapsed=BigInt(snapshot.blockTimestamp)-BigInt(snapshot.lastSettled)+age;
 if(elapsed<0n)throw Error('INVALID_CHAIN_TIME');
 const [estimate]=estimateBalance(snapshot.committed,elapsed.toString(),'0',{exempt:snapshot.exempt,initialized:snapshot.initialized});
 return{committed:snapshot.committed,estimate:estimate.toString(),stale:ageSeconds>60,
  roundingUncertaintyWei:BigInt(snapshot.committed)>THRESHOLD&&!snapshot.exempt&&snapshot.initialized&&elapsed>0n?'1':'0'};
}
export function renderBalance(document,snapshot,ageSeconds){
 const view=balanceDisplay(snapshot,ageSeconds);
 document.getElementById('committed').textContent=view.committed+' wei';
 document.getElementById('estimated').textContent=view.estimate+' wei (unsettled estimate)';
 document.getElementById('estimate-status').textContent=(view.stale?'Stale: refresh chain snapshot. ':'')+'Public rounding uncertainty: up to '+view.roundingUncertaintyWei+' wei. Block '+snapshot.blockNumber+'. Local clock interpolation does not predict future block timestamps.';
 return view;
}
