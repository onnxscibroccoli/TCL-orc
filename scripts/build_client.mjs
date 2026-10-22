import {build} from 'esbuild';
import {mkdirSync,writeFileSync,readFileSync,copyFileSync,existsSync} from 'node:fs';
import {createHash} from 'node:crypto';
mkdirSync('build/client',{recursive:true});
for(const name of ['wallet','prover.worker','view','app'])await build({entryPoints:[`client/${name}.mjs`],outfile:`build/client/${name}.js`,bundle:true,platform:'browser',format:'esm',target:'es2022',sourcemap:false});
const assets={};
for(const [name,path] of Object.entries({wasm:'build/zk/puf_verify_js/puf_verify.wasm',zkey:'build/zk/circuit.zkey',vk:'build/zk/verification_key.json'})){
 const bytes=readFileSync(path),file=path.split('/').at(-1);copyFileSync(path,'build/client/'+file);
 assets[name]={url:'./'+file,bytes:bytes.byteLength,sha256:createHash('sha256').update(bytes).digest('hex')};
}
writeFileSync('build/client/assets.json',JSON.stringify(assets,null,2)+'\n');
if(existsSync('client/index.html'))copyFileSync('client/index.html','build/client/index.html');

const sha256={};
for(const file of ['wallet.js','prover.worker.js','app.js','view.js','index.html','assets.json'])sha256[file]=createHash('sha256').update(readFileSync('build/client/'+file)).digest('hex');
writeFileSync('build/client/manifest.json',JSON.stringify({purpose:'LOCAL TEST ONLY: browser client artifacts, not production ceremony',assets,sha256},null,2)+'\n');
