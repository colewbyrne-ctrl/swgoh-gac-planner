// Manual counter assignment for the attack page.
//
// The picker only ever lists counters the optimizer already considers legal for
// that defense, so a pick always survives the rebuild. Options already spent on
// another defense stay selectable — choosing one frees it there, which is the
// reassignment the server performs.

(function () {
  const modal = document.getElementById("assign-modal");
  const subtitle = document.getElementById("assign-subtitle");
  const search = document.getElementById("assign-search");
  const optionList = document.getElementById("assign-options");
  const form = document.getElementById("assign-form");
  const cancelButton = document.getElementById("assign-cancel");

  if (!modal || !form) {
    return;
  }

  let target = null;
  let options = [];
  let requestId = 0;

  function closeModal() {
    modal.hidden = true;
    target = null;
    options = [];
    search.value = "";
    optionList.innerHTML = "";
  }

  function submit(option) {
    form.elements.combat_type.value = target.combatType;
    form.elements.defense_leader.value = target.defenseLeader;
    form.elements.defense_name.value = target.defenseName;
    form.elements.counter_leader.value = option.counter_leader;
    form.elements.counter_units.value = option.counter_units_repr;
    form.submit();
  }

  function matches(option, query) {
    if (!query) {
      return true;
    }
    const haystack = (option.counter_leader + " " + option.counter_units.join(" ")).toLowerCase();
    return query.split(/\s+/).every((term) => haystack.includes(term));
  }

  function render() {
    const query = search.value.trim().toLowerCase();
    const visible = options.filter((option) => matches(option, query));
    optionList.innerHTML = "";

    if (!visible.length) {
      const empty = document.createElement("p");
      empty.className = "muted small";
      empty.textContent = options.length
        ? "No counter matches that filter."
        : "No legal counters for this defense. Loosen a rejection or check your roster.";
      optionList.appendChild(empty);
      return;
    }

    visible.forEach((option) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "option" + (option.is_current ? " option-current" : "");
      row.addEventListener("click", () => submit(option));

      const leader = document.createElement("div");
      leader.className = "leader";
      leader.textContent = option.counter_leader;
      if (option.is_current) {
        leader.textContent += " — currently assigned";
      } else if (option.assigned_to) {
        leader.textContent += " — on " + option.assigned_to + ", will reassign";
      }
      row.appendChild(leader);

      const detail = document.createElement("div");
      detail.className = "muted small";
      detail.textContent =
        option.counter_units.join(", ") +
        "  ·  win " + option.win_percent + "%  ·  seen " + option.seen;
      row.appendChild(detail);

      optionList.appendChild(row);
    });
  }

  function openModal(button) {
    target = {
      combatType: button.dataset.combatType,
      defenseLeader: button.dataset.defenseLeader,
      defenseName: button.dataset.defenseName,
    };
    subtitle.textContent = "Counters available against " + target.defenseLeader + ".";
    optionList.innerHTML = '<p class="muted small">Loading…</p>';
    modal.hidden = false;
    search.focus();

    const currentRequest = ++requestId;
    const query = new URLSearchParams({
      combat_type: target.combatType,
      defense_leader: target.defenseLeader,
    });

    fetch("/counter-options?" + query.toString())
      .then((response) => response.json())
      .then((data) => {
        if (currentRequest !== requestId) {
          return;
        }
        options = data.options || [];
        render();
      })
      .catch(() => {
        if (currentRequest === requestId) {
          optionList.innerHTML = '<p class="muted small">Could not load counters.</p>';
        }
      });
  }

  document.querySelectorAll(".assign-button").forEach((button) => {
    button.addEventListener("click", () => openModal(button));
  });

  search.addEventListener("input", render);
  cancelButton.addEventListener("click", closeModal);
  modal.addEventListener("click", (event) => {
    if (event.target === modal) {
      closeModal();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) {
      closeModal();
    }
  });
})();
