"use strict";

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("button[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const value = button.getAttribute("data-copy") || "";
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          await navigator.clipboard.writeText(value);
        } else {
          const field = document.createElement("textarea");
          field.value = value;
          field.style.position = "fixed";
          field.style.opacity = "0";
          document.body.append(field);
          field.select();
          if (!document.execCommand("copy")) throw new Error("copy blocked");
          field.remove();
        }
        const previous = button.textContent;
        button.textContent = "已复制";
        setTimeout(() => { button.textContent = previous; }, 1200);
      } catch {
        button.textContent = "请手动复制";
      }
    });
  });

  const search = document.querySelector("[data-search]");
  const filters = Array.from(document.querySelectorAll("[data-filter]"));
  const filterItems = Array.from(document.querySelectorAll(".filter-item"));
  function applyFilters() {
    const needle = (search?.value || "").trim().toLocaleLowerCase();
    filterItems.forEach((item) => {
      const matchesText = !needle || (item.dataset.searchValue || "").toLocaleLowerCase().includes(needle);
      const matchesOther = filters.every((filter) => {
        if (!filter.value) return true;
        const value = item.dataset[filter.dataset.filter] || "";
        return filter.dataset.filter === "prompt" ? value.split(" ").includes(filter.value) : value === filter.value;
      });
      item.hidden = !(matchesText && matchesOther);
    });
  }
  search?.addEventListener("input", applyFilters);
  filters.forEach((filter) => filter.addEventListener("change", applyFilters));

  if (document.getElementById("compare-output")) initializeComparison();
});

function node(tag, text, className) {
  const result = document.createElement(tag);
  if (text !== undefined && text !== null) result.textContent = String(text);
  if (className) result.className = className;
  return result;
}

function addLine(parent, label, value) {
  const row = node("p", undefined, "compare-line");
  row.append(node("strong", label + "："));
  row.append(node("span", value === undefined || value === null ? "—" : value));
  parent.append(row);
}

function initializeComparison() {
  const data = window.OBSERVER_COMPARE;
  const selectA = document.getElementById("compare-a");
  const selectB = document.getElementById("compare-b");
  const output = document.getElementById("compare-output");
  if (!data || !selectA || !selectB || !output) {
    output?.append(node("p", "比较数据不可用。"));
    return;
  }
  if (data.defaultPair) {
    selectA.value = data.defaultPair[0];
    selectB.value = data.defaultPair[1];
  }
  function render() {
    output.replaceChildren();
    const runA = data.runs.find((run) => run.runId === selectA.value);
    const runB = data.runs.find((run) => run.runId === selectB.value);
    if (!runA || !runB || runA.runId === runB.runId) {
      output.append(node("p", "请选择两个不同的 Run。", "notice"));
      return;
    }
    if (runA.model !== runB.model) {
      output.append(node("p", "模型不同：仍可查看字段差异，但不应直接归因为 Prompt 效果。", "notice warning"));
    }
    const first = new Map(runA.items.map((item) => [item.imageSha256, item]));
    const second = new Map(runB.items.map((item) => [item.imageSha256, item]));
    const common = Array.from(first.keys()).filter((key) => second.has(key)).sort();
    output.append(node("p", `共同图片 ${common.length} 张；按完整 SHA-256 对齐。`, "muted"));
    for (const digest of common) {
      const a = first.get(digest);
      const b = second.get(digest);
      const image = data.images[digest] || { displayName: digest };
      const section = node("section", undefined, "comparison");
      const title = node("h2");
      const link = node("a", image.displayName);
      link.href = `images/${digest}.html`;
      title.append(link);
      section.append(title);
      if (image.thumbnail && /^thumbnails\/[0-9a-f]{64}\.png$/.test(image.thumbnail)) {
        const thumb = node("img", undefined, "thumb");
        thumb.src = image.thumbnail;
        thumb.alt = image.displayName;
        section.append(thumb);
      }
      const columns = node("div", undefined, "compare-columns");
      for (const [run, item, other] of [[runA, a, b], [runB, b, a]]) {
        const card = node("div", undefined, "compare-card");
        const runLink = node("a", run.runId);
        runLink.href = `runs/${run.runId}.html`;
        const heading = node("h3");
        heading.append(runLink);
        card.append(heading);
        addLine(card, "Prompt", run.promptVersion);
        addLine(card, "状态", item.status);
        addLine(card, "错误", item.errorCode);
        addLine(card, "调用尝试", item.attempts);
        addLine(card, "Tokens", item.usage?.totalTokens);
        addLine(card, "耗时 ms", item.elapsedMs);
        addLine(card, "Cache key", item.cacheKey);
        const grade = run.grades[digest];
        addLine(card, "人工等级", grade?.grade);
        if (grade?.note) addLine(card, "人工备注", grade.note);
        for (const field of ["imageType", "action", "knowledgeValue", "retrievalDecision", "description", "extractedText", "code", "table", "chart", "keyFacts", "uncertainties"]) {
          const value = item.result?.[field];
          const otherValue = other.result?.[field];
          const changed = JSON.stringify(value) !== JSON.stringify(otherValue);
          const block = node("div", undefined, changed ? "compare-field changed" : "compare-field");
          block.append(node("strong", field));
          block.append(node("pre", value === undefined || value === null ? "—" :
            typeof value === "string" ? value : JSON.stringify(value, null, 2)));
          card.append(block);
        }
        columns.append(card);
      }
      section.append(columns);
      output.append(section);
    }
  }
  selectA.addEventListener("change", render);
  selectB.addEventListener("change", render);
  render();
}
