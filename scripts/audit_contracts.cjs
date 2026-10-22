#!/usr/bin/env node
'use strict';
// Real-source compilation audit. This does NOT run solc SMTChecker.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const cp = require('node:child_process');
const solc = require('solc');
const root = path.resolve(__dirname, '..');
const hash = data => crypto.createHash('sha256').update(data).digest('hex');
function solidityFiles(directory) {
  return fs.readdirSync(path.join(root, directory), {withFileTypes: true}).flatMap(entry => {
    const relative = `${directory}/${entry.name}`;
    return entry.isDirectory() ? solidityFiles(relative) : entry.name.endsWith('.sol') ? [relative] : [];
  }).sort();
}
const sourceFiles = solidityFiles('contracts');
const importedSources = {};
if (fs.existsSync(path.join(root, 'build/zk/PufVerifier.sol'))) sourceFiles.push('build/zk/PufVerifier.sol');
const sources = Object.fromEntries(sourceFiles.map(f => [f, {content: fs.readFileSync(path.join(root, f), 'utf8')}]));
const input = {language: 'Solidity', sources, settings: {
  optimizer: {enabled: true, runs: 200}, evmVersion: 'shanghai',
  outputSelection: {'*': {'*': ['abi', 'evm.bytecode.object', 'evm.deployedBytecode.object', 'evm.deployedBytecode.immutableReferences', 'storageLayout']}}
}};
const output = JSON.parse(solc.compile(JSON.stringify(input), {import: importPath => {
  const resolved = path.resolve(root, 'node_modules', importPath);
  if (!resolved.startsWith(path.join(root, 'node_modules') + path.sep)) return {error: 'Import outside dependencies'};
  try {
    const content = fs.readFileSync(resolved, 'utf8');
    importedSources[importPath] = hash(content);
    return {contents: content};
  } catch (error) { return {error: `Cannot resolve ${importPath}: ${error.message}`}; }
}}));
for (const diagnostic of output.errors || []) process.stderr.write(diagnostic.formattedMessage + '\n');
if ((output.errors || []).some(e => e.severity === 'error')) process.exit(1);
// Run proof obligations against the exact model recorded in the provenance manifest.
const proof = cp.spawnSync(process.env.PYTHON || 'python3', ['-m', 'verification.ledger_model'], {cwd: root, encoding: 'utf8'});
if (proof.status !== 0) { process.stderr.write(proof.stderr || proof.stdout); process.exit(1); }
const obligations = JSON.parse(proof.stdout);
if (!Object.values(obligations).every(v => v === 'unsat')) throw new Error('Unproved model obligation');
const contracts = {};
for (const [file, entries] of Object.entries(output.contracts)) {
  for (const [name, artifact] of Object.entries(entries)) {
    contracts[`${file}:${name}`] = {
      creationBytecode: artifact.evm.bytecode.object,
      creationBytecodeSha256: hash(Buffer.from(artifact.evm.bytecode.object, 'hex')),
      runtimeBytecodeTemplate: artifact.evm.deployedBytecode.object,
      runtimeBytecodeTemplateSha256: hash(Buffer.from(artifact.evm.deployedBytecode.object, 'hex')),
      immutableReferences: artifact.evm.deployedBytecode.immutableReferences,
      storageLayout: artifact.storageLayout, abi: artifact.abi
    };
  }
}
const report = {
  format: 1, compiler: solc.version(), compilerInputSha256: hash(JSON.stringify(input)),
  sources: Object.fromEntries(sourceFiles.map(f => [f, hash(sources[f].content)])),
  importedSources,
  modelSha256: hash(fs.readFileSync(path.join(root, 'verification/ledger_model.py'))),
  modelRequirementsSha256: hash(fs.readFileSync(path.join(root, 'verification/requirements.txt'))),
  obligations, assurance: 'Symbolic abstraction plus real-source compilation; no EVM refinement proof or SMTChecker claim.',
  deploymentNote: 'Runtime bytecode is a compiler template: constructor immutable substitutions must be verified against on-chain code before asserting deployment provenance.',
  contracts
};
const directory = path.join(root, 'build/audit');
fs.mkdirSync(directory, {recursive: true});
fs.writeFileSync(path.join(directory, 'contracts.json'), JSON.stringify(report, null, 2) + '\n');
process.stdout.write(`Compiled ${sourceFiles.length} real source files; ${Object.keys(obligations).length} symbolic obligations UNSAT. Provenance: build/audit/contracts.json\n`);
