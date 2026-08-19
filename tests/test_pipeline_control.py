"""Tests for starting, cancelling, and reporting on the pipeline subprocess.

These never launch the real scraper: a sleeping Python process stands in for it
so cancellation can be exercised without driving a browser.
"""

import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from swgoh.web import service
from swgoh.web.app import app


@pytest.fixture
def fake_run():
    """Put a long-running stand-in process into the pipeline state."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    service._pipeline.process = process
    service._pipeline.started_at = service.now_text()
    service._pipeline.command = ["fake"]
    service._pipeline.cancelled = False
    service._pipeline.log_handle = None

    yield process

    if process.poll() is None:
        process.kill()
        process.wait(timeout=10)

    service._pipeline.process = None
    service._pipeline.started_at = None
    service._pipeline.command = None
    service._pipeline.cancelled = False
    service._pipeline.log_handle = None


@pytest.fixture
def idle_pipeline():
    service._pipeline.process = None
    service._pipeline.cancelled = False
    yield
    service._pipeline.process = None
    service._pipeline.cancelled = False


def test_status_is_idle_without_a_run(idle_pipeline):
    assert service.pipeline_status()["state"] == "idle"


def test_stop_without_a_run_is_a_no_op(idle_pipeline):
    assert service.stop_pipeline() == "No pipeline is running."


def test_status_is_running_while_alive(fake_run):
    assert service.pipeline_status()["state"] == "running"


def test_stop_kills_the_process_and_reports_cancelled(fake_run):
    message = service.stop_pipeline()

    assert message == "Pipeline cancelled."
    assert fake_run.poll() is not None
    assert service.pipeline_status()["state"] == "cancelled"


def test_start_refuses_while_a_run_is_active(fake_run):
    message = service.start_pipeline(service.load_pipeline_settings())

    assert message == "Pipeline is already running."
    assert fake_run.poll() is None


def test_status_endpoint_reports_running(fake_run):
    with TestClient(app) as client:
        payload = client.get("/pipeline-status").json()

    assert payload["state"] == "running"
    assert "log_tail" in payload


def test_stop_endpoint_cancels_the_run(fake_run):
    with TestClient(app) as client:
        response = client.post("/stop-pipeline", follow_redirects=False)

    assert response.status_code == 303
    assert fake_run.poll() is not None
    assert service.pipeline_status()["state"] == "cancelled"


def test_setup_page_has_a_cancel_button(idle_pipeline):
    with TestClient(app) as client:
        body = client.get("/setup").text

    assert 'action="/stop-pipeline"' in body
    assert 'id="finish-modal"' in body
