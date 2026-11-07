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

const el = {
  notice: $("notice"),
  start: $("start"),
  workspace: $("workspace"),
  chip: $("dataset-chip"),
  chipName: $("chip-name"),
  changeButton: $("change-button"),
  dropzone: $("dropzone"),
  fileInput: $("file-input"),
  sampleButton: $("sample-button"),
  datasetName: $("dataset-name"),
  datasetMeta: $("dataset-meta"),
  columnList: $("column-list"),
  previewTable: $("preview-table"),
  thread: $("thread"),
  suggestions: $("suggestions"),
  askForm: $("ask-form"),
  question: $("question"),
  askButton: $("ask-button"),
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
  el.notice.textContent = message;
  el.notice.className = kind === "info" ? "notice notice-info" : "notice";
  el.notice.hidden = false;
}

function hideNotice() {
  el.notice.hidden = true;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function plural(count, word) {
  return `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;
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
  const headRow = table.createTHead().insertRow();
  columns.forEach((name, index) => {
    const th = document.createElement("th");
    th.scope = "col";
    th.textContent = name;
    const first = rows.map((row) => row[index]).find((v) => v !== null && v !== undefined);
    if (typeof first === "number") th.className = "num";
    headRow.appendChild(th);
  });

  const body = table.createTBody();
  for (const row of rows) {
    const tr = body.insertRow();
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

function autoResize() {
  el.question.style.height = "auto";
  el.question.style.height = `${Math.min(el.question.scrollHeight, 180)}px`;
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
  el.suggestions.textContent = "";
  for (const text of list) {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "suggestion";
    button.textContent = text;
    button.addEventListener("click", () => {
      el.question.value = text;
      askQuestion();
    });
    li.appendChild(button);
    el.suggestions.appendChild(li);
  }
}

function typeLabel(type) {
  if (type === "INTEGER") return "whole number";
  if (type === "REAL") return "decimal";
  return "text";
}

function clearResults() {
  el.thread.querySelectorAll(".result").forEach((node) => node.remove());
}

function showDataset(dataset, isSample) {
  state.dataset = dataset;
  state.isSample = isSample;
  rememberDataset();

  el.chipName.textContent = dataset.filename;
  el.datasetName.textContent = dataset.filename;
  el.datasetMeta.textContent = `${plural(dataset.row_count, "row")}, ${plural(dataset.columns.length, "column")}`;

  el.columnList.textContent = "";
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
    el.columnList.appendChild(li);
  }

  renderTable(el.previewTable, dataset.preview.columns, dataset.preview.rows);
  renderSuggestions(suggestionsFor(dataset, isSample));
  clearResults();

  el.start.hidden = true;
  el.workspace.hidden = false;
  el.chip.hidden = false;
  el.question.focus();
}

function resetDataset() {
  state.dataset = null;
  state.isSample = false;
  rememberDataset();
  clearResults();
  el.workspace.hidden = true;
  el.chip.hidden = true;
  el.start.hidden = false;
  el.fileInput.value = "";
}

function setLoadingDataset(isLoading) {
  el.sampleButton.disabled = isLoading;
  el.sampleButton.textContent = isLoading ? "Loading sample data..." : "Try it with sample sales data";
  el.dropzone.setAttribute("aria-busy", String(isLoading));
}

async function uploadFile(file) {
  if (!file) return;
  hideNotice();
  if (!/\.(csv|tsv|txt)$/i.test(file.name)) {
    showNotice("Choose a .csv file. Excel files can be saved as CSV with File, then Save As.");
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
  const card = el.template.content.firstElementChild.cloneNode(true);
  card.querySelector(".result-question").textContent = question;
  card.querySelector(".result-answer").textContent = "Writing a query...";
  card.classList.add("is-loading");
  card.querySelector(".result-table").hidden = true;
  card.querySelector(".sql").hidden = true;
  el.thread.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "start" });
  return card;
}

function fillResultCard(card, result) {
  card.classList.remove("is-loading");
  card.querySelector(".result-answer").textContent = result.answer;

  if (result.status === "failed") card.classList.add("is-error");

  if (result.status === "ok") {
    const note = result.truncated ? `Showing the first ${result.row_count} rows.` : "";
    renderTable(card.querySelector(".result-table"), result.columns, result.rows, note);
  }

  const meta = [];
  if (result.status === "ok") meta.push(plural(result.row_count, "row"));
  meta.push(`answered in ${(result.elapsed_ms / 1000).toFixed(1)} s`);
  if (result.status === "ok" && result.failed_attempts.length) {
    meta.push(`fixed its own query after ${plural(result.failed_attempts.length, "failed attempt")}`);
  }
  card.querySelector(".result-meta").textContent = meta.join(", ");

  if (result.sql || result.failed_attempts.length) {
    const sql = card.querySelector(".sql");
    sql.hidden = false;
    if (result.status === "failed") {
      sql.querySelector("summary").textContent = "Show the queries that were tried";
    }
    const code = card.querySelector(".sql-code");
    const copy = card.querySelector(".copy-button");
    if (result.sql) {
      code.innerHTML = highlightSql(result.sql);
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(result.sql);
          copy.textContent = "Copied";
        } catch (error) {
          copy.textContent = "Copy failed";
        }
        setTimeout(() => (copy.textContent = "Copy SQL"), 1600);
      });
    } else {
      copy.hidden = true;
      code.parentElement.hidden = true;
    }

    if (result.failed_attempts.length) {
      const attempts = card.querySelector(".attempts");
      attempts.hidden = false;
      attempts.querySelector(".attempts-title").textContent =
        result.status === "ok" ? "Earlier attempts that failed:" : "Attempts:";
      const list = attempts.querySelector(".attempt-list");
      for (const attempt of result.failed_attempts) {
        const li = document.createElement("li");
        const attemptCode = document.createElement("code");
        attemptCode.textContent = attempt.sql || "(no query)";
        const error = document.createElement("span");
        error.className = "attempt-error";
        error.textContent = attempt.error;
        li.append(attemptCode, error);
        list.appendChild(li);
      }
    }
  }
}

function failResultCard(card, message) {
  card.classList.remove("is-loading");
  card.classList.add("is-error");
  card.querySelector(".result-answer").textContent = message;
}

async function askQuestion() {
  const question = el.question.value.trim();
  if (!question || state.busy || !state.dataset) return;

  hideNotice();
  state.busy = true;
  el.askButton.disabled = true;
  el.askButton.textContent = "Asking...";
  el.question.value = "";
  autoResize();
  const card = createResultCard(question);

  try {
    const result = await api("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dataset_id: state.dataset.id, question }),
    });
    fillResultCard(card, result);
  } catch (error) {
    failResultCard(card, error.message);
    if (/not found/i.test(error.message)) {
      resetDataset();
      showNotice(error.message);
    }
  } finally {
    state.busy = false;
    el.askButton.disabled = false;
    el.askButton.textContent = "Ask";
    el.question.focus();
  }
}

/* Events */

el.fileInput.addEventListener("change", () => uploadFile(el.fileInput.files[0]));
el.sampleButton.addEventListener("click", loadSample);
el.changeButton.addEventListener("click", resetDataset);

for (const eventName of ["dragenter", "dragover"]) {
  el.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    el.dropzone.classList.add("is-dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  el.dropzone.addEventListener(eventName, (event) => {
    event.preventDefault();
    el.dropzone.classList.remove("is-dragging");
  });
}
el.dropzone.addEventListener("drop", (event) => {
  uploadFile(event.dataTransfer && event.dataTransfer.files[0]);
});

el.askForm.addEventListener("submit", (event) => {
  event.preventDefault();
  askQuestion();
});

el.question.addEventListener("input", autoResize);
el.question.addEventListener("keydown", (event) => {
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
