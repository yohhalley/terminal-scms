"""TCL-006: reproducible demo scenarios (seed, reset and scripted steps).

A scenario module exposes ``NAME``, ``DEFAULT_SEED``, ``seed(rng, **options)`` and
``STEPS`` (step name -> callable). The registry below is the only list of
scenarios the ``scenario_reset`` and ``scenario_step`` commands accept.
"""

from . import import_v1

SCENARIOS = {
    import_v1.NAME: import_v1,
}


def get_scenario(name):
    try:
        return SCENARIOS[name]
    except KeyError as exc:
        known = ', '.join(sorted(SCENARIOS))
        raise LookupError(f'Unknown scenario {name!r}. Known scenarios: {known}.') from exc


def reset_scenario(name, *, seed=None, containers=1):
    """Wipe the MiniTOS app tables, restore catalogs and seed ``name``.

    Runs in one transaction: a failing seed leaves the previous state intact.
    MQTT is not touched here; the outbox rows are published by the normal
    ``publish_uns_outbox`` worker (or ``scenario_reset --publish``).
    """
    import random

    from django.db import transaction

    from ..services.che_components import ensure_component_catalog, ensure_signal_catalog
    from .base import wipe_minitos_tables

    scenario = get_scenario(name)
    seed = scenario.DEFAULT_SEED if seed is None else int(seed)
    with transaction.atomic():
        tables = wipe_minitos_tables()
        ensure_signal_catalog(ensure_component_catalog())
        result = scenario.seed(random.Random(seed), containers=containers)
    result['tables_wiped'] = tables
    result['seed'] = seed
    return result


def run_step(name, step, **options):
    scenario = get_scenario(name)
    try:
        handler = scenario.STEPS[step]
    except KeyError as exc:
        known = ', '.join(sorted(scenario.STEPS))
        raise LookupError(f'Unknown step {step!r} for {name}. Known steps: {known}.') from exc
    return handler(**options)
