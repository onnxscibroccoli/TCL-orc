#!/usr/bin/env python3
"""Run the full protocol check in a real rootless, offline, immutable CI image."""
import argparse
import hashlib
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import tempfile
import uuid


def validate_image(image):
    if not re.fullmatch(r'sha256:[a-f0-9]{64}',image):
        raise ValueError('immutable local image ID required')
    return image


def runtime_command(image,name,evidence):
    validate_image(image)
    return ['docker','run','--name',name,'--read-only','--network=none',
        '--cap-drop=ALL','--security-opt=no-new-privileges','--user=65532:65532',
        '--memory=4g','--cpus=2','--pids-limit=512',
        '--tmpfs','/work:rw,exec,nosuid,size=2g,mode=1777',
        '--tmpfs','/tmp:rw,exec,nosuid,size=2g,mode=1777',
        '--mount',f'type=bind,src={Path(evidence).resolve()},dst=/evidence',image]


def client_runtime_command(image,name,evidence):
    command=runtime_command(image,name,evidence)
    command[command.index('--memory=4g')]='--memory=2g'
    command[-1:-1]=['--env','TCL_CONSTRAINED=1','--entrypoint','bash']
    return command+['-euc','cp -R /opt/protocol/. /work/; cd /work; npm run zk:setup; npm run client:build; npm run test:client:browser; cp build/audit/client-browser.json /evidence/client-browser.json']


def sha(data):return hashlib.sha256(data).hexdigest()


def artifact_hashes(directory):
    result={}
    for path in sorted(Path(directory).iterdir()):
        info=path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError('container evidence must be regular single-link files')
        # Called only after container termination, preventing replacement races.
        with path.open('rb') as stream:result[path.name]=sha(stream.read())
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-image',required=True)
    parser.add_argument('--python-image',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if not all(re.fullmatch(r'[^\s,@]+@sha256:[a-f0-9]{64}',image) for image in (args.base_image,args.python_image)):
        parser.error('digest-pinned base image required')
    root=Path(__file__).resolve().parents[1]
    def git(*argv):return subprocess.check_output(['git','-C',str(root),*argv])
    if git('status','--porcelain').strip():raise ValueError('validation requires a clean committed source tree')
    security=json.loads(subprocess.check_output(['docker','info','--format','{{json .SecurityOptions}}']))
    if 'name=rootless' not in security:raise ValueError('real rootless daemon required; no fallback or skip')
    revision=git('rev-parse','HEAD').decode().strip()
    archive=git('archive','--format=tar','HEAD')
    output=args.output.resolve();output.mkdir(parents=True,exist_ok=True)
    if any(output.iterdir()):raise ValueError('fresh evidence directory required')
    # The disposable container maps UID 65532 through rootless user namespaces.
    # Only this synthetic CI output directory is writable; no secrets are mounted.
    artifacts=output/'artifacts';artifacts.mkdir(mode=0o777);artifacts.chmod(0o777)
    name='tcl-validation-'+uuid.uuid4().hex
    names=[name];artifact_roots={'artifact_sha256':artifacts}
    report={'purpose':'synthetic CI acceptance; not production release approval',
        'revision':revision,'source_archive_sha256':sha(archive),'base_image':args.base_image,'python_image':args.python_image,
        'rootless_security_options':security,'status':'failed'}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            iid=Path(tmp)/'image-id'
            subprocess.run(['docker','build','--build-arg','BASE_IMAGE='+args.base_image,'--build-arg','PYTHON_IMAGE='+args.python_image,
                '--build-arg','SOURCE_REVISION='+revision,'--build-arg','SOURCE_ARCHIVE_SHA256='+sha(archive),
                '--iidfile',str(iid),'-f','Containerfile','-'],input=archive,check=True,timeout=1200)
            image=validate_image(iid.read_text().strip());report['image_id']=image
            report['image_inspect']=json.loads(subprocess.check_output(['docker','image','inspect',image]))[0]
            # Preserve the exact dependency-complete image for offline replay.
            image_tar=Path(tmp)/'image.tar'
            subprocess.run(['docker','save','--output',str(image_tar),image],check=True,timeout=300)
            with image_tar.open('rb') as source, gzip.open(output/'validation-image.tar.gz','wb') as target:
                shutil.copyfileobj(source,target)
            with (output/'validation-image.tar.gz').open('rb') as stream:
                report['image_archive_sha256']=hashlib.file_digest(stream,'sha256').hexdigest()
        command=runtime_command(image,name,artifacts);report['command']=command
        result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=1200)
        (output/'validation.log').write_bytes(result.stdout)
        print(result.stdout.decode(errors='replace'),end='')
        report['exit_code']=result.returncode
        report['container_inspect']=json.loads(subprocess.check_output(['docker','inspect',name]))[0]
        if result.returncode:raise RuntimeError('containerized protocol check failed')
        if re.search(rb'(?m)^(?:#|\xe2\x84\xb9) skipped [1-9]|\bskipped=[1-9]',result.stdout):
            raise RuntimeError('skipped tests cannot establish container acceptance')
        artifact_hashes(artifacts)
        for required in ('contracts.json','zk-manifest.json','verification_key.json','python-packages.txt','node-version.txt','python-version.txt','client-browser.json','client-manifest.json'):
            if not (artifacts/required).is_file() or not (artifacts/required).stat().st_size:
                raise ValueError('missing operational evidence: '+required)
        client_artifacts=output/'client-artifacts';client_artifacts.mkdir(mode=0o777);client_artifacts.chmod(0o777)
        artifact_roots['client_artifact_sha256']=client_artifacts
        client_name=name+'-client';names.append(client_name)
        client_command=client_runtime_command(image,client_name,client_artifacts)
        client_result=subprocess.run(client_command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=600)
        (output/'client-validation.log').write_bytes(client_result.stdout)
        print(client_result.stdout.decode(errors='replace'),end='')
        report['constrained_client']={'command':client_command,'exit_code':client_result.returncode,
            'inspect':json.loads(subprocess.check_output(['docker','inspect',client_name]))[0]}
        if client_result.returncode:raise RuntimeError('two-GiB client acceptance failed')
        artifact_hashes(client_artifacts)
        if not (client_artifacts/'client-browser.json').is_file():raise ValueError('missing constrained browser evidence')
        report['status']='passed'
    finally:
        cleanup_results={}
        for container in names:
            cleanup=subprocess.run(['docker','rm','-f',container],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            cleanup_results[container]=cleanup.returncode
        report['cleanup_exit_codes']=cleanup_results
        for label,directory in artifact_roots.items():
            if any(cleanup_results.values()):
                report['status']='failed'
                quarantine=output.parent/('uncollected-'+uuid.uuid4().hex)
                directory.rename(quarantine);directory.mkdir()
            try:report[label]=artifact_hashes(directory)
            except (ValueError,OSError):
                shutil.rmtree(directory);directory.mkdir()
                report['status']='failed';report['unsafe_artifacts_rejected']=True
        for log in ('validation.log','client-validation.log'):
            if (output/log).is_file():report[log+'_sha256']=sha((output/log).read_bytes())
        (output/'provenance.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    if report['status']!='passed':raise RuntimeError('container cleanup failed')

if __name__=='__main__':main()
