#!/usr/bin/env python3
"""Local, dependency-free prose diff reviewer. No source files are modified."""

import argparse
import difflib
import hashlib
import html
import json
import os
import re
import secrets
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from itertools import zip_longest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def atomic_write(path, text):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def highlight(before, after):
    a, b = [re.findall(r"\s+|\w+|[^\w\s]", text or "") for text in (before, after)]
    left, right = [], []
    for tag, i, j, k, after_end in difflib.SequenceMatcher(
        None, a, b, autojunk=False
    ).get_opcodes():
        x, y = html.escape("".join(a[i:j])), html.escape("".join(b[k:after_end]))
        left.append(x if tag == "equal" else "<mark>" + x + "</mark>")
        right.append(y if tag == "equal" else "<mark>" + y + "</mark>")
    return "".join(left), "".join(right)


def make_rows(before, after):
    a, b = before.splitlines(keepends=True), after.splitlines(keepends=True)
    rows = []
    section = "Title & introduction"
    for tag, i, j, k, after_end in difflib.SequenceMatcher(
        None, a, b, autojunk=False
    ).get_opcodes():
        pairs = (
            align_lines(a, b, i, j, k, after_end)
            if tag == "replace"
            else zip_longest(range(i, j), range(k, after_end))
        )
        for ai, bi in pairs:
            old = a[ai] if ai is not None else None
            new = b[bi] if bi is not None else None
            heading = old if old and old.startswith("## ") else new
            if heading and heading.startswith("## "):
                section = heading.strip()[3:]
            old_html, new_html = highlight(old, new)
            rows.append(
                dict(
                    id=str(len(rows)),
                    before=old,
                    after=new,
                    oldLine=ai + 1 if ai is not None else None,
                    newLine=bi + 1 if bi is not None else None,
                    changed=tag != "equal",
                    section=section,
                    beforeHtml=old_html,
                    afterHtml=new_html,
                )
            )
    return rows


def align_lines(a, b, i, j, k, after_end):
    """Align related lines without pairing inserted spacing with a prose paragraph."""
    n, m = j - i, after_end - k
    score = [[0.0] * (m + 1) for _ in range(n + 1)]
    route = {}
    for x in range(1, n + 1):
        score[x][0] = -0.45 * x
        route[x, 0] = "delete"
    for y in range(1, m + 1):
        score[0][y] = -0.45 * y
        route[0, y] = "insert"
    for x in range(1, n + 1):
        for y in range(1, m + 1):
            old, new = a[i + x - 1], b[k + y - 1]
            similarity = difflib.SequenceMatcher(None, old, new, autojunk=False).ratio()
            match = (
                2 * similarity - 0.5 if bool(old.strip()) == bool(new.strip()) else -2
            )
            options = [
                (score[x - 1][y - 1] + match, "pair"),
                (score[x - 1][y] - 0.45, "delete"),
                (score[x][y - 1] - 0.45, "insert"),
            ]
            score[x][y], route[x, y] = max(options, key=lambda item: item[0])
    pairs = []
    x, y = n, m
    while x or y:
        step = route[x, y]
        pairs.append(
            (
                i + x - 1 if step != "insert" else None,
                k + y - 1 if step != "delete" else None,
            )
        )
        x -= step != "insert"
        y -= step != "delete"
    return reversed(pairs)


def result_text(rows, decisions):
    result = []
    for row in rows:
        decision = decisions.get(row["id"], {})
        choice = decision.get("choice", "accept")
        if choice == "edit":
            text = decision["text"]
        elif choice == "reject":
            text = row["before"]
        else:
            text = row["after"]
        result.append(text or "")
    return "".join(result)


