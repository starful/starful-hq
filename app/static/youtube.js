/** YouTube attach — design tab: 없음|있음 · 썸네일 · 후보 모달(다시찾기 없음). */
(function () {
  const wrapEl = document.getElementById("yt-wrap");
  if (!wrapEl) return;

  const shopsEl = document.getElementById("yt-shops");
  const countEl = document.getElementById("yt-count");
  const pickedEl = document.getElementById("yt-picked");
  const tabs = Array.from(wrapEl.querySelectorAll(".yt-tab[data-filter]"));
  const fetchBtn = document.getElementById("yt-fetch-open");
  const clearBtn = document.getElementById("yt-clear");
  const shipBtn = document.getElementById("yt-ship");
  const pop = document.getElementById("yt-pop");
  const popName = document.getElementById("yt-pop-name");
  const popGrid = document.getElementById("yt-pop-grid");
  const popFoot = document.getElementById("yt-pop-foot");
  const popX = document.getElementById("yt-pop-x");

  const NOUN = wrapEl.getAttribute("data-yt-noun") || "장소";
  const API_BASE = (wrapEl.getAttribute("data-yt-api") || "").replace(/\/?$/, "/");

  let selected = null;
  let shops = [];
  let filterMode = "missing";

  if (pop) document.body.appendChild(pop);

  function apiPath(path) {
    return API_BASE + String(path || "").replace(/^\//, "");
  }

  async function api(path, opts) {
    const res = await fetch(apiPath(path), {
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      ...opts,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || data.message || res.statusText || "request failed");
    return data;
  }

  function esc(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatViews(n) {
    const v = Number(n) || 0;
    if (v >= 100000000) return (v / 100000000).toFixed(1).replace(/\.0$/, "") + "億";
    if (v >= 10000) return (v / 10000).toFixed(1).replace(/\.0$/, "") + "万";
    return v.toLocaleString();
  }

  function say(msg, ok) {
    if (window.hqSay) window.hqSay(msg, !!ok);
  }

  let shipState = { pending_places: 0, can_enqueue: false, label: "반영 맡기기", blocked: "" };

  function syncSticky() {
    const has = !!(selected && selected.has_youtube);
    if (fetchBtn) {
      fetchBtn.disabled = !selected;
      fetchBtn.textContent = has ? "바꾸기" : "후보 검색";
    }
    if (clearBtn) {
      clearBtn.hidden = !has;
      clearBtn.disabled = !has;
    }
    const pending = !!(shipState.pending_places || shipState.pending_files);
    if (shipBtn) {
      shipBtn.hidden = !pending;
      shipBtn.disabled = !shipState.can_enqueue;
      shipBtn.textContent = shipState.label || "반영 맡기기";
      shipBtn.title = shipState.blocked || "붙인 영상을 모아 한 번 PR·배포";
    }
    const bar = document.getElementById("yt-actions");
    if (bar) {
      bar.classList.toggle("has-clear", has);
      bar.classList.toggle("has-ship", pending);
    }
  }

  async function refreshShip() {
    try {
      shipState = await api("ship");
    } catch (_) {
      shipState = { pending_places: 0, can_enqueue: false, label: "반영 맡기기", blocked: "" };
    }
    syncSticky();
  }

  function setPickedLabel() {
    if (!pickedEl) return;
    if (!selected) {
      pickedEl.textContent = "";
      pickedEl.hidden = true;
      return;
    }
    pickedEl.textContent = selected.shop_name || selected.base_id;
    pickedEl.hidden = false;
  }

  function setFilter(mode, { reload = true } = {}) {
    const next = mode === "attached" ? "attached" : "missing";
    filterMode = next;
    tabs.forEach((tab) => {
      const on = tab.getAttribute("data-filter") === filterMode;
      tab.classList.toggle("on", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
    });
    if (reload) loadShops();
  }

  function thumbHtml(s) {
    if (s.has_youtube && s.youtube_thumb) {
      const href = esc(s.youtube_url || `https://www.youtube.com/watch?v=${s.youtube_id}`);
      return `<a class="yt-thumb has" href="${href}" target="_blank" rel="noopener noreferrer" title="YouTube 열기" data-yt-link>
        <img src="${esc(s.youtube_thumb)}" alt="" loading="lazy">
      </a>`;
    }
    return `<span class="yt-thumb none" aria-label="영상 없음"><i>영상 없음</i></span>`;
  }

  function linksHtml(s) {
    const bits = [];
    if (s.page_url) {
      bits.push(
        `<a class="yt-link" href="${esc(s.page_url)}" target="_blank" rel="noopener noreferrer" data-yt-link>글 보기</a>`
      );
    }
    if (s.has_youtube && (s.youtube_url || s.youtube_id)) {
      const href = esc(s.youtube_url || `https://www.youtube.com/watch?v=${s.youtube_id}`);
      bits.push(`<a class="yt-link" href="${href}" target="_blank" rel="noopener noreferrer" data-yt-link>영상</a>`);
    }
    return bits.length ? `<span class="yt-links">${bits.join("")}</span>` : "";
  }

  function renderList() {
    if (countEl) {
      countEl.textContent =
        filterMode === "attached" ? `있음 ${shops.length}개` : `없음 ${shops.length}개`;
    }
    if (!shops.length) {
      shopsEl.innerHTML = `<li class="empty">${
        filterMode === "attached" ? "붙인 영상이 없어요." : `youtube 없는 ${esc(NOUN)}가 없어요.`
      }</li>`;
      return;
    }
    shopsEl.innerHTML = shops
      .map((s) => {
        const on = selected && selected.base_id === s.base_id ? " on" : "";
        return `<li>
          <button type="button" class="yt-row${on}" data-id="${esc(s.base_id)}">
            ${thumbHtml(s)}
            <span class="yt-row-main">
              <b>${esc(s.shop_name)}</b>
              ${linksHtml(s)}
            </span>
          </button>
        </li>`;
      })
      .join("");

    shopsEl.querySelectorAll(".yt-row").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        if (ev.target.closest("[data-yt-link]")) return;
        const id = btn.getAttribute("data-id");
        selected = shops.find((x) => x.base_id === id) || null;
        shopsEl.querySelectorAll(".yt-row").forEach((b) => {
          b.classList.toggle("on", b.getAttribute("data-id") === id);
        });
        setPickedLabel();
        syncSticky();
      });
    });
  }

  async function loadShops() {
    shopsEl.innerHTML = '<li class="empty">로딩…</li>';
    try {
      const data = await api(`shops?filter=${encodeURIComponent(filterMode)}&limit=100`);
      shops = data.shops || [];
      if (selected) {
        const fresh = shops.find((x) => x.base_id === selected.base_id);
        selected = fresh || null;
      }
      setPickedLabel();
      syncSticky();
      renderList();
      refreshShip();
    } catch (e) {
      shopsEl.innerHTML = `<li class="empty">${esc(e.message)}</li>`;
      say(e.message, false);
    }
  }

  function closePop() {
    if (pop) pop.hidden = true;
  }

  function paintCands(cands) {
    if (!popGrid) return;
    popGrid.innerHTML = "";
    if (!cands || !cands.length) {
      popGrid.innerHTML = '<p class="hint">후보가 없어요.</p>';
      return;
    }
    cands.forEach((c) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "cand yt-cand";
      b.innerHTML =
        '<img alt="" loading="lazy"><b></b><small></small><span class="yt-cand-views"></span><span class="yt-cand-go">붙이기</span>';
      b.querySelector("img").src = c.thumbnail || "";
      b.querySelector("b").textContent = c.title || c.video_id;
      b.querySelector("small").textContent = c.channel || "";
      b.querySelector(".yt-cand-views").textContent =
        "▶ " + (c.views_label || formatViews(c.view_count)) + " 회";
      b.addEventListener("click", async () => {
        if (!selected) return;
        popGrid.querySelectorAll(".cand").forEach((x) => {
          x.disabled = true;
        });
        b.classList.add("on");
        try {
          await api(`shops/${encodeURIComponent(selected.base_id)}/attach`, {
            method: "POST",
            body: JSON.stringify({ video_id: c.video_id }),
          });
          say("붙였어요", true);
          closePop();
          // Stay on current tab (usually 없음) so the next place is ready.
          selected = null;
          await loadShops();
          setPickedLabel();
          await refreshShip();
        } catch (e) {
          say(e.message, false);
          popGrid.querySelectorAll(".cand").forEach((x) => {
            x.disabled = false;
          });
        }
      });
      popGrid.appendChild(b);
    });
  }

  async function openFetch() {
    if (!selected || !pop) return;
    pop.hidden = false;
    if (popName) popName.textContent = selected.shop_name || selected.base_id;
    if (popFoot) popFoot.textContent = "고르면 바로 붙여요 · 3~5개";
    popGrid.innerHTML = '<p class="hint">YouTube에서 찾는 중…</p>';
    try {
      const data = await api(`shops/${encodeURIComponent(selected.base_id)}/fetch`, {
        method: "POST",
        body: JSON.stringify({ max_results: 5 }),
      });
      const n = (data.candidates || []).length;
      if (popFoot) {
        popFoot.textContent = n ? `후보 ${n}개 · 고르면 바로 붙여요` : "후보가 없어요";
      }
      paintCands(data.candidates || []);
      if (!n) say("후보가 없어요", false);
    } catch (e) {
      popGrid.innerHTML = "";
      const p = document.createElement("p");
      p.className = "hint warn";
      p.textContent = e.message || "후보를 찾지 못했어요";
      popGrid.appendChild(p);
      say(e.message, false);
    }
  }

  fetchBtn?.addEventListener("click", () => openFetch());
  shipBtn?.addEventListener("click", async () => {
    if (!shipState.can_enqueue) return;
    shipBtn.disabled = true;
    try {
      const data = await api("ship", { method: "POST", body: "{}" });
      say(data.message || "큐에 담았어요", !!data.ok);
      shipState = data;
      syncSticky();
      document.dispatchEvent(new Event("office:poke"));
    } catch (e) {
      say(e.message, false);
      await refreshShip();
    }
  });
  clearBtn?.addEventListener("click", async () => {
    if (!selected) return;
    if (!confirm("이 글에서 YouTube를 제거할까요?")) return;
    try {
      await api(`shops/${encodeURIComponent(selected.base_id)}/clear`, {
        method: "POST",
        body: JSON.stringify({}),
      });
      selected.has_youtube = false;
      selected.youtube_id = "";
      selected.youtube_url = "";
      selected.youtube_thumb = "";
      say("제거됨", true);
      setFilter("missing", { reload: false });
      await loadShops();
      await refreshShip();
    } catch (e) {
      say(e.message, false);
    }
  });

  popX?.addEventListener("click", closePop);
  pop?.addEventListener("click", (ev) => {
    if (ev.target === pop) closePop();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closePop();
  });

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => setFilter(tab.getAttribute("data-filter")));
  });

  const flow = document.getElementById("design-flow");
  const modeBtns = Array.from(document.querySelectorAll("[data-design-mode]"));
  function setDesignMode(mode) {
    const next = mode === "youtube" ? "youtube" : "images";
    modeBtns.forEach((btn) => {
      const on = btn.getAttribute("data-design-mode") === next;
      btn.classList.toggle("on", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
    });
    document.querySelectorAll("[data-design-panel]").forEach((el) => {
      el.hidden = el.getAttribute("data-design-panel") !== next;
    });
    document.querySelectorAll("[data-design-sticky]").forEach((el) => {
      el.hidden = el.getAttribute("data-design-sticky") !== next;
    });
    if (next === "youtube") {
      if (location.hash !== "#youtube") history.replaceState(null, "", "#youtube");
      if (!shops.length) loadShops();
    } else if (location.hash === "#youtube") {
      history.replaceState(null, "", location.pathname + location.search);
    }
  }
  modeBtns.forEach((btn) => {
    btn.addEventListener("click", () => setDesignMode(btn.getAttribute("data-design-mode")));
  });
  if (flow && flow.getAttribute("data-has-yt") === "1") {
    setDesignMode(location.hash === "#youtube" ? "youtube" : "images");
  }

  if (!API_BASE || API_BASE === "/") {
    say("YouTube API 경로가 없습니다", false);
    return;
  }
  loadShops();
})();
