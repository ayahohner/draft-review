import json
import random
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from unittest.mock import patch

from server import Review, handler_for, highlight, make_rows, result_text


class ReviewTests(unittest.TestCase):
    def test_accept_and_reject_reconstruct_sources_exactly(self):
        pairs = [
            ("", ""),
            ("a\n", ""),
            ("", "b\n"),
            ("a", "a\n"),
            ("a\r\nb\r\n", "a\r\nc\r\n"),
            ("old\n\nend", "new\nextra\n\nend\n"),
            ("# A\nhello\n# B\nworld\n", "# B\nworld\n# A\nhello\n"),
        ]
        rng = random.Random(7)
        for _ in range(100):
            pairs.append(
                tuple(
                    "".join(
                        rng.choice(["a\n", "\n", "b\n", "line <>&\n"])
                        for _ in range(rng.randrange(20))
                    )
                    for _ in range(2)
                )
            )
        for before, after in pairs:
            rows = make_rows(before, after)
            self.assertEqual(result_text(rows, {}), after)
            self.assertEqual(
                result_text(
                    rows, {r["id"]: {"choice": "reject"} for r in rows if r["changed"]}
                ),
                before,
            )

    def test_example_round_trip(self):
        path = Path(__file__).parent / "examples"
        before, after = [
            (path / f"{name}.md").read_text() for name in ["before", "after"]
        ]
        rows = make_rows(before, after)
        self.assertEqual(result_text(rows, {}), after)
        self.assertEqual(
            result_text(
                rows, {r["id"]: {"choice": "reject"} for r in rows if r["changed"]}
            ),
            before,
        )

    def test_edits_notes_and_restart_preserve_state(self):
        with tempfile.TemporaryDirectory() as directory:
            review = Review("old\n\ntail\n", "new\n\ntail\n", Path(directory))
            decisions = {
                "0": {
                    "choice": "edit",
                    "text": "my version\n",
                    "note": "Keep the metaphor.",
                    "reviewed": True,
                }
            }
            review.save(0, decisions)
            self.assertEqual(
                (Path(directory) / "reviewed.md").read_text(), "my version\n\ntail\n"
            )
            self.assertIn(
                "Keep the metaphor.", (Path(directory) / "feedback.md").read_text()
            )
            restarted = Review("old\n\ntail\n", "new\n\ntail\n", Path(directory))
            self.assertEqual(restarted.public()["decisions"], decisions)
            self.assertEqual(restarted.public()["revision"], 1)
            self.assertEqual(
                json.loads(restarted.path.read_text())["result"], "my version\n\ntail\n"
            )
            with self.assertRaises(RuntimeError):
                restarted.save(0, {})
            with self.assertRaises(ValueError):
                restarted.save(1, {"999": {"choice": "accept"}})
            with self.assertRaises(ValueError):
                Review("different", "new", Path(directory))
            self.assertEqual(
                json.loads(restarted.path.read_text())["decisions"], decisions
            )

    def test_highlighting_escapes_html(self):
        old, new = highlight(
            "<script>alert(1)</script>", "<img src=x onerror=alert(1)>"
        )
        self.assertNotIn("<script>", old)
        self.assertNotIn("<img", new)
        self.assertIn("&lt;", new)

    def test_inserted_blank_line_does_not_shift_paragraph_pairing(self):
        rows = make_rows(
            "Introduction here.\n- Discernment helps you choose.\n- Capacity helps you stay.\n",
            "Introduction revised.\n\n- Discernment helps you choose wisely.\n- Capacity helps you stay present.\n",
        )
        self.assertTrue(any(r["before"] is None and r["after"] == "\n" for r in rows))
        for row in rows:
            if row["before"] and row["before"].startswith("- Discernment"):
                self.assertTrue(row["after"].startswith("- Discernment"))

    def test_export_failure_keeps_committed_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            review = Review("old\n", "new\n", Path(directory))
            with patch.object(
                review, "write_exports", side_effect=OSError("disk full")
            ):
                review.save(0, {"0": {"choice": "reject"}})
            self.assertEqual(review.state["revision"], 1)
            self.assertEqual(json.loads(review.path.read_text())["revision"], 1)
            self.assertIsNotNone(review.export_error)
            restarted = Review("old\n", "new\n", Path(directory))
            self.assertEqual((Path(directory) / "reviewed.md").read_text(), "old\n")
            restarted.save(1, {})
            self.assertEqual(restarted.state["revision"], 2)
            self.assertIsNone(restarted.export_error)

    def test_failed_canonical_write_preserves_saved_state(self):
        with tempfile.TemporaryDirectory() as directory:
            review = Review("old\n", "new\n", Path(directory))
            original = review.path.read_bytes()
            with patch("server.os.replace", side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    review.save(0, {"0": {"choice": "reject"}})
            self.assertEqual(review.state["revision"], 0)
            self.assertEqual(review.path.read_bytes(), original)
            self.assertEqual(
                set(p.name for p in Path(directory).iterdir()),
                {"review.json", "reviewed.md", "feedback.md"},
            )

    def test_sessions_have_distinct_stable_recovery_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Review("old", "new", Path(directory) / "one")
            second = Review("old", "new", Path(directory) / "two")
            restarted = Review("old", "new", Path(directory) / "one")
            self.assertNotEqual(first.state["sessionId"], second.state["sessionId"])
            self.assertEqual(first.state["sessionId"], restarted.state["sessionId"])

    def test_invalid_saved_state_is_not_overwritten(self):
        for invalid in (
            {},
            [],
            {"formatVersion": 9, "fingerprint": "x", "revision": 0, "decisions": {}},
        ):
            with (
                self.subTest(invalid=invalid),
                tempfile.TemporaryDirectory() as directory,
            ):
                path = Path(directory) / "review.json"
                original = json.dumps(invalid)
                path.write_text(original)
                with self.assertRaises(ValueError):
                    Review("old", "new", Path(directory))
                self.assertEqual(path.read_text(), original)

    def test_invalid_decisions_and_revisions_do_not_modify_state(self):
        with tempfile.TemporaryDirectory() as directory:
            review = Review("old", "new", Path(directory))
            for revision in (True, 0.0, "0", -1, None):
                with self.subTest(revision=revision), self.assertRaises(ValueError):
                    review.save(revision, {})
            for decisions in (
                [],
                {"0": {"choice": "edit"}},
                {"0": {"choice": "accept", "note": None}},
                {"0": {"choice": "other"}},
                {"0": {"choice": "accept", "reviewed": 1}},
            ):
                with self.subTest(decisions=decisions), self.assertRaises(ValueError):
                    review.save(0, decisions)
            self.assertEqual(review.state["revision"], 0)

    def test_cli_protects_source_files_from_output_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "reviewed.md"
            source.write_text("original text")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parent / "server.py"),
                    "--before",
                    str(source),
                    "--after",
                    str(source),
                    "--session",
                    directory,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("Source files must not", completed.stderr)
            self.assertEqual(source.read_text(), "original text")

    def test_http_save_auth_and_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            review = Review("old\n", "new\n", Path(directory))
            server = HTTPServer(("127.0.0.1", 0), lambda *args: None)
            port = server.server_port
            server.RequestHandlerClass = handler_for(review, port)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}"
            try:
                with urllib.request.urlopen(base + "/api/session") as response:
                    data = json.load(response)
                for invalid in ([], None, "text", {"revision": True, "decisions": {}}):
                    request = urllib.request.Request(
                        base + "/api/save",
                        data=json.dumps(invalid).encode(),
                        headers={
                            "X-Review-Token": data["token"],
                            "Content-Type": "application/json",
                        },
                    )
                    with (
                        self.subTest(body=invalid),
                        self.assertRaises(urllib.error.HTTPError) as error,
                    ):
                        urllib.request.urlopen(request)
                    self.assertEqual(error.exception.code, 400)
                    error.exception.close()
                hostile_origin = urllib.request.Request(
                    base + "/api/save",
                    data=b"{}",
                    headers={
                        "X-Review-Token": data["token"],
                        "Origin": "https://example.com",
                    },
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(hostile_origin)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
                payload = json.dumps(
                    {"revision": 0, "decisions": {"0": {"choice": "reject"}}}
                ).encode()
                req = urllib.request.Request(
                    base + "/api/save",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(req)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
                req.add_header("X-Review-Token", data["token"])
                with urllib.request.urlopen(req) as response:
                    self.assertEqual(json.load(response)["revision"], 1)
                self.assertEqual((Path(directory) / "reviewed.md").read_text(), "old\n")
                with urllib.request.urlopen(base + "/export/review.json") as response:
                    self.assertIn(
                        "attachment;", response.headers["Content-Disposition"]
                    )
                    self.assertEqual(json.load(response)["result"], "old\n")
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(req)
                self.assertEqual(error.exception.code, 409)
                error.exception.close()
                hostile = urllib.request.Request(
                    base + "/api/session", headers={"Host": "hostile.example"}
                )
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(hostile)
                self.assertEqual(error.exception.code, 403)
                error.exception.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == "__main__":
    unittest.main()
