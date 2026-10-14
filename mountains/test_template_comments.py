"""
Template comments that are not comments.

Django's `{# ... #}` is single-line only — `django.template.base.tag_re` is compiled without
`re.DOTALL`, so a comment spanning two lines never matches, is never stripped, and is served
to the reader as literal text. Nothing warns: the page still renders, the markup is still
valid, and inside an SVG or a table cell the leak is invisible in the browser while sitting
in the page source of every request.

This has now happened three times in this codebase, twice in one week, which is what a
mistake that cannot be seen looks like. `{% comment %}` has no such limit, and the sweep
below is cheap.
"""

from pathlib import Path

from django.test import SimpleTestCase

#: Every template in the project, wherever an app keeps them.
ROOTS = ('core/templates', 'mountains/templates', 'users/templates')


def templates():
    for root in ROOTS:
        yield from sorted(Path(root).rglob('*.html'))


def unclosed_comments(path):
    """The 1-based line numbers where a `{#` opens without its `#}` closing on the line."""
    with open(path, encoding='utf-8') as handle:
        return [number for number, line in enumerate(handle, 1)
                if '{#' in line and '#}' not in line]


class CommentTests(SimpleTestCase):
    def test_no_comment_spans_more_than_one_line(self):
        leaking = {str(path): lines for path in templates()
                   if (lines := unclosed_comments(path))}
        self.assertEqual(leaking, {},
                         'multi-line {# #} is rendered as text; use {% comment %}')

    def test_there_are_templates_to_check(self):
        """So the sweep above cannot pass by finding nothing at all."""
        self.assertGreater(len(list(templates())), 50)

    def test_the_check_finds_a_leak_when_there_is_one(self, ):
        """The detector itself, on a file written to leak."""
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.html', delete=False) as handle:
            handle.write('<p>fine</p>\n{# one\n   two #}\n{# single line #}\n')
        self.assertEqual(unclosed_comments(handle.name), [2])
