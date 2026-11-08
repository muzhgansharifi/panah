"""The app is called PANAH everywhere: the former working name is left only in app/main.py, in the two constants that
take over a database from before the rename (OLD_DB_NAME, OLD_DEMO_PASSWORD).

Looks through app/, run.bat, share-online.bat, README.md, HOSTING.md and package/ - whatever of them exists (the
extracted package has no package/ folder) - text and binary files alike, as `grep -r -i` does; "ideas" is not it.
"""
import re
import unittest
from pathlib import Path

from support import APP_DIR  # support adds app/ to sys.path

import main as launcher  # noqa: E402  (app/main.py)

ROOT = APP_DIR.parent
PLACES = ('app', 'run.bat', 'share-online.bat', 'README.md', 'HOSTING.md', 'package')
OLD = re.compile(r'(?<!i)' + re.escape(Path(launcher.OLD_DB_NAME).stem), re.IGNORECASE)
ALLOWED = re.compile(r'^OLD_(?:DB_NAME|DEMO_PASSWORD) = ')  # the two constants, in app/main.py only


def files():
    for name in PLACES:
        top = ROOT / name
        if top.is_file():
            yield top
        elif top.is_dir():
            for path in sorted(top.rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts:
                    yield path


class OldNameTest(unittest.TestCase):
    def test_old_name_only_in_takeover(self):
        found, main = [], APP_DIR / 'main.py'
        for path in files():
            text = path.read_bytes().decode('latin-1')  # every byte is one character: binary files too
            for number, line in enumerate(text.splitlines(), 1):
                if OLD.search(line) and not (path == main and ALLOWED.match(line)):
                    found.append('%s:%d' % (path.relative_to(ROOT).as_posix(), number))
        self.assertEqual(found, [], 'the former name is still here')

    def test_takeover_constants_are_there(self):
        """The search above is only as good as its pattern: the constants it starts from are the old name."""
        text = (APP_DIR / 'main.py').read_text(encoding='utf-8')
        self.assertEqual(len([line for line in text.splitlines() if ALLOWED.match(line)]), 2)
        self.assertTrue(OLD.search(launcher.OLD_DEMO_PASSWORD))
        self.assertIsNone(OLD.search('Ideas and IDEAS'))


if __name__ == '__main__':
    unittest.main()
