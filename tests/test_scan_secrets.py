"""The secret scanner must catch each pattern family and let clean/marked lines through."""

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "scan_secrets", Path(__file__).resolve().parent.parent / "scripts" / "scan_secrets.py"
)
scan_secrets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan_secrets)


class ScanTests(unittest.TestCase):
    def _scan(self, text: str) -> list[str]:
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "f.txt")
            Path(p).write_text(text)
            return scan_secrets.scan(p)

    def test_flags_each_family(self):
        samples = {
            "api-key": "k = 'sk-" + "a1B2c3D4e5F6g7H8i9J0" + "'",
            "bearer": "Authorization: Bearer " + "abcdefghij0123456789xyz",
            "assignment": "api_key = '" + "abcdefghijklmnop" + "'",
            "private": "-----BEGIN " + "RSA PRIVATE KEY-----",
            "home": "/home/" + "someone/.hermes/state.db",
            "email": "me@" + "corp.io",
            "phone": "+" + "491701234567",
            "chat": "chat_id: " + "12345678",
            "prefixed key": 'X_KEY="' + "abcdefghijklmnop1234" + '"',
            "unquoted token": "TOKEN: " + "abcdefghijkl1234",
            "root path": "/root/" + "notes.txt",  # scan: allow
            "mixed-case home": "/home/" + "Bob/x",
            "spaced phone": "+49 170 " + "1234567",
            "tailnet": "box." + "tail1234.ts.net",  # scan: allow
            "private ip": "10.0." + "3.7",
            "short bearer": "Bearer " + "abcdefgh1234",
        }
        for name, line in samples.items():
            with self.subTest(name):
                self.assertTrue(self._scan(line), f"{name} not flagged")

    def test_pathological_long_lines_finish_quickly(self):
        import time

        t = time.time()
        self._scan("a." * 30000 + "\n" + "10." * 30000 + "\n" + "x" * 60000)
        self.assertLess(time.time() - t, 3)

    def test_clean_and_allowed_lines_pass(self):
        self.assertEqual(self._scan("x = 1\npath = '/home/<user>/.hermes'\nuser@example.com\n"), [])
        self.assertEqual(self._scan("/home/" + "someone/x  # scan: allow"), [])
        self.assertEqual(self._scan('version: 0.1.7\nkey = value\nrequires_hermes: ">=0.21.7"\n'), [])

    def test_repo_itself_is_clean(self):
        files = scan_secrets.tracked_files()
        self.assertTrue(files)
        self.assertEqual([h for f in files for h in scan_secrets.scan(f)], [])

    def test_binary_non_image_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "build.whl")
            Path(p).write_bytes(bytes(range(256)))
            hits = scan_secrets.scan(p)
            self.assertEqual(len(hits), 1)
            self.assertIn("binary (non-UTF-8) file is tracked", hits[0])

    def test_binary_image_file_is_allowed(self):
        with tempfile.TemporaryDirectory() as d:
            for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".PNG"):
                p = os.path.join(d, f"banner{ext}")
                Path(p).write_bytes(bytes(range(256)))
                self.assertEqual(scan_secrets.scan(p), [], f"{ext} should be allowed")


if __name__ == "__main__":
    unittest.main()
