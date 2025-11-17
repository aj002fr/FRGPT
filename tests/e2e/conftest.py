"""Pytest fixtures for E2E tests."""

import pytest
import shutil
from pathlib import Path


@pytest.fixture
def clean_workspace():
    """Clean workspace before tests."""
    workspace_root = Path(__file__).parent.parent.parent / "workspace"
    
    # Clean before test
    if workspace_root.exists():
        shutil.rmtree(workspace_root)
    
    yield workspace_root
    
    # Optionally clean after test (commented out to inspect results)
    # if workspace_root.exists():
    #     shutil.rmtree(workspace_root)


@pytest.fixture
def project_root():
    """Get project root path."""
    return Path(__file__).parent.parent.parent


