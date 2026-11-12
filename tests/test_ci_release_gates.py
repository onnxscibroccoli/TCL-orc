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
    "release-notes",
    "release-origin",
}


class _StrictYamlLoader(yaml.SafeLoader):
    """Fail on duplicate YAML keys, which ordinary safe_load silently drops."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"duplicate workflow key: {key}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def test_all_workflow_files_parse_without_duplicate_keys():
    workflows = sorted((REPO / ".github" / "workflows").glob("*.yml"))
    assert workflows
    for workflow in workflows:
        doc = yaml.load(workflow.read_text(encoding="utf-8"), Loader=_StrictYamlLoader)
        assert isinstance(doc, dict) and isinstance(doc.get("jobs"), dict)
        assert doc["jobs"], workflow


def test_superseded_pr_runs_cancel_but_release_tags_never_cancel():
    workflows = sorted((REPO / ".github" / "workflows").glob("*.yml"))
    for workflow in workflows:
        config = yaml.load(
            workflow.read_text(encoding="utf-8"), Loader=_StrictYamlLoader
        )
        concurrency = config["concurrency"]
        assert "github.workflow" in concurrency["group"]
        assert "github.event.pull_request.number" in concurrency["group"]
        assert concurrency["cancel-in-progress"] == (
            "${{ github.event_name == 'pull_request' }}"
        )


def test_docs_workflow_changes_fail_closed_and_never_claim_to_deploy():
    doc = yaml.load(
        (REPO / ".github" / "workflows" / "quality-gates.yml").read_text(encoding="utf-8"),
        Loader=_StrictYamlLoader,
    )
    jobs = doc["jobs"]
    assert set(jobs) == {
        "markdown-structure-lint", "prose-style-lint", "math-katex-verification",
        "compile-pdf-artifact", "deploy-staging-preview",
    }
    collect = next(step for step in jobs["markdown-structure-lint"]["steps"]
                   if step.get("id") == "changed")
    shell = collect["run"]
    assert "git diff --name-only" in shell
    assert "changed-paths.txt" in shell
    assert "grep -E" in shell
    assert "|| true" not in shell
    prose = jobs["prose-style-lint"]
    assert prose["permissions"] == {"contents": "read"}
    assert next(step["with"]["reporter"] for step in prose["steps"]
                if "vale-action" in step.get("uses", "")) == "github-pr-annotations"
    preview = jobs["deploy-staging-preview"]
    assert "no deployment" in preview["name"]
    assert all("actions/deploy-pages" not in step.get("uses", "")
               for step in preview["steps"])


def _jobs():
    data = yaml.load(CI.read_text(encoding="utf-8"), Loader=_StrictYamlLoader)
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
    origin_gate = jobs["release-origin"]
    assert "refs/tags/v" in origin_gate["if"]
    origin_shell = "\\n".join(s.get("run", "") for s in origin_gate["steps"])
    assert "git merge-base --is-ancestor" in origin_shell
    assert "refs/remotes/origin/main" in origin_shell
    note_gate = jobs["release-notes"]
    assert "refs/tags/v" in note_gate["if"]
    assert {"zk", "contracts"} <= _needs(note_gate)
    note_steps = "\\n".join(s.get("run", "") for s in note_gate["steps"])
    for evidence in ("circomspect", "TCL_REQUIRE_ZK", "test_notes_ledger.py",
                     "test_note_zk.py", "NoteController.t.sol"):
        assert evidence in (note_steps + str(note_gate["steps"]))


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
