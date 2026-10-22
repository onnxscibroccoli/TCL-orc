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
        '--tmpfs','/tmp:rw,exec,nosuid,size=256m,mode=1777',
        '--mount',f'type=bind,src={Path(evidence).resolve()},dst=/evidence',image]


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
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    if not re.fullmatch(r'[^\s,@]+@sha256:[a-f0-9]{64}',args.base_image):
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
    report={'purpose':'synthetic CI acceptance; not production release approval',
        'revision':revision,'source_archive_sha256':sha(archive),'base_image':args.base_image,
        'rootless_security_options':security,'status':'failed'}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            iid=Path(tmp)/'image-id'
            subprocess.run(['docker','build','--build-arg','BASE_IMAGE='+args.base_image,
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
        for required in ('contracts.json','zk-manifest.json','verification_key.json','python-packages.txt','node-version.txt'):
            if not (artifacts/required).is_file() or not (artifacts/required).stat().st_size:
                raise ValueError('missing operational evidence: '+required)
        report['status']='passed'
    finally:
        cleanup=subprocess.run(['docker','rm','-f',name],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        report['cleanup_exit_code']=cleanup.returncode
        if cleanup.returncode:
            report['status']='failed'
            # Detach the uploader's directory from a possibly live container.
            quarantine=output.parent/('uncollected-'+uuid.uuid4().hex)
            artifacts.rename(quarantine);artifacts.mkdir()
        try:
            report['artifact_sha256']=artifact_hashes(artifacts)
        except (ValueError,OSError):
            # Never leave attacker-created links for the artifact uploader.
            shutil.rmtree(artifacts);artifacts.mkdir()
            report['status']='failed';report['unsafe_artifacts_rejected']=True
        if (output/'validation.log').is_file():
            report['validation_log_sha256']=sha((output/'validation.log').read_bytes())
        (output/'provenance.json').write_text(json.dumps(report,sort_keys=True,indent=2)+'\n')
    if report['status']!='passed':raise RuntimeError('container cleanup failed')

if __name__=='__main__':main()
