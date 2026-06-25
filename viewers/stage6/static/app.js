const state = {
  search: "",
  only_with_markdown: false,
  only_stage4_relevant: false,
  stage4_status: "all",
  stage5_status: "all",
  stage6_status: "all",
  document_kinds: [],
  selected_index: 0,
};

const dom = {
  metaSummary: document.getElementById("metaSummary"),
  filteredCount: document.getElementById("filteredCount"),
  documentKinds: document.getElementById("documentKinds"),
  documentList: document.getElementById("documentList"),
  emptyState: document.getElementById("emptyState"),
  contentSection: document.getElementById("contentSection"),

  searchInput: document.getElementById("searchInput"),
  onlyWithMarkdown: document.getElementById("onlyWithMarkdown"),
  onlyStage4Relevant: document.getElementById("onlyStage4Relevant"),
  stage4Status: document.getElementById("stage4Status"),
  stage5Status: document.getElementById("stage5Status"),
  stage6Status: document.getElementById("stage6Status"),
  applyFiltersBtn: document.getElementById("applyFiltersBtn"),
  resetFiltersBtn: document.getElementById("resetFiltersBtn"),
  prevBtn: document.getElementById("prevBtn"),
  nextBtn: document.getElementById("nextBtn"),

  docTitle: document.getElementById("docTitle"),
  docSubtitle: document.getElementById("docSubtitle"),
  sourceUrlBtn: document.getElementById("sourceUrlBtn"),

  statFilteredIndex: document.getElementById("statFilteredIndex"),
  statDocumentKind: document.getElementById("statDocumentKind"),
  statStage4Status: document.getElementById("statStage4Status"),
  statStage5Status: document.getElementById("statStage5Status"),
  statStage6Status: document.getElementById("statStage6Status"),
  statMarkdownLength: document.getElementById("statMarkdownLength"),

  badges: document.getElementById("badges"),
  renderedMarkdown: document.getElementById("renderedMarkdown"),
  rawMarkdown: document.getElementById("rawMarkdown"),
  cleanedHtml: document.getElementById("cleanedHtml"),
  
  stage6Data: document.getElementById("stage6Data"),
  
  quickMetadata: document.getElementById("quickMetadata"),
  fullMetadata: document.getElementById("fullMetadata"),
};

let meta = {
  total_count: 0,
  all_kinds: [],
};

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

function getSelectedKindsFromUI() {
  const checked = Array.from(
    dom.documentKinds.querySelectorAll('input[type="checkbox"]:checked')
  );
  return checked.map((el) => el.value);
}

function syncStateFromUI() {
  state.search = dom.searchInput.value.trim();
  state.only_with_markdown = dom.onlyWithMarkdown.checked;
  state.only_stage4_relevant = dom.onlyStage4Relevant.checked;
  state.stage4_status = dom.stage4Status.value;
  state.stage5_status = dom.stage5Status.value;
  state.stage6_status = dom.stage6Status.value;
  state.document_kinds = getSelectedKindsFromUI();
}

function resetFilters() {
  state.search = "";
  state.only_with_markdown = false;
  state.only_stage4_relevant = false;
  state.stage4_status = "all";
  state.stage5_status = "all";
  state.stage6_status = "all";
  state.document_kinds = [];
  state.selected_index = 0;

  dom.searchInput.value = "";
  dom.onlyWithMarkdown.checked = false;
  dom.onlyStage4Relevant.checked = false;
  dom.stage4Status.value = "all";
  dom.stage5Status.value = "all";
  dom.stage6Status.value = "all";

  Array.from(dom.documentKinds.querySelectorAll('input[type="checkbox"]')).forEach((el) => {
    el.checked = false;
  });
}

function renderDocumentKindCheckboxes() {
  dom.documentKinds.innerHTML = "";

  meta.all_kinds.forEach((kind) => {
    const id = `kind-${kind}`;
    const wrapper = document.createElement("label");
    wrapper.className = "checkbox-item";
    wrapper.innerHTML = `
      <input type="checkbox" id="${id}" value="${escapeHtml(kind)}">
      <span>${escapeHtml(kind)}</span>
    `;
    dom.documentKinds.appendChild(wrapper);
  });
}

async function fetchMeta() {
  const res = await fetch("/api/meta");
  meta = await res.json();

  renderDocumentKindCheckboxes();
  dom.metaSummary.textContent = `Total rows: ${meta.total_count}`;
}

async function queryData() {
  const res = await fetch("/api/query", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(state),
  });

  return await res.json();
}

