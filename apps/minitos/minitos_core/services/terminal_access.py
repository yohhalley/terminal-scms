def accessible_terminals(user):
    """Authorization boundary for terminal-scoped tools.

    All authenticated users currently see every terminal. Organization or
    object-level grants can replace this query without changing the views.
    """
    from minitos_core.models import Location

    if not user.is_authenticated:
        return Location.objects.none()
    return Location.objects.filter(location_type__in=[Location.TL, Location.ID]).order_by('name')


def terminal_capabilities(user, location):
    """Return the terminal actions currently available to a user."""
    has_terminal_access = user.is_authenticated
    return {
        'can_view_terminal': has_terminal_access,
        'can_edit_terminal': has_terminal_access,
        'can_manage_berth_plan': has_terminal_access,
    }
