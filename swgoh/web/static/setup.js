// Live pipeline status for the setup page.
//
// While a run is in progress the status line, log tail, and buttons refresh in
// place every few seconds. The completion popup only fires on a transition
// observed by *this* page load, so opening /setup after an old run finished
// does not pop a stale dialog.

(function () {
  const POLL_MS = 3000;
  const FINISHED = ["complete", "failed", "cancelled"];

  const statusEl = document.getElementById("pipeline-status");
  const logEl = document.getElementById("log-tail");
  const logHeading = document.getElementById("log-heading");
  const startButton = document.getElementById("start-button");
  const cancelButton = document.getElementById("cancel-button");
  const modal = document.getElementById("finish-modal");
  const modalDetail = document.getElementById("finish-detail");
  const modalTitle = document.getElementById("finish-title");

  if (!statusEl || !modal) {
    return;
  }

  let lastState = statusEl.dataset.state;
  let timer = null;

  const TITLES = {
    complete: "Pipeline complete",
    failed: "Pipeline failed",
    cancelled: "Pipeline cancelled",
  };

  function closeModal() {
    modal.hidden = true;
  }

  function openModal(status) {
    modalTitle.textContent = TITLES[status.state] || "Pipeline finished";
    modalDetail.textContent = status.detail;
    modal.hidden = false;
  }

  function render(status) {
    statusEl.dataset.state = status.state;
    statusEl.className = "status status-" + status.state;
    statusEl.innerHTML = "";

    const strong = document.createElement("strong");
    strong.textContent = status.state;
    statusEl.append(strong, " — " + status.detail);

    if (logEl && typeof status.log_tail === "string") {
      logEl.textContent = status.log_tail;
      logEl.hidden = !status.log_tail;
      if (logHeading) {
        logHeading.hidden = !status.log_tail;
      }
    }

    const running = status.state === "running";
    if (startButton) startButton.disabled = running;
    if (cancelButton) cancelButton.disabled = !running;
  }

  async function poll() {
    let status;
    try {
      const response = await fetch("/pipeline-status", { cache: "no-store" });
      if (!response.ok) return;
      status = await response.json();
    } catch (err) {
      return; // server restarting or offline; try again on the next tick
    }

    render(status);

    const justFinished = lastState === "running" && FINISHED.includes(status.state);
    lastState = status.state;

    if (justFinished) {
      openModal(status);
      stopPolling();
    }
  }

  function stopPolling() {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  document.getElementById("finish-reload").addEventListener("click", function () {
    window.location.reload();
  });

  document.getElementById("finish-dismiss").addEventListener("click", closeModal);

  modal.addEventListener("click", function (event) {
    if (event.target === modal) closeModal();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !modal.hidden) closeModal();
  });

  if (lastState === "running") {
    timer = setInterval(poll, POLL_MS);
    poll();
  }
})();
