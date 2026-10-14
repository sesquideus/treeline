"""
Units written twice — once by the template, once by the stylesheet.

`main.css` ends with a short block of `::after` rules: an element classed `altitude` gets
" m" appended, `distance` gets " km", `slope` " m/km", `angle` "°". The numbers are
therefore written bare, and a template that helpfully adds the unit itself produces
`+400.4 m m` — in the browser only. The page source looks right, every test that reads the
response body passes, and nothing anywhere raises.

That is exactly how it got into `summit/blocks/peak-col.html` and the prose on the
comparison page: both were written by reading the rendered HTML, which is the one place the
duplication cannot be seen. The sweep below reads the stylesheet for the rules rather than
hard-coding them, so a new unit rule is covered the moment it is added.
"""

import re

from django.test import SimpleTestCase

from mountains.test_template_comments import templates

STYLESHEET = 'static/css/main.css'

#: `.altitude::after { content: " m"; }` — the class, and what CSS already writes after it.
UNIT_RULE = re.compile(r'\.(\w[\w-]*)::after\s*\{\s*content:\s*"([^"]+)"')

#: The opening tag of an element carrying a class, and its text up to the first closing tag.
#: Good enough because every cell this applies to holds a number and nothing else; an
#: element with children simply stops matching and is not inspected.
def element(css_class):
    return re.compile(r'class="[^"]*\b%s\b[^"]*"[^>]*>((?:(?!</)[\s\S])*)</\w+>([\s\S]{0,16})'
                      % re.escape(css_class))


def css_units(path=STYLESHEET):
    """Every class whose unit the stylesheet writes, and the text it writes."""
    with open(path, encoding='utf-8') as handle:
        return dict(UNIT_RULE.findall(handle.read()))


def doubled_units(source, units):
    """
    The classes this markup writes a unit for that the stylesheet will write again.

    Both shapes are caught: the unit inside the element, which is how a table cell does it,
    and the unit just after the closing tag, which is how a sentence does it.

    Inside, the unit is looked for after a value — a `}}` or a digit — rather than at the
    end of the cell, because the one that got through was wrapped in an `{% if %}` and so
    had `{% endif %}` after it. It is the unit following the number that is the mistake,
    wherever in the cell that number sits.
    """
    offences = []
    for css_class, unit in units.items():
        token = re.escape(unit.strip())
        for inside, after in element(css_class).findall(source):
            if (re.search(r'(\}\}|\d)\s*%s(?![\w/])' % token, inside)
                    or re.match(r'\s*%s(?![\w/])' % token, after)):
                offences.append(css_class)
    return sorted(set(offences))


class UnitTests(SimpleTestCase):
    def setUp(self):
        self.units = css_units()

    def test_the_stylesheet_still_writes_units(self):
        """Without this the sweep below would pass by having nothing to look for."""
        self.assertEqual(self.units.get('altitude'), ' m')
        self.assertEqual(self.units.get('distance'), ' km')

    def test_no_template_writes_a_unit_the_stylesheet_already_writes(self):
        doubled = {str(path): classes for path in templates()
                   if (classes := doubled_units(path.read_text(encoding='utf-8'), self.units))}
        self.assertEqual(doubled, {},
                         'these would render as "400 m m"; the ::after rules in '
                         f'{STYLESHEET} already supply the unit')

    def test_the_check_finds_a_unit_inside_the_element(self):
        self.assertEqual(
            doubled_units('<td class="altitude">{{ x|diff_altitude }} m</td>', self.units),
            ['altitude'])

    def test_the_check_finds_a_unit_the_rest_of_the_cell_hides(self):
        """
        The shape `peak-col.html` actually had: the unit in the middle of the cell, with
        `{% endif %}` after it. An end-anchored check passes this and sees nothing.
        """
        self.assertEqual(doubled_units(
            '<td class="altitude">{% if c %}{{ x|diff_altitude }} m{% else %}?{% endif %}</td>',
            self.units), ['altitude'])

    def test_the_check_finds_a_unit_after_the_element(self):
        """And the shape the comparison page's prose had."""
        self.assertEqual(
            doubled_units('<span class="altitude">{{ x }}</span> m).', self.units),
            ['altitude'])

    def test_a_bare_number_is_fine(self):
        self.assertEqual(doubled_units('<td class="distance">{{ x }}</td>', self.units), [])

    def test_a_word_merely_starting_with_the_unit_is_not_one(self):
        """`m` is a unit; `metres above the col` is prose."""
        self.assertEqual(
            doubled_units('<span class="altitude">{{ x }}</span> metres above', self.units),
            [])

    def test_an_unclassed_number_may_say_whatever_it_likes(self):
        """Only the classed elements get a unit from CSS, so only they can double one."""
        self.assertEqual(doubled_units('({{ mountain.point.altitude }} m)', self.units), [])
