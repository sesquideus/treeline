"""
Every static file a template names must exist. With content-hashed names in production
(ManifestStaticFilesStorage) a missing one is no broken image but a server error on every page
that names it: `icon.png`, linked from the base template and never committed, took all of amos
down on 2026-11-21.
"""
import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase

STATIC_TAG = re.compile(r"""\{%\s*static\s+['"]([^'"]+)['"]""")


class StaticReferenceTests(SimpleTestCase):
    def test_every_static_file_named_in_a_template_exists(self):
        named = {}
        for template in Path(settings.BASE_DIR).glob('*/templates/**/*.html'):
            if '.venv' in template.parts:
                continue
            for path in STATIC_TAG.findall(template.read_text()):
                named.setdefault(path, template.relative_to(settings.BASE_DIR))
        self.assertGreater(len(named), 3)
        missing = {path: str(where) for path, where in named.items() if not finders.find(path)}
        self.assertEqual(missing, {})
