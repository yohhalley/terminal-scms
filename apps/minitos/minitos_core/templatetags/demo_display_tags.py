"""Template side of ``minitos_core/demo_display.py`` (QA review of #54, S1).

``{{ value|demo_time:location }}`` renders a datetime with
``demo_display.TIME_FORMAT`` in the display zone
(``demo_display.TIME_DISPLAY_ZONE``), or ``demo_display.MISSING_VALUE`` when it
is empty, so S2, the vessel visit panel and the admin share one formatting path.
"""

from django import template

from .. import demo_display

register = template.Library()


@register.filter
def demo_time(value, location):
    return demo_display.format_local_time(value, location)


@register.filter
def demo_time_with_zone(value, location):
    return demo_display.format_local_time(value, location, with_zone=True)