class Review:
    def __init__(self, before, after, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.rows = make_rows(before, after)
        self.fingerprint = hashlib.sha256((before + "\0" + after).encode()).hexdigest()
        self.token = secrets.token_urlsafe(32)
        self.path = directory / "review.json"
        self.state = dict(
            formatVersion=1,
            fingerprint=self.fingerprint,
            revision=0,
            title="Draft review",
            decisions={},
            sessionId=secrets.token_hex(16),
        )
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("Saved review must be an object.")
            required = {"formatVersion", "fingerprint", "revision", "decisions"}
            if not required <= loaded.keys():
                raise ValueError("Saved review is missing required fields.")
            self.state.update({key: loaded[key] for key in self.state if key in loaded})
            if self.state["formatVersion"] != 1:
                raise ValueError("Unsupported review format version.")
            if type(self.state["revision"]) is not int or self.state["revision"] < 0:
                raise ValueError("Invalid saved revision.")
            if self.state["fingerprint"] != self.fingerprint:
                raise ValueError(
                    "Source snapshots changed. Use a new --session directory to preserve the existing review."
                )
            self.validate(self.state["decisions"])
        self.export_error = None
        self.persist()

    def public(self):
        return dict(
            **self.state,
            rows=self.rows,
            token=self.token,
            outputPath=str(self.directory / "reviewed.md"),
            reviewPath=str(self.path),
            exportError=self.export_error,
        )

    def validate(self, decisions):
        if not isinstance(decisions, dict):
            raise ValueError("Decisions must be an object.")
        changed = {r["id"] for r in self.rows if r["changed"]}
        for key, value in decisions.items():
            if key not in changed or not isinstance(value, dict):
                raise ValueError("Unknown change.")
            if value.get("choice") not in ("accept", "reject", "edit"):
                raise ValueError("Unknown decision.")
            if not isinstance(value.get("text", ""), str) or not isinstance(
                value.get("note", ""), str
            ):
                raise ValueError("Text and notes must be strings.")
            if value["choice"] == "edit" and "text" not in value:
                raise ValueError("An edit requires replacement text.")
            if not isinstance(value.get("reviewed", False), bool):
                raise ValueError("Invalid reviewed flag.")
        return decisions

    def save(self, revision, decisions):
        if type(revision) is not int or revision < 0:
            raise ValueError("Revision must be a non-negative integer.")
        if revision != self.state["revision"]:
            raise RuntimeError(
                "This review changed in another tab. Export your recovery copy, then reload."
            )
        self.validate(decisions)
        previous = self.state
        self.state = dict(previous, revision=revision + 1, decisions=decisions)
        try:
            self.persist()
        except OSError:
            self.state = previous
            raise

    def persist(self):
        # The JSON is authoritative; derived exports are rebuilt on restart.
        data = dict(
            self.state,
            rows=self.rows,
            result=result_text(self.rows, self.state["decisions"]),
        )
        atomic_write(self.path, json.dumps(data, ensure_ascii=False, indent=2))
        # Canonical JSON is committed. Export failures must not roll back its revision.
        self.export_error = None
        try:
            self.write_exports(data)
        except OSError:
            self.export_error = "Review saved, but Markdown exports could not be refreshed. Fix directory permissions and restart to rebuild them."

    def write_exports(self, data):
        atomic_write(self.directory / "reviewed.md", data["result"])
        feedback = [
            "# Revision review\n",
            "Apply the decisions below and address the notes. The accompanying reviewed.md includes accepted changes and direct edits; notes still require attention.\n",
        ]
        for row in self.rows:
            if not row["changed"]:
                continue
            d = self.state["decisions"].get(row["id"], {"choice": "accept"})
            feedback.extend(
                [
                    f"\n## Change {row['id']} · original line {row['oldLine']} · {d['choice']}\n",
                    "\nOriginal:\n\n" + (row["before"] or "(no line)\n"),
                    "\nProposed:\n\n" + (row["after"] or "(no line)\n"),
                ]
            )
            if d["choice"] == "edit":
                feedback.append("\nReplacement:\n\n" + d["text"] + "\n")
            if d.get("note"):
                feedback.append("\nInstruction: " + d["note"] + "\n")
        atomic_write(self.directory / "feedback.md", "\n".join(feedback))


def handler_for(review, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed(self):
            return self.headers.get("Host") in (
                f"127.0.0.1:{port}",
                f"localhost:{port}",
            )

        def reply(self, status, data, content_type="application/json", filename=None):
            if not isinstance(data, bytes):
                data = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{filename}"'
                )
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.allowed():
                return self.reply(403, {"error": "Local access only."})
            path = self.path.split("?")[0]
            if path == "/api/session":
                return self.reply(200, review.public())
            exports = {
                "/export/review.json": (
                    "review.json",
                    "application/json",
                    "review.json",
                ),
                "/export/reviewed.md": ("reviewed.md", "text/markdown", "reviewed.md"),
            }
            if path in exports:
                name, mime, filename = exports[path]
                if name == "review.json":
                    data = dict(
                        review.state,
                        rows=review.rows,
                        result=result_text(review.rows, review.state["decisions"]),
                    )
                    content = json.dumps(data, ensure_ascii=False, indent=2).encode(
                        "utf-8"
                    )
                else:
                    content = result_text(
                        review.rows, review.state["decisions"]
                    ).encode("utf-8")
                return self.reply(200, content, mime, filename)
            static = {
                "/": ("index.html", "text/html"),
                "/app.js": ("app.js", "text/javascript"),
                "/style.css": ("style.css", "text/css"),
            }
            if path in static:
                name, mime = static[path]
                return self.reply(200, (ROOT / name).read_bytes(), mime)
            return self.reply(404, {"error": "Not found."})

        def do_POST(self):
            if not self.allowed() or self.headers.get("X-Review-Token") != review.token:
                return self.reply(403, {"error": "Local access token required."})
            origin = self.headers.get("Origin")
            if origin and origin not in (
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
            ):
                return self.reply(403, {"error": "Origin not allowed."})
            if self.path != "/api/save":
                return self.reply(404, {"error": "Not found."})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4_000_000:
                    raise ValueError("Invalid request size.")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("Request body must be an object.")
                review.save(body["revision"], body["decisions"])
                return self.reply(
                    200,
                    {
                        "revision": review.state["revision"],
                        "exportError": review.export_error,
                    },
                )
            except RuntimeError as error:
                return self.reply(409, {"error": str(error)})
            except (ValueError, KeyError, TypeError) as error:
                return self.reply(400, {"error": str(error)})
            except OSError:
                return self.reply(
                    500,
                    {
                        "error": "Could not save to disk. Keep this tab open and export a recovery copy."
                    },
                )

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", type=Path, default=ROOT / "examples/before.md")
    parser.add_argument("--after", type=Path, default=ROOT / "examples/after.md")
    parser.add_argument("--session", type=Path, default=ROOT / "session/demo")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error(
            "--port must be between 0 and 65535 (0 selects an available port)."
        )
    directory = args.session.resolve()
    outputs = {
        directory / name for name in ("review.json", "reviewed.md", "feedback.md")
    }
    if any(
        source.resolve() in {path.resolve() for path in outputs}
        for source in (args.before, args.after)
    ):
        parser.error("Source files must not be session output files.")
    try:
        review = Review(
            args.before.read_bytes().decode("utf-8"),
            args.after.read_bytes().decode("utf-8"),
            directory,
        )
        with HTTPServer(
            ("127.0.0.1", args.port), handler_for(review, args.port)
        ) as server:
            server.RequestHandlerClass = handler_for(review, server.server_port)
            print(f"Review ready at http://127.0.0.1:{server.server_port}", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