function renderSidebarItems(items, selectedIndex) {
  dom.documentList.innerHTML = "";

  items.forEach((item) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "document-item";
    if (item.filtered_index === selectedIndex) {
      button.classList.add("active");
    }

    const kind = item.document_kind ?? "—";
    
    // Показываем компактно статусы
    button.innerHTML = `
      <div class="document-item-title">${escapeHtml(item.name)}</div>
      <div class="document-item-meta">
        <span>${escapeHtml(kind)}</span>
        <span>·</span>
        <span>st6=${escapeHtml(item.stage6_status)}</span>
      </div>
    `;

    button.addEventListener("click", async () => {
      state.selected_index = item.filtered_index;
      const data = await queryData();
      renderData(data);
    });

    dom.documentList.appendChild(button);
  });
}

function renderBadges(current) {
  const badges = [];

  if (current.document_kind) {
    badges.push(`<span class="badge badge-blue">${escapeHtml(current.document_kind)}</span>`);
  }

  if (current.stage6_status === "success") {
    badges.push(`<span class="badge badge-green">stage6 success</span>`);
  } else if (current.stage6_status === "null") {
    badges.push(`<span class="badge badge-yellow">stage6 skipped</span>`);
  }

  dom.badges.innerHTML = badges.join("");
}

function renderCurrent(current) {
  if (!current) {
    dom.emptyState.classList.remove("hidden");
    dom.contentSection.classList.add("hidden");
    return;
  }

  dom.emptyState.classList.add("hidden");
  dom.contentSection.classList.remove("hidden");

  dom.docTitle.textContent = current.name || "Unnamed document";
  dom.docSubtitle.textContent = current.description || "—";

  dom.sourceUrlBtn.href = current.url || "#";

  dom.statFilteredIndex.textContent = String(current.filtered_index);
  dom.statDocumentKind.textContent = current.document_kind ?? "—";
  dom.statStage4Status.textContent = current.stage4_status ?? "—";
  dom.statStage5Status.textContent = current.stage5_status ?? "—";
  dom.statStage6Status.textContent = current.stage6_status ?? "—";
  dom.statMarkdownLength.textContent = String(current.markdown_length ?? 0);

  renderBadges(current);

  // Markdown
  if (current.markdown_content && current.markdown_content.trim()) {
    dom.renderedMarkdown.innerHTML = current.markdown_rendered_html || "";
    dom.rawMarkdown.textContent = current.markdown_content;
  } else {
    dom.renderedMarkdown.innerHTML = `<div class="empty-text">This page has no markdown content.</div>`;
    dom.rawMarkdown.textContent = "This page has no markdown content.";
  }

  // HTML
  if (current.cleaned_html && current.cleaned_html.trim()) {
    dom.cleanedHtml.textContent = current.cleaned_html;
  } else {
    dom.cleanedHtml.textContent = "This page has no cleaned_html.";
  }
  
  // Stage 6 Data
  if (current.stage6_extraction_json) {
    dom.stage6Data.textContent = current.stage6_extraction_json;
  } else {
    dom.stage6Data.textContent = "No Stage 6 extraction data available (status: " + current.stage6_status + ").";
  }

  dom.quickMetadata.textContent = current.quick_metadata_json || "{}";
  dom.fullMetadata.textContent = current.full_row_json || "{}";
}

function renderData(data) {
  dom.filteredCount.textContent = `${data.filtered_total} / ${data.total_count}`;
  renderSidebarItems(data.items, data.selected_index);
  renderCurrent(data.current);

  const hasPrev = data.filtered_total > 0 && data.selected_index > 0;
  const hasNext = data.filtered_total > 0 && data.selected_index < data.filtered_total - 1;

  dom.prevBtn.disabled = !hasPrev;
  dom.nextBtn.disabled = !hasNext;
}

async function applyFilters() {
  syncStateFromUI();
  state.selected_index = 0;
  const data = await queryData();
  renderData(data);
}

async function init() {
  await fetchMeta();
  const data = await queryData();
  renderData(data);
}

dom.applyFiltersBtn.addEventListener("click", applyFilters);

dom.resetFiltersBtn.addEventListener("click", async () => {
  resetFilters();
  const data = await queryData();
  renderData(data);
});

dom.prevBtn.addEventListener("click", async () => {
  if (state.selected_index > 0) {
    state.selected_index -= 1;
    const data = await queryData();
    renderData(data);
  }
});

dom.nextBtn.addEventListener("click", async () => {
  state.selected_index += 1;
  const data = await queryData();
  renderData(data);
});

dom.searchInput.addEventListener("keydown", async (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    await applyFilters();
  }
});

document.querySelectorAll(".tab-button").forEach((button) => {
  button.addEventListener("click", () => {
    const tabName = button.dataset.tab;

    document.querySelectorAll(".tab-button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((panel) => panel.classList.remove("active"));

    button.classList.add("active");
    document.getElementById(`tab-${tabName}`).classList.add("active");
  });
});

init();