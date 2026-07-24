(() => {
  const toggle = document.querySelector(".nav-toggle");
  const nav = document.querySelector(".site-header nav");
  toggle?.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    nav?.classList.toggle("open", open);
  });

  document.querySelectorAll("table[data-sortable] th").forEach((th, index) => {
    th.tabIndex = 0;
    th.setAttribute("role", "button");
    const sort = () => {
      const table = th.closest("table");
      const body = table.querySelector("tbody");
      const ascending = th.dataset.order !== "asc";
      const rows = [...body.rows];
      rows.sort((a, b) => {
        const av = a.cells[index].textContent.trim().replaceAll(",", "");
        const bv = b.cells[index].textContent.trim().replaceAll(",", "");
        const an = Number(av), bn = Number(bv);
        const cmp = Number.isFinite(an) && Number.isFinite(bn)
          ? an - bn : av.localeCompare(bv, undefined, {numeric: true});
        return ascending ? cmp : -cmp;
      });
      rows.forEach(row => body.appendChild(row));
      table.querySelectorAll("th").forEach(cell => delete cell.dataset.order);
      th.dataset.order = ascending ? "asc" : "desc";
    };
    th.addEventListener("click", sort);
    th.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); sort(); }
    });
  });

  const timingButtons = document.querySelectorAll("[data-window]");
  const timingTable = document.querySelector(".window-controls + .table-wrap table");
  const chooseWindow = minutes => {
    timingButtons.forEach(button => button.setAttribute("aria-pressed", String(button.dataset.window === minutes)));
    if (!timingTable) return;
    [...timingTable.rows].forEach(row => {
      [...row.cells].forEach((cell, index) => {
        if (index < 3) return;
        const cellWindow = index < 5 ? "5" : index < 7 ? "30" : "60";
        cell.hidden = cellWindow !== minutes;
      });
    });
  };
  timingButtons.forEach(button => button.addEventListener("click", () => chooseWindow(button.dataset.window)));
  if (timingButtons.length) chooseWindow("5");
})();
