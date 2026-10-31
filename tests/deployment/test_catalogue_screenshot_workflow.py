"""Security policy for privileged catalogue screenshot delivery."""

import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "catalogue-screenshots-commit.yml"
PLANNER_TEST_PATH = PROJECT_ROOT / ".github" / "scripts" / "catalogue-screenshot-plan.test.cjs"


def test_screenshot_planner_unit_contract() -> None:
    subprocess.run(["node", str(PLANNER_TEST_PATH)], check=True)


def test_write_token_job_loads_only_the_trusted_default_branch_revision() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "ref: ${{ github.sha }}" in workflow
    assert "path: .trusted-workflow" in workflow
    assert "sparse-checkout: .github/scripts/catalogue-screenshot-plan.cjs" in workflow
    assert "persist-credentials: false" in workflow
    assert "github.event.workflow_run.head_sha" not in workflow
    assert "ref: ${{ github.event.workflow_run" not in workflow
    assert "ref: ${{ github.event.pull_request" not in workflow
    assert "require(process.env.TRUSTED_SCREENSHOT_PLANNER)" in workflow
    assert "${{ github.workspace }}/.trusted-workflow/.github/scripts/catalogue-screenshot-plan.cjs" in workflow


def test_all_artifact_and_pull_security_checks_precede_blob_creation() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    metadata_validation = workflow.index("validateScreenshotMetadata(fs.readdirSync")
    content_read = workflow.index("contents: fs.readFileSync")
    content_validation = workflow.index("planScreenshotArtifact(artifactContents)")
    create_blob = workflow.index("github.rest.git.createBlob")

    assert metadata_validation < content_read < content_validation < create_blob

    for required_check in (
        "run.repository.full_name !==",
        "pull.head.repo?.full_name !==",
        "pull.head.sha !== runPull.head.sha",
    ):
        assert workflow.index(required_check) < create_blob

    assert "\n        run:" not in workflow
    assert "npm " not in workflow
