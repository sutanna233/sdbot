import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from generate_artists import SDArtistTester, parse_args


class LinuxCompatTests(unittest.TestCase):
    def test_models_command_exists(self):
        args = parse_args(["models"])
        self.assertEqual(args.command, "models")
        self.assertEqual(args.action, "list")

    def test_dispatch_models_command(self):
        tester = SDArtistTester.__new__(SDArtistTester)
        called = {}

        def fake_models(action="list", role=None, model_key=None):
            called.update(action=action, role=role, model_key=model_key)

        tester.cmd_models = fake_models
        tester._dispatch(SimpleNamespace(command="models", action="status", role=None, model_key=None))
        self.assertEqual(called, {"action": "status", "role": None, "model_key": None})

    def test_telegram_start_dispatch_blocks(self):
        tester = SDArtistTester.__new__(SDArtistTester)
        called = {}

        def fake_telegram(action="status", token=None, block=False):
            called.update(action=action, token=token, block=block)

        tester.cmd_telegram = fake_telegram
        tester._dispatch(SimpleNamespace(command="telegram", action="start", token=None))
        self.assertEqual(called, {"action": "start", "token": None, "block": True})

    def test_gallery_linux_opener_does_not_use_startfile(self):
        tester = SDArtistTester.__new__(SDArtistTester)
        with tempfile.TemporaryDirectory() as tmp:
            html = Path(tmp) / "index.html"
            html.write_text("ok", encoding="utf-8")
            with patch("sys.platform", "linux"), patch("webbrowser.open") as mock_open:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    tester._open_gallery_file(html)
            self.assertIn("file://", buf.getvalue())
            mock_open.assert_called_once()

    def test_tags_splits_multi_word_query_fallback(self):
        class Danbooru:
            def __init__(self):
                self.queries = []

            def search_tags(self, keyword):
                self.queries.append(keyword)
                if keyword == "white_hair red_eyes":
                    return []
                if keyword == "white_hair":
                    return [{"name": "white_hair", "type": "general", "count": 100}]
                if keyword == "red_eyes":
                    return [{"name": "red_eyes", "type": "general", "count": 200}]
                return []

        tester = SDArtistTester.__new__(SDArtistTester)
        tester.danbooru = Danbooru()
        buf = io.StringIO()
        with redirect_stdout(buf):
            tester.cmd_tags("white_hair red_eyes")
        out = buf.getvalue()
        self.assertIn("white_hair", out)
        self.assertIn("red_eyes", out)
        self.assertNotIn("No tags found", out)


if __name__ == "__main__":
    unittest.main()
