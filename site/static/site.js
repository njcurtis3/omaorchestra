// omaorchestra.site: theme, copy buttons, tabs, search, table of contents,
// and the mobile docs menu. No dependencies; every feature degrades to a
// plain working page without it.
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const base = document.body.dataset.base || "./";

  // ------------------------------------------------------------ theme

  function currentTheme() {
    const set = document.documentElement.dataset.theme;
    if (set) return set;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }

  $$("[data-theme-toggle]").forEach((button) => {
    button.addEventListener("click", () => {
      const next = currentTheme() === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (e) {}
    });
  });

  // ------------------------------------------------------------ copy

  const COPY_ICON = '<svg aria-hidden="true" viewBox="0 0 16 16"><rect x="5.5" y="5.5" width="8" height="8" rx="1.5"/><path d="M10.5 3.5v-1a1 1 0 0 0-1-1h-6a1 1 0 0 0-1 1v6a1 1 0 0 0 1 1h1"/></svg>';
  const DONE_ICON = '<svg aria-hidden="true" viewBox="0 0 16 16"><path d="m3.5 8.5 3 3 6-7"/></svg>';

  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (e) {
      // Older browsers, or a page not served over https.
      const area = Object.assign(document.createElement("textarea"), { value: text });
      area.style.cssText = "position:fixed;opacity:0";
      document.body.append(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      return ok;
    }
  }

  function flash(el, isButton) {
    el.classList.add("copied");
    const before = isButton ? el.innerHTML : null;
    if (isButton) el.innerHTML = DONE_ICON;
    setTimeout(() => {
      el.classList.remove("copied");
      if (isButton) el.innerHTML = before;
    }, 1400);
  }

  // Every code block gets a copy button. Shell prompts and comments stay out
  // of what is copied.
  $$(".prose .hl").forEach((block) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "copy-button";
    button.setAttribute("aria-label", "Copy code");
    button.innerHTML = COPY_ICON;
    block.append(button);
  });

  document.addEventListener("click", async (event) => {
    const target = event.target.closest("[data-copy], .hl .copy-button");
    if (!target) return;
    let text = target.dataset.copy;
    if (text === undefined) {
      text = target.closest(".hl").querySelector("pre").innerText.replace(/\n$/, "");
    }
    if (await copy(text)) flash(target, target.tagName === "BUTTON");
  });

  // ------------------------------------------------------------ tabs

  $$("[data-tabs]").forEach((root) => {
    const tabs = $$('[role="tab"]', root);
    const select = (tab, focus) => {
      tabs.forEach((t) => {
        const on = t === tab;
        t.setAttribute("aria-selected", String(on));
        t.tabIndex = on ? 0 : -1;
        document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
      });
      if (focus) tab.focus();
    };
    tabs.forEach((tab, i) => {
      tab.addEventListener("click", () => select(tab));
      tab.addEventListener("keydown", (e) => {
        const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
        if (step) {
          e.preventDefault();
          select(tabs[(i + step + tabs.length) % tabs.length], true);
        } else if (e.key === "Home" || e.key === "End") {
          e.preventDefault();
          select(tabs[e.key === "Home" ? 0 : tabs.length - 1], true);
        }
      });
    });
  });

  // ------------------------------------------------------------ table of contents

  const tocLinks = $$(".toc a");
  if (tocLinks.length && "IntersectionObserver" in window) {
    const byId = new Map(tocLinks.map((a) => [decodeURIComponent(a.hash.slice(1)), a]));
    const headings = [...byId.keys()].map((id) => document.getElementById(id)).filter(Boolean);
    const visible = new Set();
    const mark = () => {
      // The topmost heading on screen, or failing that the last one passed.
      let current = headings.find((h) => visible.has(h));
      if (!current) {
        const passed = headings.filter((h) => h.getBoundingClientRect().top < 120);
        current = passed[passed.length - 1] || headings[0];
      }
      // Force a boolean: toggle(name, undefined) flips instead of clearing.
      tocLinks.forEach((a) => a.classList.toggle("active", Boolean(current && byId.get(current.id) === a)));
    };
    const observer = new IntersectionObserver((entries) => {
      entries.forEach((e) => (e.isIntersecting ? visible.add(e.target) : visible.delete(e.target)));
      mark();
    }, { rootMargin: "-64px 0px -60% 0px" });
    headings.forEach((h) => observer.observe(h));
  }

  // ------------------------------------------------------------ mobile menu

  const menuButton = $("[data-menu-toggle]");
  if (menuButton) {
    if (!$("[data-sidebar]")) {
      // Pages without a docs sidebar: the menu button opens the docs instead.
      menuButton.addEventListener("click", () => { location.href = base + "docs/guide/"; });
    } else {
      const setOpen = (open) => {
        document.body.classList.toggle("menu-open", open);
        menuButton.setAttribute("aria-expanded", String(open));
      };
      menuButton.addEventListener("click", () => setOpen(!document.body.classList.contains("menu-open")));
      document.addEventListener("click", (e) => {
        if (document.body.classList.contains("menu-open") &&
            !e.target.closest("[data-sidebar], [data-menu-toggle]")) setOpen(false);
      });
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") setOpen(false); });
    }
  }

  // ------------------------------------------------------------ search

  const search = $("[data-search]");
  const input = $("[data-search-input]");
  const results = $("[data-search-results]");
  let index = null;
  let selected = 0;
  let lastFocus = null;

  async function loadIndex() {
    if (index) return index;
    try {
      const response = await fetch(base + "search.json");
      index = (await response.json()).map((e) => ({
        ...e,
        _title: e.title.toLowerCase(),
        _page: e.page.toLowerCase(),
        _text: e.text.toLowerCase(),
      }));
    } catch (e) {
      index = [];
    }
    return index;
  }

  const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function snippet(text, terms) {
    const lower = text.toLowerCase();
    let at = -1;
    for (const t of terms) { at = lower.indexOf(t); if (at >= 0) break; }
    const start = Math.max(0, at - 60);
    let out = (start > 0 ? "…" : "") + text.slice(start, start + 200);
    out = escapeHtml(out);
    for (const t of terms) {
      if (t.length < 2) continue;
      out = out.replace(new RegExp(t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi"), (m) => `<mark>${m}</mark>`);
    }
    return out;
  }

  function score(entry, terms, phrase) {
    let s = 0;
    for (const t of terms) {
      const inTitle = entry._title.includes(t);
      const inPage = entry._page.includes(t);
      const inText = entry._text.includes(t);
      if (!inTitle && !inPage && !inText) return 0;
      if (inTitle) s += entry._title.startsWith(t) ? 12 : 8;
      if (inPage) s += 3;
      if (inText) s += 1;
    }
    if (entry._title.includes(phrase)) s += 10;
    if (entry._title === phrase) s += 20;
    if (!entry.url.includes("#")) s += 2;
    return s;
  }

  function render(query) {
    const phrase = query.trim().toLowerCase();
    if (!phrase || !index) { results.innerHTML = ""; return; }
    const terms = phrase.split(/\s+/).filter(Boolean);
    const hits = index
      .map((e) => [score(e, terms, phrase), e])
      .filter(([s]) => s > 0)
      .sort((a, b) => b[0] - a[0])
      .slice(0, 12);
    selected = 0;
    if (!hits.length) {
      results.innerHTML = `<li class="search-empty">No results for “${escapeHtml(query.trim())}”</li>`;
      return;
    }
    results.innerHTML = hits.map(([, e], i) => `
      <li><a href="${base}${e.url}" role="option" aria-selected="${i === 0}">
        <span class="sr-title">${escapeHtml(e.title)}</span>
        <span class="sr-page">${escapeHtml(e.page)}</span>
        ${e.text ? `<span class="sr-text">${snippet(e.text, terms)}</span>` : ""}
      </a></li>`).join("");
  }

  function move(step) {
    const links = $$("a", results);
    if (!links.length) return;
    selected = (selected + step + links.length) % links.length;
    links.forEach((a, i) => a.setAttribute("aria-selected", String(i === selected)));
    links[selected].scrollIntoView({ block: "nearest" });
  }

  async function openSearch() {
    lastFocus = document.activeElement;
    search.hidden = false;
    document.body.style.overflow = "hidden";
    input.value = "";
    results.innerHTML = "";
    input.focus();
    await loadIndex();
    if (input.value) render(input.value);
  }

  function closeSearch() {
    search.hidden = true;
    document.body.style.overflow = "";
    if (lastFocus) lastFocus.focus();
  }

  if (search) {
    $$("[data-search-open]").forEach((b) => b.addEventListener("click", openSearch));
    $$("[data-search-close]").forEach((b) => b.addEventListener("click", closeSearch));
    input.addEventListener("input", () => render(input.value));
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "Enter") {
        const link = $$("a", results)[selected];
        if (link) { e.preventDefault(); location.href = link.href; closeSearch(); }
      }
    });
    results.addEventListener("click", (e) => { if (e.target.closest("a")) closeSearch(); });
    document.addEventListener("keydown", (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName) || document.activeElement.isContentEditable;
      if (e.key === "Escape" && !search.hidden) { e.preventDefault(); closeSearch(); }
      else if ((e.key === "k" && (e.metaKey || e.ctrlKey)) || (e.key === "/" && !typing && search.hidden)) {
        e.preventDefault();
        search.hidden ? openSearch() : closeSearch();
      }
    });
  }
})();
