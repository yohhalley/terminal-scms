from django.conf import settings


def terminal_root(terminal_code):
    return f"{settings.UNS_ROOT}/{terminal_code}"


def released_order_topic(terminal_code, order_id):
    return f"{terminal_root(terminal_code)}/tos/orders/{order_id}/released"


def execution_event_topic(terminal_code, order_id):
    return f"{terminal_root(terminal_code)}/oes/orders/{order_id}/execution"


def equipment_state_topic(terminal_code, che_id):
    return f"{terminal_root(terminal_code)}/simulator/che/{che_id}/state"


def equipment_command_topic(terminal_code, che_id):
    return f"{terminal_root(terminal_code)}/oes/che/{che_id}/command"


def command_result_topic(terminal_code, command_id):
    return f"{terminal_root(terminal_code)}/simulator/commands/{command_id}/result"


def subscriptions():
    root = settings.UNS_ROOT
    return [
        (f"{root}/+/tos/orders/+/released", 1),
        (f"{root}/+/simulator/che/+/state", 1),
        (f"{root}/+/simulator/commands/+/result", 1),
    ]

