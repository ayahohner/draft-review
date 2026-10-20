"use strict";
let session,
  decisions = {},
  current = null,
  dirty = false,
  saving = false,
  timer,
  serial = 0,
  conflict = false;
const $ = (selector) => document.querySelector(selector);
const list = $("#review-list");
const cards = new Map();
const openedNotes = new Set();
let changed = [],
  cacheKey;
const clone = (value) => JSON.parse(JSON.stringify(value));
const decisionFor = (row) => ({
  choice: "accept",
  reviewed: false,
  note: "",
  text: row.after || "",
  ...decisions[row.id],
});
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function button(text, action, className = "") {
  const b = el("button", className, text);
  b.type = "button";
  b.addEventListener("click", action);
  return b;
}
function showError(text) {
  $("#error").textContent = text;
  $("#error").hidden = !text;
}
function selectedText(row) {
  const d = decisionFor(row);
  return d.choice === "reject"
    ? row.before || ""
    : d.choice === "edit"
      ? d.text
      : row.after || "";
}
function output() {
  return session.rows
    .map((row) => (row.changed ? selectedText(row) : row.after || ""))
    .join("");
}
function recoverable() {
  return {
    fingerprint: session.fingerprint,
    revision: session.revision,
    serial,
    decisions,
    dirty,
  };
}
function cache() {
  try {
    localStorage.setItem(cacheKey, JSON.stringify(recoverable()));
  } catch {
    showError(
      "Browser recovery storage is unavailable. Keep this tab open until the status says saved to disk.",
    );
  }
}
function changedState() {
  dirty = true;
  serial++;
  cache();
  $("#save-status").textContent = "Saving…";
  clearTimeout(timer);
  timer = setTimeout(save, 250);
  updateSummary();
}
async function save() {
  if (!dirty || saving || conflict) return;
  saving = true;
  const generation = serial;
  try {
    const response = await fetch("/api/save", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Review-Token": session.token,
      },
      body: JSON.stringify({ revision: session.revision, decisions }),
    });
    const data = await response.json();
    if (!response.ok) {
      if (response.status === 409) conflict = true;
      throw new Error(data.error || "Save failed.");
    }
    session.revision = data.revision;
    if (generation === serial) {
      dirty = false;
      $("#save-status").textContent = "✓ Saved to disk";
    }
    cache();
    showError(data.exportError || "");
  } catch (error) {
    $("#save-status").textContent = "Not saved to disk";
    showError(
      error.message +
        " Your changes are kept in this browser. Use Export review for a recovery copy.",
    );
  } finally {
    saving = false;
    if (dirty && !conflict) {
      clearTimeout(timer);
      timer = setTimeout(save, 1200);
    }
  }
}
function updateSummary() {
  const reviewed = changed.filter((r) => decisions[r.id]?.reviewed).length;
  const notes = changed.filter((r) => decisions[r.id]?.note?.trim()).length;
  $("#summary").textContent =
    `${reviewed} of ${changed.length} reviewed${notes ? " · " + notes + " with notes" : ""}`;
  $("#progress").max = changed.length || 1;
  $("#progress").value = reviewed;
  for (const b of $("#sections").children) {
    const rows = changed.filter((r) => r.section === b.dataset.section);
    b.lastChild.textContent = `${rows.filter((r) => decisions[r.id]?.reviewed).length}/${rows.length}`;
  }
}
function focusRow(id, scroll = true) {
  if (current !== null) cards.get(current)?.classList.remove("current");
  current = id;
  cards.get(id)?.classList.add("current");
  const index = changed.findIndex((r) => r.id === id);
  $("#position").textContent =
    index >= 0 ? `${index + 1} / ${changed.length}` : "—";
  if (scroll)
    cards.get(id)?.scrollIntoView({ block: "start", behavior: "instant" });
}
function visibleRows() {
  return changed.filter((r) => cards.has(r.id));
}
function move(direction) {
  const rows = visibleRows();
  if (!rows.length) return;
  const index = rows.findIndex((r) => r.id === current);
  const next =
    rows[
      Math.max(
        0,
        Math.min(rows.length - 1, (index < 0 ? 0 : index) + direction),
      )
    ];
  focusRow(next.id);
  cards.get(next.id)?.focus({ preventScroll: true });
}
function setChoice(row, choice, advance = false) {
  const previousRows = visibleRows();
  const previousIndex = previousRows.findIndex((r) => r.id === row.id);
  const d = clone(decisionFor(row));
  d.choice = choice;
  d.reviewed = true;
  decisions[row.id] = d;
  if (choice === "edit" && !passes(row)) {
    $("#filter").value = "all";
    render();
    focusRow(row.id);
  } else if (!passes(row)) {
    render();
    const next =
      previousRows.slice(previousIndex + 1).find((r) => cards.has(r.id)) ||
      previousRows
        .slice(0, previousIndex)
        .reverse()
        .find((r) => cards.has(r.id));
    if (next) {
      focusRow(next.id);
      cards.get(next.id)?.focus({ preventScroll: true });
    }
    advance = false;
  } else refreshCard(row);
  changedState();
  if (advance) move(1);
}
function refreshCard(row) {
  const existing = cards.get(row.id);
  if (!existing) return;
  const replacement = makeCard(row);
  existing.replaceWith(replacement);
  cards.set(row.id, replacement);
  if (current === row.id) replacement.classList.add("current");
}
function textRow(row, before) {
  const text = before ? row.before : row.after;
  const node = el("div", "text-row " + (before ? "before" : "after"));
  const num = before ? row.oldLine : row.newLine;
  node.append(el("span", "row-label", `${before ? "−" : "+"} ${num ?? "—"}`));
  const content = el("div", "text");
  if (text === null || text === "")
    content.append(
      el("span", "empty-line", before ? "No original line" : "Line removed"),
    );
  else if (!text.trim()) content.append(el("span", "empty-line", "Blank line"));
  else content.innerHTML = before ? row.beforeHtml : row.afterHtml; // Escaped on the server, only <mark> is added.
  node.append(content);
  return node;
}
function autoHeight(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = Math.max(80, textarea.scrollHeight + 2) + "px";
}
function makeCard(row) {
  const d = decisionFor(row),
    card = el("article", "change");
  card.dataset.id = row.id;
  card.dataset.choice = d.choice;
  card.tabIndex = -1;
  card.setAttribute(
    "aria-label",
    `Change at original line ${row.oldLine ?? "new insertion"}`,
  );
  card.addEventListener("pointerdown", () => focusRow(row.id, false));
  card.addEventListener("focusin", () => focusRow(row.id, false));
  const top = el("div", "change-top"),
    meta = el("div", "change-meta");
  meta.append(
    el("strong", "", `Change ${changed.findIndex((r) => r.id === row.id) + 1}`),
    el(
      "span",
      "",
      `Original ${row.oldLine ?? "—"} → revised ${row.newLine ?? "—"}`,
    ),
    el("span", "review-label", d.reviewed ? "Reviewed" : "Not reviewed"),
  );
  if (
    row.before !== null &&
    row.after !== null &&
    row.before !== row.after &&
    row.before.trim() === row.after.trim()
  )
    meta.append(el("span", "", "Spacing or line-ending change"));
  const actions = el("div", "actions");
  for (const [choice, label] of [
    ["accept", "✓ Accept"],
    ["reject", "× Reject"],
    ["edit", "Edit"],
  ]) {
    const b = button(
      label,
      () => {
        setChoice(row, choice);
        if (choice === "edit") {
          const input = cards.get(row.id).querySelector(".replacement");
          input.focus();
          autoHeight(input);
        } else {
          cards
            .get(row.id)
            ?.querySelector("." + choice)
            ?.focus({ preventScroll: true });
        }
      },
      choice,
    );
    b.setAttribute("aria-pressed", String(d.choice === choice));
    b.title =
      choice === "reject"
        ? "Restore the original line"
        : choice === "accept"
          ? "Keep the proposed line"
          : "Write your own replacement";
    actions.append(b);
  }
  const noteButton = button(d.note?.trim() ? "Note •" : "Note", () => {
    openedNotes.add(row.id);
    refreshCard(row);
    cards.get(row.id).querySelector(".note").focus();
  });
  noteButton.setAttribute(
    "aria-expanded",
    String(openedNotes.has(row.id) || !!d.note),
  );
  actions.append(noteButton);
  top.append(meta, actions);
  card.append(top, textRow(row, true), textRow(row, false));
  if (d.choice === "edit") {
    const area = el("div", "edit-area"),
      label = el("label", "", "Your replacement"),
      input = el("textarea", "replacement");
    input.id = "edit-" + row.id;
    label.htmlFor = input.id;
    input.value = d.text.replace(/\r?\n$/, "");
    input.addEventListener("input", () => {
      const next = clone(decisionFor(row));
      next.choice = "edit";
      next.reviewed = true;
      // Keep the line terminator from the source unless the user removes the line entirely.
      const ending = (row.after ?? row.before ?? "").match(/\r?\n$/)?.[0] || "";
      next.text = input.value ? input.value + ending : "";
      decisions[row.id] = next;
      autoHeight(input);
      changedState();
    });
    area.append(
      label,
      input,
      el(
        "p",
        "hint",
        "Saved as you type. Clear the field to remove this line. Accept or reject to switch back; your edit is retained.",
      ),
    );
    card.append(area);
    requestAnimationFrame(() => autoHeight(input));
  }
  if (openedNotes.has(row.id) || d.note) {
    const area = el("div", "note-area"),
      label = el("label", "", "Instruction for the next revision"),
      input = el("textarea", "note");
    input.id = "note-" + row.id;
    label.htmlFor = input.id;
    input.placeholder =
      "For example: keep the opening sentence, but simplify the explanation.";
    input.value = d.note || "";
    input.addEventListener("input", () => {
      decisions[row.id] = {
        ...decisionFor(row),
        note: input.value,
        reviewed: true,
      };
      card.querySelector(".review-label").textContent = "Reviewed";
      noteButton.textContent = input.value.trim() ? "Note •" : "Note";
      changedState();
      autoHeight(input);
    });
    input.addEventListener("blur", () => {
      if (!passes(row)) {
        render();
      }
    });
    area.append(
      label,
      input,
      el(
        "p",
        "hint",
        "Included in the review file. This note does not change the resulting Markdown.",
      ),
    );
    card.append(area);
  }
  return card;
}
function passes(row) {
  const filter = $("#filter").value,
    d = decisionFor(row);
  return (
    filter === "all" ||
    (filter === "pending" && !d.reviewed) ||
    (filter === "notes" && !!d.note.trim()) ||
    filter === d.choice
  );
}
function render() {
  list.replaceChildren();
  cards.clear();
  let previousSection = null;
  const context = $("#context").checked;
  for (const row of session.rows) {
    if (row.changed && !passes(row)) continue;
    if (!row.changed && !context) continue;
    if (row.section !== previousSection) {
      const heading = el("h2", "section-title", row.section);
      list.append(heading);
      previousSection = row.section;
    }
    if (row.changed) {
      const card = makeCard(row);
      cards.set(row.id, card);
      list.append(card);
    } else if (row.after?.trim()) {
      list.append(el("div", "unchanged", row.after));
    }
  }
  if (!cards.size)
    list.append(
      el(
        "p",
        "empty",
        "No changes in this view. Choose “All changes” to continue.",
      ),
    );
  if (cards.has(current)) focusRow(current, false);
  else if (cards.size) focusRow(cards.keys().next().value, false);
  else focusRow(null, false);
  updateSummary();
}
function view(result) {
  $("#result").hidden = !result;
  list.hidden = result;
  $("#result").textContent = result ? output() : "";
  $("#diff-view").setAttribute("aria-pressed", String(!result));
  $("#result-view").setAttribute("aria-pressed", String(result));
}
function download(name, text, type) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = el("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function downloadReview(format) {
  if (dirty && !saving) await save();
  if (dirty || saving) {
    download(
      format === "json" ? "review.recovery.json" : "reviewed.md",
      format === "json" ? JSON.stringify(exportData(), null, 2) : output(),
      format === "json" ? "application/json" : "text/markdown",
    );
    showError(
      "Exported a browser recovery copy. The latest changes are not yet confirmed on disk.",
    );
    return;
  }
  const link = el("a");
  link.href = format === "json" ? "/export/review.json" : "/export/reviewed.md";
  link.download = format === "json" ? "review.json" : "reviewed.md";
  document.body.append(link);
  link.click();
  link.remove();
}
function exportData() {
  return {
    formatVersion: 1,
    title: session.title,
    fingerprint: session.fingerprint,
    revision: session.revision,
    decisions: clone(decisions),
    rows: session.rows,
    result: output(),
    instructions:
      "Apply the selected decisions and direct replacements. Address each note; notes have not yet been applied to result. Unreviewed changes default to accept.",
  };
}
$("#export").addEventListener("click", () => {
  $("#export-path").textContent = "Saved review: " + session.reviewPath;
  $("#export-dialog").showModal();
  save();
});
$("#download-json").addEventListener("click", () => downloadReview("json"));
$("#download-md").addEventListener("click", () => downloadReview("md"));
$("#diff-view").addEventListener("click", () => view(false));
$("#result-view").addEventListener("click", () => view(true));
$("#filter").addEventListener("change", () => {
  view(false);
  render();
});
$("#context").addEventListener("change", render);
$("#previous").addEventListener("click", () => move(-1));
$("#next").addEventListener("click", () => move(1));
document.addEventListener("keydown", (event) => {
  if (
    !session ||
    event.metaKey ||
    event.ctrlKey ||
    event.altKey ||
    event.target.matches("textarea,input,select") ||
    $("#export-dialog").open ||
    !$("#result").hidden
  )
    return;
  const key = event.key.toLowerCase(),
    row = changed.find((r) => r.id === current);
  if (!["j", "k", "a", "r", "e", "n"].includes(key)) return;
  event.preventDefault();
  if (key === "j") move(1);
  else if (key === "k") move(-1);
  else if (row) {
    if (key === "a" || key === "r")
      setChoice(row, key === "a" ? "accept" : "reject", true);
    else if (key === "e") {
      setChoice(row, "edit");
      cards.get(row.id).querySelector(".replacement").focus();
    } else {
      openedNotes.add(row.id);
      refreshCard(row);
      cards.get(row.id).querySelector(".note").focus();
    }
  }
});
window.addEventListener("beforeunload", (event) => {
  if (dirty) {
    cache();
    event.preventDefault();
    event.returnValue = "";
  }
});
async function init() {
  try {
    const response = await fetch("/api/session");
    if (!response.ok)
      throw new Error(
        "Unable to load the review. Restart the local server and reload.",
      );
    session = await response.json();
    changed = session.rows.filter((r) => r.changed);
    decisions = clone(session.decisions);
    cacheKey = "draft-review:" + session.sessionId;
    let cached = null;
    try {
      cached = JSON.parse(localStorage.getItem(cacheKey) || "null");
    } catch {
      showError(
        "Browser recovery storage is unavailable. Changes will still save to disk while connected.",
      );
    }
    if (cached?.dirty) {
      if (cached.revision === session.revision) {
        decisions = cached.decisions;
        dirty = true;
        serial = cached.serial || 1;
      } else {
        showError(
          "An unsaved browser recovery copy differs from the disk review. It is loaded for export; export it before reloading or resolving the conflict.",
        );
        decisions = cached.decisions;
        conflict = true;
        dirty = true;
      }
    }
    if (session.exportError) showError(session.exportError);
    $("#save-status").textContent = dirty
      ? "Unsaved recovery copy"
      : "✓ Saved to disk";
    $("#output-path").textContent = "Autosaves to " + session.outputPath;
    for (const section of new Set(changed.map((r) => r.section))) {
      const b = button("", () => {
        view(false);
        $("#filter").value = "all";
        render();
        focusRow(changed.find((r) => r.section === section).id);
      });
      b.dataset.section = section;
      b.append(el("div", "", section), el("span"));
      $("#sections").append(b);
    }
    render();
    if (dirty && !conflict) save();
  } catch (error) {
    showError(error.message);
    $("#save-status").textContent = "Could not load";
    $("#export").disabled = true;
  }
}
init();
