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


def reset_scenario(name, *, seed=None, containers=1, reset_epoch=None):
    """Wipe the MiniTOS app tables, restore catalogs and seed ``name``.

    Runs in one transaction: a failing seed leaves the previous state intact.
    MQTT is not touched here; the outbox rows are published by the normal
    ``publish_uns_outbox`` worker.

    QA M2: ``reset_epoch`` (``make scenario-reset`` passes the same value to
    terminal-oes) is stored before seeding, so the seeded outbox envelopes carry
    it and OES execution events from any other run are ignored. ``None`` stores
    no epoch: no fence, the pre-M2 behaviour.
    """
    import random

    from django.db import transaction

    from ..services.che_components import ensure_component_catalog, ensure_signal_catalog
    from ..services.reset_epoch import set_epoch, validate_epoch
    from ..services.status_events import SOURCE_SCENARIO, status_event_source
    from .base import wipe_minitos_tables

    scenario = get_scenario(name)
    seed = scenario.DEFAULT_SEED if seed is None else int(seed)
    if reset_epoch:
        reset_epoch = validate_epoch(reset_epoch)
    with status_event_source(SOURCE_SCENARIO), transaction.atomic():
        tables = wipe_minitos_tables()
        set_epoch(reset_epoch, scenario=name)
        ensure_signal_catalog(ensure_component_catalog())
        result = scenario.seed(random.Random(seed), containers=containers)
    result['tables_wiped'] = tables
    result['seed'] = seed
    result['reset_epoch'] = reset_epoch or ''
    return result


def run_step(name, step, **options):
    scenario = get_scenario(name)
    try:
        handler = scenario.STEPS[step]
    except KeyError as exc:
        known = ', '.join(sorted(scenario.STEPS))
        raise LookupError(f'Unknown step {step!r} for {name}. Known steps: {known}.') from exc
    from ..services.status_events import SOURCE_SCENARIO, status_event_source

    with status_event_source(SOURCE_SCENARIO):
        return handler(**options)
