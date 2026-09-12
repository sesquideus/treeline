"""
The management-command checks, run as tests.

`manage.py check` and `makemigrations --check` are the two things that go stale without
anyone noticing — the first when a field or admin declaration stops validating, the second
when a model is edited and the migration is left for later. Both are cheap, and neither is
part of any other test here.
"""

from io import StringIO

from django.core.management import call_command
from django.core.management.base import SystemCheckError
from django.test import TestCase


class SystemCheckTests(TestCase):
    def test_the_system_check_framework_reports_no_issues(self):
        try:
            call_command('check', stdout=StringIO(), stderr=StringIO())
        except SystemCheckError as error:
            self.fail(f'manage.py check reported issues:\n{error}')

    def test_every_model_change_has_a_migration(self):
        """`makemigrations --check` exits non-zero while a model edit is unmigrated."""
        try:
            call_command('makemigrations', '--check', '--dry-run',
                         stdout=StringIO(), stderr=StringIO())
        except SystemExit:
            self.fail('there are model changes with no migration; run makemigrations')
