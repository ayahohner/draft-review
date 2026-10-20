# Draft review

Review changes between two Markdown or plain-text drafts in your browser. Accept or reject individual changes, write replacements, and leave notes for the next revision. Everything runs locally, with no account, API key, or runtime dependencies beyond Python and a browser.

## Quick start

Requires Python 3.10 or newer and a current browser.

```sh
git clone https://github.com/ayahohner/draft-review.git
cd draft-review
python3 server.py
```

Open <http://127.0.0.1:8766>. The default comparison uses the fictional drafts in `examples/` and saves your work to `session/demo/`. Press Ctrl+C in the terminal to stop the server.

To review your own UTF-8 files:

```sh
python3 server.py --before original.md --after proposed.md --session session/my-review
```

Use a separate session directory for each pair of drafts. Restart with the same arguments to resume. The source files are never modified; changing either source requires a new session directory. Add `--port 8767` if the default port is busy, or `--port 0` to choose an available port and print its address.

## Reviewing changes

Each changed physical line has four controls. Paragraphs stored as single Markdown lines wrap for reading; this is a line-based comparison, not a rendered Markdown editor.

- **Accept** keeps the proposed line and is the default.
- **Reject** restores the original line.
- **Edit** replaces the line with your text. Clear the field to remove the line.
- **Note** records an instruction for a later revision without changing the output.

Word-level highlighting shows changes within paired lines. Insertions, deletions, blank lines, and line endings are preserved when accepting or rejecting. Editing retains the source line's final LF or CRLF; browser textareas normalize internal line endings in multiline edits.

Use the filter to show pending, noted, rejected, or edited changes. Enable **Unchanged text** for context, or select **Read result** to preview the assembled text. The reviewed counter tracks explicit decisions; unreviewed changes still default to accept.

| Key   | Action                      |
| ----- | --------------------------- |
| J / K | Next / previous change      |
| A / R | Accept / reject and advance |
| E     | Edit the selected change    |
| N     | Add a note                  |

Shortcuts pause while typing and while the export dialog or result view is open.

## Saved files and recovery

Changes autosave after a short pause. Each session contains:

| File          | Contents                                                                                    |
| ------------- | ------------------------------------------------------------------------------------------- |
| `review.json` | Authoritative review: source lines, decisions, notes, revision number, and assembled result |
| `reviewed.md` | Assembled text, including direct edits                                                      |
| `feedback.md` | Readable decisions and notes for an editor or writing assistant                             |

Notes are never applied automatically. **Export review** downloads the JSON handoff or assembled Markdown. These files contain draft text; review them before sharing.

The canonical JSON is replaced atomically. Markdown exports are regenerated on restart; if an export fails, a visible warning distinguishes that failure from a failed review save. Browser recovery storage retains unsaved decisions for the same session and browser origin. Use the same port when returning to an unsaved session.

Use one active editing tab. Revision checks reject stale saves from another tab. On a conflict, export your browser recovery copy before reloading. Recovery JSON can be inspected or handed to an editor; there is no automatic import or conflict merge.

## Privacy and scope

The server listens on `127.0.0.1`, checks the HTTP Host header, and requires a session token for writes. It serves only the application assets and current review endpoints. It has no telemetry, external fonts, external scripts, or remote storage.

Drafts and decisions remain in plaintext on disk and in browser recovery storage. This is a local, single-user tool, not an authenticated service for shared hosting. Do not expose it through a public proxy or tunnel.

`session/`, `private/`, common export filenames, and local configuration files are ignored by Git. Keep private inputs in those directories: arbitrary Markdown files elsewhere are **not** automatically ignored. Removing a session directory does not clear its browser recovery data; clear site data for the local address to remove browser copies.

The comparison algorithm is intended for ordinary prose drafts. Very large files or large replacement blocks can take substantial time and memory. Save requests are limited to 4 MB.

## Development

The server and diff logic live in `server.py`; the interface is plain JavaScript, HTML, and CSS. No frontend build is required.

```sh
python3 -m unittest -v
node --check app.js
```

Tests use synthetic data and the public examples. They cover reconstruction, escaping, persistence, failed writes, request validation, and local HTTP access controls.

Optional formatting and lint checks (development tools only):

```sh
uvx ruff@0.14.1 check .
uvx ruff@0.14.1 format --check .
npx --yes prettier@3.6.2 --check app.js index.html style.css README.md .github/workflows/checks.yml
```

Run the same formatters without `--check` (and use `--write` for Prettier) when editing. Contributions should include regression tests for behavioral changes and must not include real drafts, session exports, credentials, or personal data.
