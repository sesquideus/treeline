"""
Where a parent came from (migration 0039).

Isolation is one-way: a parent must cite a source, but a source may stand alone, because the
nearest higher point can be measured and cited with no summit yet attributed to it — 663 of
them were, when the constraint went in. Prominence is both ways: a parent cites a source,
and a source cites what it measured against, which is the parent.
"""

from django.db import IntegrityError, transaction
from django.test import TestCase

from mountains.models import Summit
from mountains.test_factories import fixture_source, make_summit


class SourceConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.parent = make_summit('Parent', 2000, 49.0, 20.0)
        cls.child = make_summit('Child', 1500, 49.1, 20.1)
        cls.source = fixture_source()

    def set(self, **fields):
        with transaction.atomic():
            Summit.objects.filter(pk=self.child.pk).update(**fields)

    def assertRefused(self, name, **fields):
        with self.assertRaisesMessage(IntegrityError, name):
            self.set(**fields)

    def test_an_isolation_parent_needs_a_source(self):
        self.assertRefused('isolation_parent_requires_source', isolation_parent=self.parent)

    def test_an_isolation_source_needs_no_parent(self):
        self.set(isolation_source=self.source)

    def test_an_isolation_parent_with_a_source_is_fine(self):
        self.set(isolation_parent=self.parent, isolation_source=self.source)

    def test_a_prominence_parent_needs_a_source(self):
        self.assertRefused('prominence_source_requires_parent', prominence_parent=self.parent)

    def test_a_prominence_source_needs_a_parent(self):
        self.assertRefused('prominence_source_requires_parent', prominence_source=self.source)

    def test_a_prominence_parent_with_a_source_is_fine(self):
        self.set(prominence_parent=self.parent, prominence_source=self.source)

    def test_neither_is_fine(self):
        self.set(prominence_parent=None, prominence_source=None,
                 isolation_parent=None, isolation_source=None)
