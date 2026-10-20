(() => {
  const area = document.getElementById("items");
  let session = null;
  let state = null;
  let following = true;
  let userInputUntil = 0;
  let frame = 0;
  const atBottom = () => area.scrollHeight - area.clientHeight - area.scrollTop <= 4;
  const markInput = () => { userInputUntil = Date.now() + 1500; };
  area.addEventListener("wheel", event => {
    markInput();
    if (event.deltaY < 0) following = false;
  }, {passive: true});
  area.addEventListener("pointerdown", markInput, {passive: true});
  area.addEventListener("pointermove", event => { if (event.buttons) markInput(); }, {passive: true});
  area.addEventListener("touchmove", markInput, {passive: true});
  area.addEventListener("keydown", event => {
    markInput();
    if (["ArrowUp", "PageUp", "Home"].includes(event.key)) following = false;
  });
  area.addEventListener("scroll", () => {
    if (Date.now() <= userInputUntil) following = atBottom();
  }, {passive: true});

  function follow() {
    if (frame) cancelAnimationFrame(frame);
    frame = requestAnimationFrame(() => {
      frame = 0;
      if (following) area.scrollTop = area.scrollHeight;
    });
  }
  // Resize may change the bottom without producing any new Mole output.
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(follow).observe(area);
  function text(node, value) {
    if (node.textContent !== value) node.textContent = value;
  }
  function element(tag, className, parent) {
    const node = document.createElement(tag);
    node.className = className;
    parent.appendChild(node);
    return node;
  }
  function icon(node, path) {
    // Only constant, authored icon markup reaches innerHTML.
    node.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">' + path + "</svg>";
  }
  // Cards retain their DOM nodes across samples, including each scroll position.
  function updateStatus(cards) {
    let grid = document.getElementById("status-grid");
    if (!grid) {
      area.replaceChildren();
      grid = element("div", "status-grid", area);
      grid.id = "status-grid";
    }
    const keys = new Set(cards.map(card => "status-card-" + card.key));
    for (const node of [...grid.children]) if (!keys.has(node.id)) node.remove();
    for (const card of cards) {
      let node = document.getElementById("status-card-" + card.key);
      if (!node) {
        node = element("article", "status-card", grid);
        node.id = "status-card-" + card.key;
        node.dataset.key = card.key;
        element("h3", "", node);
        element("div", "status-number", node);
        element("p", "status-note", node);
        const bar = element("div", "status-bar", node);
        element("div", "status-fill", bar);
        element("div", "status-rows", node);
      }
      text(node.querySelector("h3"), card.title);
      text(node.querySelector(".status-number"), card.value);
      text(node.querySelector(".status-note"), card.note);
      const bar = node.querySelector(".status-bar");
      bar.hidden = card.usage === null;
      node.querySelector(".status-fill").style.width = (card.usage || 0) + "%";
      const rows = node.querySelector(".status-rows");
      const values = card.rows.filter(Boolean);
      while (rows.children.length > values.length) rows.children[rows.children.length - 1].remove();
      values.forEach((value, index) => text(rows.children[index] || element("div", "status-row", rows), value));
    }
  }
  window.updateCleanup = view => {
    if (session !== view.session) {
      following = true;
      userInputUntil = 0;
      // Retain the scroll container and spinner even when a new task starts.
      if (session !== null) area.replaceChildren();
      session = view.session;
    }
    if (state !== view.state) {
      state = view.state;
      document.body.className = state;
      const paths = {
        running: '<path d="M12 3a9 9 0 1 1-9 9"/>',
        completed: '<path d="m5 12 4 4 10-10"/>',
        failed: '<path d="m6 6 12 12M18 6 6 18"/>',
        ready: '<path d="m9 5 10 7-10 7z"/>'
      };
      icon(document.getElementById("status-icon"), paths[state] || '<path d="M6 12h12"/>');
    }
    for (const key of ["status", "message", "amount", "caption", "elapsed"]) {
      text(document.getElementById(key), view[key]);
    }
    const mode = document.getElementById("mode");
    text(mode, view.tag);
    mode.hidden = !view.tag;
    if (view.command === "status" && view.cards && view.cards.length) {
      updateStatus(view.cards);
      let notice = document.getElementById("status-notice");
      if (!notice) {
        notice = element("p", "status-notice", area);
        notice.id = "status-notice";
      }
      notice.hidden = view.state === "running";
      text(notice, view.message || view.status);
      return; // Monitoring is a dashboard, not a log that follows the bottom.
    }
    const ids = new Set(view.sections.map(section => "category-" + section.id));
    for (const child of [...area.children]) {
      if (child.tagName === "SECTION" && !ids.has(child.id)) child.remove();
      else if (child.tagName !== "SECTION" && !["empty-output", "truncated-output"].includes(child.id)) child.remove();
    }
    for (const section of view.sections) {
      let node = document.getElementById("category-" + section.id);
      if (!node) {
        node = element("section", "", area);
        node.id = "category-" + section.id;
        const empty = document.getElementById("empty-output");
        if (empty) area.insertBefore(node, empty);
        const heading = element("div", "section-heading", node);
        const category = element("div", "category-icon", heading);
        icon(category, section.developer
          ? '<path d="m4 6 6 6-6 6m10 0h6"/>'
          : '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M4 10h16M10 10v10"/>');
        element("h3", "", heading);
        element("div", "items", node);
      }
      node.className = section.current ? "current" : "";
      text(node.querySelector("h3"), section.title);
      const items = node.querySelector(".items");
      const itemIDs = new Set(section.items.map(item => item.id));
      if (!section.items.length && section.current) itemIDs.add("pending");
      for (const child of [...items.children]) {
        if (!itemIDs.has(child.dataset.id)) child.remove();
      }
      for (const item of section.items) {
        let row = [...items.children].find(child => child.dataset.id === item.id);
        if (!row) {
          row = element("div", "item", items);
          row.dataset.id = item.id;
          element("p", "", row);
          element("div", "value", row);
        }
        text(row.querySelector("p"), item.text);
        const value = row.querySelector(".value");
        value.className = item.error ? "value error" : "value";
        text(value, item.value);
      }
      if (!section.items.length && section.current) {
        let pending = [...items.children].find(child => child.dataset.id === "pending");
        if (!pending) {
          pending = element("div", "item", items);
          pending.dataset.id = "pending";
          element("p", "", pending);
          element("span", "dot", pending);
        }
        text(pending.querySelector("p"), view.status + "…");
      }
    }
    for (const [id, value] of [["empty-output", view.sections.length ? "" : view.fallback], ["truncated-output", view.truncated]]) {
      let node = document.getElementById(id);
      if (!node) { node = element("p", "empty", area); node.id = id; }
      node.hidden = !value;
      text(node, value);
    }
    area.dataset.revision = String(view.revision);
    follow();
  };
})();
