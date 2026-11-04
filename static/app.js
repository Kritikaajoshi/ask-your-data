"use strict";

const SAMPLE_QUESTIONS = [
  "Which region had the highest total revenue?",
  "What were the top 3 products by units sold?",
  "How did monthly revenue change during 2025?",
  "What percentage of orders came from returning customers?",
  "Compare average order revenue for online and in store orders",
];

const SQL_KEYWORDS = new Set([
  "select", "from", "where", "group", "by", "order", "limit", "as", "and", "or", "not",
  "in", "is", "null", "join", "left", "right", "inner", "outer", "on", "having", "distinct",
  "with", "union", "all", "case", "when", "then", "else", "end", "asc", "desc", "between",
  "like", "over", "partition", "offset", "recursive", "cast", "exists", "count", "sum", "avg",
  "min", "max", "round", "strftime", "substr", "coalesce", "row_number", "rank", "lower", "upper",
]);

const state = {
  dataset: null,
  isSample: false,
  busy: false,
};

const $ = (id) => document.getElementById(id);

const elements = {
  notice: $("notice"),
  noDataset: $("no-dataset"),
  datasetInfo: $("dataset-info"),
  datasetName: $("dataset-name"),
  datasetMeta: $("dataset-meta"),
  columnList: $("column-list"),
  changeButton: $("change-button"),
  dropzone: $("dropzone"),
  fileInput: $("file-input"),
  sampleButton: $("sample-button"),
  emptyState: $("empty-state"),
  askArea: $("ask-area"),
  askForm: $("ask-form"),
  question: $("question"),
  askButton: $("ask-button"),
  suggestions: $("suggestions"),
  previewTable: $("preview-table"),
  results: $("results"),
  template: $("result-template"),
};

/* Helpers */

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, options);
  } catch (error) {
    throw new Error("Could not reach the server. Check that it is running.");
  }
  let body = null;
  try {
    body = await response.json();
  } catch (error) {
    body = null;
  }
  if (!response.ok) {
    let message = `Request failed (${response.status}).`;
    if (body && typeof body.detail === "string") {
      message = body.detail;
    } else if (body && Array.isArray(body.detail) && body.detail[0] && body.detail[0].msg) {
      message = body.detail[0].msg;
    }
    throw new Error(message);
  }
  return body;
}

function showNotice(message, kind = "error") {
  elements.notice.textContent = message;
  elements.notice.className = kind === "info" ? "notice notice-info" : "notice";
  elements.notice.hidden = false;
}

function hideNotice() {
  elements.notice.hidden = true;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function formatCell(value) {
  if (value === null || value === undefined) {
    return { text: "empty", className: "null" };
  }
  if (typeof value === "number") {
    if (Number.isInteger(value)) {
      return { text: String(value), className: "num" };
    }
    return {
      text: value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }),
      className: "num",
    };
  }
  return { text: String(value), className: "" };
}

function renderTable(container, columns, rows, note) {
  container.textContent = "";
  if (!columns.length) {
    container.hidden = true;
    return;
  }
  container.hidden = false;

  const table = document.createElement("table");
  const thead = table.createTHead();
  const headRow = thead.insertRow();
  columns.forEach((name, index) => {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = name;
    const firstValue = rows.map((row) => row[index]).find((v) => v !== null && v !== undefined);
    if (typeof firstValue === "number") th.className = "num";
    headRow.appendChild(th);
  });

  const tbody = table.createTBody();
  for (const row of rows) {
    const tr = tbody.insertRow();
    for (const value of row) {
      const td = tr.insertCell();
      const cell = formatCell(value);
      td.textContent = cell.text;
      if (cell.className) td.className = cell.className;
    }
  }
  container.appendChild(table);

  if (note) {
    const p = document.createElement("p");
    p.className = "table-note";
    p.textContent = note;
    container.appendChild(p);
  }
}

function highlightSql(sql) {
  const pattern = /('(?:[^']|'')*'?)|("(?:[^"]|"")*"?)|([A-Za-z_][A-Za-z0-9_]*)|(\d+(?:\.\d+)?)|([\s\S])/g;
  let html = "";
  let match;
  while ((match = pattern.exec(sql)) !== null) {
    const [token, single, double, word, number] = match;
    if (single !== undefined) {
      html += `<span class="str">${escapeHtml(token)}</span>`;
    } else if (double !== undefined) {
      html += escapeHtml(token);
    } else if (word !== undefined && SQL_KEYWORDS.has(word.toLowerCase())) {
      html += `<span class="kw">${escapeHtml(token)}</span>`;
    } else if (number !== undefined) {
      html += `<span class="num">${escapeHtml(token)}</span>`;
    } else {
      html += escapeHtml(token);
    }
  }
  return html;
}

