// Reloads the page every 30 seconds. The server renders it; this only swaps the content,
// without innerHTML, and keeps what the reader opened or sorted.
"use strict";

const REFRESH_MS = 30000;
const TIMEOUT_MS = 10000;
let sort = { column: "status", descending: false };
let refreshing = false;

function sortProblems() {
  const table = document.getElementById("problems");
  if (!table) return;
  const headers = Array.from(table.tHead.rows[0].cells);
  const index = headers.findIndex((cell) => cell.querySelector("button").dataset.sort === sort.column);
  const value = (row) => row.cells[index].dataset.sortValue ?? row.cells[index].textContent.trim();
  const rows = Array.from(table.tBodies[0].rows);
  // The order of the server decides between equal rows, whatever was sorted before.
  rows.forEach((row, position) => {
    row.dataset.order ??= String(position);
  });
  rows.sort((a, b) => {
    const order = value(a).localeCompare(value(b), document.documentElement.lang, { numeric: true });
    if (order === 0) return Number(a.dataset.order) - Number(b.dataset.order);
    return sort.descending ? -order : order;
  });
  table.tBodies[0].append(...rows);
  headers.forEach((cell, position) => {
    const direction = sort.descending ? "descending" : "ascending";
    cell.setAttribute("aria-sort", position === index ? direction : "none");
  });
}

// A way to find the focused control again in the new content.
function focusedControl() {
  const element = document.activeElement;
  if (element?.dataset?.sort) return `button[data-sort="${element.dataset.sort}"]`;
  const key = element?.tagName === "SUMMARY" ? element.parentElement.dataset.key : undefined;
  return key ? `details[data-key="${CSS.escape(key)}"] > summary` : null;
}

function show(fresh) {
  const current = document.getElementById("content");
  const opened = new Set(
    Array.from(current.querySelectorAll("details[open]"), (element) => element.dataset.key),
  );
  const scrolled = new Map(
    Array.from(current.querySelectorAll("[data-scroll]"), (element) => [
      element.dataset.scroll,
      element.scrollLeft,
    ]),
  );
  const focused = focusedControl();
  const content = document.importNode(fresh, true);
  content.querySelectorAll("details").forEach((element) => {
    element.open = opened.has(element.dataset.key);
  });
  current.replaceWith(content);
  sortProblems();
  content.querySelectorAll("[data-scroll]").forEach((element) => {
    element.scrollLeft = scrolled.get(element.dataset.scroll) ?? 0;
  });
  // Without scrolling: the reader may be far from the focused control.
  if (focused) content.querySelector(focused)?.focus({ preventScroll: true });
}

function setOutdated(outdated) {
  document.body.classList.toggle("outdated", outdated);
  document.getElementById("refresh-failed").hidden = !outdated;
}

async function refresh() {
  if (refreshing) return;
  refreshing = true;
  try {
    const response = await fetch("./", { cache: "no-store", signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!response.ok) throw new Error("refused");
    const page = new DOMParser().parseFromString(await response.text(), "text/html");
    // Only a page of STETHO: a sign-in or error page may also have a "content" element.
    const fresh = page.querySelector("main#content[data-stetho-page]");
    if (!fresh) throw new Error("unexpected page");
    show(fresh);
    setOutdated(false);
  } catch {
    setOutdated(true);
  } finally {
    refreshing = false;
  }
}

document.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-sort]");
  if (!button) return;
  const column = button.dataset.sort;
  sort = { column, descending: sort.column === column && !sort.descending };
  sortProblems();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refresh();
});
setInterval(refresh, REFRESH_MS);
