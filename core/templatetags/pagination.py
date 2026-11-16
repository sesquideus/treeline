from django import template

register = template.Library()

#: Up to this many pages, every one gets a link; beyond it, the run is elided.
MAX_PAGE_LINKS = 40


@register.filter
def page_links(page_obj, limit=MAX_PAGE_LINKS):
    """
    The page numbers to link to from `page_obj`: all of them up to `limit` pages, and past
    that the first and last, a window around the current page, and `Paginator.ELLIPSIS`
    for each gap — never more than `limit` entries in all.
    """
    paginator = page_obj.paginator
    if paginator.num_pages <= limit:
        return paginator.page_range
    # The first and last page, an ellipsis either side, and the window between: 4 + 2w + 1.
    window = (limit - 5) // 2
    return paginator.get_elided_page_range(page_obj.number, on_each_side=window, on_ends=1)
