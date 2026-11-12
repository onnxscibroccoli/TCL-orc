"""Release integrity guards: tags must never bypass consensus/security checks.

These tests are static assertions against the in-repository Actions DAG.  They
cannot replace actual workflow executions, provenance verification or audits.
"""
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
CI = REPO / ".github" / "workflows" / "ci.yml"
CRITICAL = {
    "pytest",
    "lint",
    "zk",
    "offline",
    "no-ethereum",
    "contracts",
    "governance",
    "rootless-container",
    "integration",
    "compose",
    "image",
    "wheel",
    "deploy-dryrun",
    "slither",
    "forge-security",
    "dependency-audit",
}


def _jobs():
    data = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return data["jobs"]


def _needs(job):
    deps = job.get("needs", [])
    return {deps} if isinstance(deps, str) else set(deps)


def test_release_and_image_publish_are_tag_only():
    jobs = _jobs()
    for key in ("release", "publish-image"):
        assert "refs/tags/v" in jobs[key].get("if", ""), key
        assert CRITICAL <= _needs(jobs[key]), key
    assert "publish-image" in _needs(jobs["release"])
    assert CRITICAL <= set(jobs), "missing mandatory CI security jobs"


def test_pr_smoke_job_cannot_publish_or_sign_images():
    jobs = _jobs()
    smoke = jobs["image"]
    assert smoke.get("permissions") == {"contents": "read"}
    assert all(not step.get("with", {}).get("push", False) for step in smoke["steps"])
    all_smoke_steps = str(smoke["steps"])
    for action in ("docker/login-action", "cosign-installer", "attest-build-provenance"):
        assert action not in all_smoke_steps
    # The publishing job does have write permissions but cannot start on a PR.
    publishing = jobs["publish-image"]
    assert publishing["permissions"]["packages"] == "write"
    assert publishing["permissions"]["attestations"] == "write"
    assert any("docker/login-action" in step.get("uses", "") for step in publishing["steps"])
    assert any(step.get("with", {}).get("push") is True for step in publishing["steps"])


def test_release_keeps_keyless_signing_and_provenance():
    jobs = _jobs()
    publishing = jobs["publish-image"]
    steps = publishing["steps"]
    build = next(s for s in steps if "docker/build-push-action" in s.get("uses", ""))
    assert build["with"]["push"] is True
    assert build["with"]["sbom"] is True
    assert build["with"]["provenance"] == "mode=max"
    scripts = "\n".join(s.get("run", "") for s in steps)
    assert "cosign sign" in scripts and "cosign attest" in scripts
    assert any("attest-build-provenance" in s.get("uses", "") for s in steps)
    release = jobs["release"]
    assert release["permissions"]["contents"] == "write"
    assert release["permissions"]["id-token"] == "write"
    assert release["permissions"]["attestations"] == "write"
