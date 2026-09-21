// Compact page snapshot for the LLM.
// Tags every visible interactive element with a stable data-agent-id
// (ids survive across snapshots, so a stale id fails loudly instead of
// silently pointing to another element) and returns a text outline.
// Input: {prefix, maxElements, maxText}. No site-specific selectors here.
(({ prefix, maxElements, maxText }) => {
  const INTERACTIVE_TAGS = new Set(["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY", "LABEL", "OPTION"]);
  const INTERACTIVE_ROLES = new Set([
    "button", "link", "checkbox", "radio", "tab", "menuitem", "menuitemcheckbox",
    "menuitemradio", "option", "switch", "textbox", "combobox", "searchbox", "slider", "treeitem",
  ]);

  if (window.__agentNextId === undefined) window.__agentNextId = 1;

  const clean = (s, n) => (s || "").replace(/\s+/g, " ").trim().slice(0, n);

  const isVisible = (el) => {
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const st = getComputedStyle(el);
    if (st.visibility === "hidden" || st.display === "none" || Number(st.opacity) === 0) return false;
    return true;
  };

  const inViewport = (el) => {
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  };

  const isInteractive = (el) => {
    if (el.tagName === "IFRAME" || el.tagName === "BODY" || el.tagName === "HTML") return false;
    if (INTERACTIVE_TAGS.has(el.tagName)) {
      if (el.tagName === "A" && !el.hasAttribute("href") && !el.onclick) return false;
      if (el.tagName === "LABEL" && !el.control) return false;
      return !el.disabled;
    }
    const role = el.getAttribute("role");
    if (role && INTERACTIVE_ROLES.has(role)) return true;
    if (el.isContentEditable && el.getAttribute("contenteditable") !== null) return true;
    if (el.hasAttribute("onclick") || el.tabIndex >= 0) return true;
    // Custom div-buttons: pointer cursor on an element whose parent is not pointer.
    const st = getComputedStyle(el);
    if (st.cursor === "pointer") {
      const p = el.parentElement;
      return !p || getComputedStyle(p).cursor !== "pointer";
    }
    return false;
  };

  const accessibleName = (el) => {
    const byLabelledBy = (el.getAttribute("aria-labelledby") || "")
      .split(/\s+/).map((id) => document.getElementById(id)?.innerText).filter(Boolean).join(" ");
    return clean(
      el.getAttribute("aria-label") || byLabelledBy ||
      (el.labels && el.labels[0]?.innerText) ||
      el.getAttribute("title") || el.getAttribute("alt") ||
      el.innerText || el.value || el.getAttribute("placeholder") ||
      el.querySelector("img[alt]")?.getAttribute("alt") || "",
      80,
    );
  };

  // Short text of the nearest ancestor that "owns" the element (a product card,
  // an email row). Lets the model tell apart 20 identical "Add" buttons.
  // Returns {text, owner}; owner lets consecutive elements of one row/card share one context line.
  const context = (el, name) => {
    let p = el.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const raw = (p.innerText || "").replace(/\s+/g, " ").trim();
      if (raw.length > 300) return { text: "", owner: null };  // page-level container: no useful context
      const rest = clean(raw.replace(name, ""), 110);
      if (rest.length >= 12) return { text: rest, owner: p };  // first ancestor with text besides the element
    }
    return { text: "", owner: null };
  };
  const SEMANTIC = new Set(["A", "BUTTON", "INPUT", "SELECT", "TEXTAREA", "SUMMARY", "OPTION"]);

  // Shadow DOM aware walk.
  const all = [];
  const walk = (root) => {
    for (const el of root.querySelectorAll("*")) {
      all.push(el);
      if (el.shadowRoot) walk(el.shadowRoot);
    }
  };
  walk(document);

  const lines = [];
  let below = 0, above = 0, lastOwner = null;
  for (const el of all) {
    if (!isInteractive(el) || !isVisible(el)) continue;
    if (!inViewport(el)) {
      el.getBoundingClientRect().top >= innerHeight ? below++ : above++;
      continue;
    }
    if (lines.length >= maxElements) { below++; continue; }
    const role = el.getAttribute("role") || el.tagName.toLowerCase();
    const name = accessibleName(el);
    // A nameless div/td/span is noise (a styled cell of a clickable row); its row is still reachable.
    if (!name && !SEMANTIC.has(el.tagName) && !el.getAttribute("role")) continue;
    if (!el.dataset.agentId) el.dataset.agentId = String(window.__agentNextId++);
    const id = prefix + el.dataset.agentId;
    const attrs = [];
    if (el.type && el.tagName === "INPUT") attrs.push(`type=${el.type}`);
    if (["INPUT", "TEXTAREA"].includes(el.tagName) && el.value) attrs.push(`value="${clean(el.value, 40)}"`);
    if (el.getAttribute("placeholder")) attrs.push(`placeholder="${clean(el.getAttribute("placeholder"), 40)}"`);
    if (el.checked) attrs.push("checked");
    if (el.getAttribute("aria-expanded")) attrs.push(`expanded=${el.getAttribute("aria-expanded")}`);
    if (el.tagName === "SELECT") attrs.push(`selected="${clean(el.selectedOptions[0]?.text, 30)}"`);
    if (el.tagName === "SELECT") attrs.push(`options=[${[...el.options].slice(0, 8).map((o) => clean(o.text, 20)).join("|")}]`);
    if (el.tagName === "A") {
      const href = el.getAttribute("href") || "";
      if (href && !href.startsWith("javascript")) attrs.push(`href=${clean(href, 60)}`);
    }
    const ctx = context(el, name);
    const same = ctx.owner && ctx.owner === lastOwner;  // same row/card as the previous line
    lastOwner = ctx.owner;
    const tail = !ctx.text ? "" : same ? "  〃" : `  ⟨${ctx.text}⟩`;
    lines.push(`[${id}] ${role} "${name}"${attrs.length ? " " + attrs.join(" ") : ""}${tail}`);
  }

  // Visible text of the viewport (headings first), trimmed.
  const headings = [...document.querySelectorAll("h1,h2,h3,[role=heading]")]
    .filter((h) => isVisible(h) && inViewport(h)).map((h) => clean(h.innerText, 80)).filter(Boolean).slice(0, 12);

  const dialogs = [...document.querySelectorAll("dialog[open],[role=dialog],[role=alertdialog],[aria-modal=true]")]
    .filter(isVisible).map((d) => clean(d.innerText, 200));

  return {
    elements: lines,
    headings,
    dialogs,
    text: clean(document.body?.innerText, maxText),
    scroll: { y: Math.round(scrollY), max: Math.max(0, document.documentElement.scrollHeight - innerHeight) },
    offscreen: { above, below },
  };
})