function plural(count, word) {
  return `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
}

function rememberDataset() {
  try {
    if (state.dataset) {
      sessionStorage.setItem(
        "ask-your-data",
        JSON.stringify({ id: state.dataset.id, isSample: state.isSample })
      );
    } else {
      sessionStorage.removeItem("ask-your-data");
    }
  } catch (error) {
    // Storage can be blocked by the browser. The app works without it.
  }
}

/* Dataset */

function suggestionsFor(dataset, isSample) {
  if (isSample) return SAMPLE_QUESTIONS;

  const columns = dataset.columns.filter((c) => !/(^|_)id$/.test(c.name));
  const text = columns.find((c) => c.type === "TEXT");
  const number = columns.find((c) => c.type === "INTEGER" || c.type === "REAL");
  const list = ["How many rows are there?"];
  if (text) list.push(`What are the 5 most common values of ${text.original_name}?`);
  if (number) list.push(`What is the average ${number.original_name}?`);
  if (text && number) list.push(`What is the total ${number.original_name} for each ${text.original_name}?`);
  return list;
}

function renderSuggestions(list) {
  elements.suggestions.textContent = "";
  for (const text of list) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion";
    button.textContent = text;
    button.addEventListener("click", () => {
      elements.question.value = text;
      askQuestion();
    });
    elements.suggestions.appendChild(button);
  }
}

function typeLabel(type) {
  if (type === "INTEGER") return "whole number";
  if (type === "REAL") return "decimal";
  return "text";
}

function showDataset(dataset, isSample) {
  state.dataset = dataset;
  state.isSample = isSample;
  rememberDataset();

  elements.datasetName.textContent = dataset.filename;
  elements.datasetMeta.textContent = `${plural(dataset.row_count, "row")}, ${plural(dataset.columns.length, "column")}`;

  elements.columnList.textContent = "";
  for (const column of dataset.columns) {
    const li = document.createElement("li");
    const name = document.createElement("span");
    name.className = "column-name";
    name.textContent = column.name;
    if (column.original_name && column.original_name !== column.name) {
      name.title = `Original header: ${column.original_name}`;
    }
    const type = document.createElement("span");
    type.className = "column-type";
    type.textContent = typeLabel(column.type);
    li.append(name, type);
    elements.columnList.appendChild(li);
  }

  renderTable(elements.previewTable, dataset.preview.columns, dataset.preview.rows);
  renderSuggestions(suggestionsFor(dataset, isSample));

  elements.noDataset.hidden = true;
  elements.datasetInfo.hidden = false;
  elements.emptyState.hidden = true;
  elements.askArea.hidden = false;
  elements.results.textContent = "";
  elements.question.focus();
}

function resetDataset() {
  state.dataset = null;
  state.isSample = false;
  rememberDataset();
  elements.noDataset.hidden = false;
  elements.datasetInfo.hidden = true;
  elements.emptyState.hidden = false;
  elements.askArea.hidden = true;
  elements.results.textContent = "";
  elements.fileInput.value = "";
}

function setLoadingDataset(isLoading) {
  elements.sampleButton.disabled = isLoading;
  elements.sampleButton.textContent = isLoading ? "Loading..." : "Use sample sales data";
  elements.dropzone.setAttribute("aria-busy", String(isLoading));
}

async function uploadFile(file) {
  if (!file) return;
  hideNotice();
  if (!/\.(csv|tsv|txt)$/i.test(file.name)) {
    showNotice("Please choose a .csv file.");
    return;
  }
  setLoadingDataset(true);
  try {
    const form = new FormData();
    form.append("file", file);
    const dataset = await api("/api/datasets", { method: "POST", body: form });
    showDataset(dataset, false);
  } catch (error) {
    showNotice(error.message);
  } finally {
    setLoadingDataset(false);
  }
}

async function loadSample() {
  hideNotice();
  setLoadingDataset(true);
  try {
    const dataset = await api("/api/datasets/sample", { method: "POST" });
    showDataset(dataset, true);
  } catch (error) {
    showNotice(error.message);
  } finally {
    setLoadingDataset(false);
  }
}

/* Asking */

function createResultCard(question) {
  const card = elements.template.content.firstElementChild.cloneNode(true);
  card.querySelector(".result-question").textContent = question;
  const answer = card.querySelector(".result-answer");
  answer.innerHTML = '<span class="loading-dots">Writing a query</span>';
  card.classList.add("is-loading", "is-new");
  card.querySelector(".sql-block").hidden = true;
  card.querySelector(".result-table").hidden = true;
  elements.results.prepend(card);
  return card;
}

function fillResultCard(card, result) {
  card.classList.remove("is-loading");
  card.querySelector(".result-answer").textContent = result.answer;

  const sqlBlock = card.querySelector(".sql-block");
  if (result.sql) {
    sqlBlock.hidden = false;
    card.querySelector(".sql-code").innerHTML = highlightSql(result.sql);
    const copy = card.querySelector(".copy-button");
    copy.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(result.sql);
        copy.textContent = "Copied";
      } catch (error) {
        copy.textContent = "Copy failed";
      }
      setTimeout(() => (copy.textContent = "Copy"), 1600);
    });
  }

  if (result.status === "failed") {
    card.classList.add("is-error");
    card.querySelector(".sql-label").textContent = "Last query tried";
  }

  if (result.status === "ok") {
    const note = result.truncated ? `Showing the first ${result.row_count} rows.` : "";
    renderTable(card.querySelector(".result-table"), result.columns, result.rows, note);
  }

  const meta = [];
  if (result.status === "ok") meta.push(plural(result.row_count, "row"));
  meta.push(`${(result.elapsed_ms / 1000).toFixed(1)} s`);
  if (result.status === "ok" && result.failed_attempts.length) {
    meta.push(`fixed itself after ${plural(result.failed_attempts.length, "failed attempt")}`);
  }
  card.querySelector(".result-meta").textContent = meta.join(", ");

  if (result.failed_attempts.length) {
    const details = card.querySelector(".attempts");
    details.hidden = false;
    details.querySelector("summary").textContent = "Show failed attempts";
    const list = details.querySelector(".attempt-list");
    for (const attempt of result.failed_attempts) {
      const li = document.createElement("li");
      const code = document.createElement("code");
      code.textContent = attempt.sql || "(no query)";
      const error = document.createElement("span");
      error.className = "attempt-error";
      error.textContent = attempt.error;
      li.append(code, error);
      list.appendChild(li);
    }
  }
}

function failResultCard(card, message) {
  card.classList.remove("is-loading");
  card.classList.add("is-error");
  card.querySelector(".result-answer").textContent = message;
}

async function askQuestion() {
  const question = elements.question.value.trim();
  if (!question || state.busy || !state.dataset) return;

  hideNotice();
  state.busy = true;
  elements.askButton.disabled = true;
  elements.askButton.textContent = "Asking...";
  const card = createResultCard(question);

  try {
    const result = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset_id: state.dataset.id, question }),
    });
    fillResultCard(card, result);
    elements.question.value = "";
  } catch (error) {
    failResultCard(card, error.message);
    if (/not found/i.test(error.message)) {
      resetDataset();
      showNotice(error.message);
    }
  } finally {
    state.busy = false;
    elements.askButton.disabled = false;
    elements.askButton.textContent = "Ask";
  }
}

/* Events */

elements.fileInput.addEventListener("change", () => uploadFile(elements.fileInput.files[0]));
elements.sampleButton.addEventListener("click", loadSample);
elements.changeButton.addEventListener("click", resetDataset);

for (const eventName of ["dragenter", "dragover"]) {
  elements.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropzone.classList.add("is-dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  elements.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    elements.dropzone.classList.remove("is-dragging");
  });
}
elements.dropzone.addEventListener("drop", (event) => {
  const file = event.dataTransfer && event.dataTransfer.files[0];
  uploadFile(file);
});

elements.askForm.addEventListener("submit", (event) => {
  event.preventDefault();
  askQuestion();
});

elements.question.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    askQuestion();
  }
});

/* Startup */

async function start() {
  try {
    const health = await api("/api/health");
    if (!health.llm_configured) {
      showNotice(
        "The AI model is not set up yet. Add GEMINI_API_KEY to your .env file and restart the server. You can still upload and preview data.",
        "info"
      );
    }
  } catch (error) {
    showNotice(error.message);
  }

  let saved = null;
  try {
    saved = JSON.parse(sessionStorage.getItem("ask-your-data") || "null");
  } catch (error) {
    saved = null;
  }
  if (saved && saved.id) {
    try {
      const dataset = await api(`/api/datasets/${encodeURIComponent(saved.id)}`);
      showDataset(dataset, Boolean(saved.isSample));
    } catch (error) {
      rememberDataset();
    }
  }
}

start();
