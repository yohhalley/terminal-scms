import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from execution.models import EquipmentState, SimulationSession, SiteProjection


def _mqtt_identifier(value):
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")
    return normalized.upper() or "SCMS"


def _token_digest(token):
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _equipment_snapshot(site):
    fields = [
        "tos_che_id", "name", "equipment_type_id", "equipment_type_code",
        "powered_on", "working", "is_available", "is_dispatched",
        "is_in_maintenance", "latitude", "longitude", "rotation",
        "fuel_level", "energy_level", "speed_mps",
    ]
    return list(EquipmentState.objects.filter(site=site).order_by("tos_che_id").values(*fields))


def _frozen_projection(site):
    return {
        "schema": "scms.oes.simulation-bootstrap.v1",
        "enterprise": {
            "id": str(site.enterprise.source_id),
            "code": site.enterprise.code,
            "name": site.enterprise.name,
        },
        "site": {
            "id": str(site.source_id),
            "code": site.code,
            "name": site.name,
            "configuration_revision": site.source_revision,
            "configuration_checksum": site.snapshot_checksum,
        },
        "projection": site.snapshot_payload,
        "equipment_state": _equipment_snapshot(site),
    }


def _validate_site_ready(site):
    if site.projection_status != SiteProjection.READY:
        raise ValidationError("Only a ready Site projection can start a simulation.")
    if site.lifecycle_status != "published":
        raise ValidationError("The MiniTOS Site configuration is not published.")
    if not site.simulation_enabled:
        raise ValidationError("Simulation is disabled for this Site.")
    if site.source_revision < 1 or len(site.snapshot_checksum) != 64:
        raise ValidationError("The Site projection has no complete configuration revision.")
    snapshot_site = (site.snapshot_payload or {}).get("site") or {}
    if int(snapshot_site.get("configuration_revision", -1)) != site.source_revision:
        raise ValidationError("The Site projection snapshot is stale.")


@transaction.atomic
def start_simulation(site_id, *, simulator_version="local", time_scale=1.0):
    site = SiteProjection.objects.select_for_update().select_related("enterprise").get(pk=site_id)
    _validate_site_ready(site)
    active = site.simulation_sessions.filter(
        actual_state__in=SimulationSession.ACTIVE_STATES,
    ).first()
    if active:
        return active, False
    group_id = _mqtt_identifier(site.enterprise.code)
    node_id = _mqtt_identifier(f"SCMS_{site.enterprise.code}_{site.code}")
    try:
        session = SimulationSession.objects.create(
            site=site,
            configuration_revision=site.source_revision,
            configuration_checksum=site.snapshot_checksum,
            bootstrap_payload=_frozen_projection(site),
            simulator_version=simulator_version,
            time_scale=time_scale,
            sparkplug_group_id=group_id,
            sparkplug_node_id=node_id,
        )
    except IntegrityError:
        return site.simulation_sessions.get(
            actual_state__in=SimulationSession.ACTIVE_STATES,
        ), False
    return session, True


@transaction.atomic
def stop_simulation(session_id):
    session = SimulationSession.objects.select_for_update().get(pk=session_id)
    if session.actual_state in {SimulationSession.STOPPED, SimulationSession.FAILED}:
        return session
    session.desired_state = SimulationSession.STOPPED_DESIRED
    session.token_expires_at = timezone.now()
    if session.actual_state == SimulationSession.PENDING:
        session.actual_state = SimulationSession.STOPPED
        session.runner_status = SimulationSession.RUNNER_STOPPED
        session.stopped_at = timezone.now()
    else:
        session.actual_state = SimulationSession.STOPPING
    session.save()
    return session


@transaction.atomic
def claim_next_session(runner_identity):
    session = (
        SimulationSession.objects.select_for_update()
        .select_related("site__enterprise")
        .filter(
            desired_state=SimulationSession.RUNNING_DESIRED,
            actual_state=SimulationSession.PENDING,
            runner_identity="",
        )
        .order_by("created_at")
        .first()
    )
    if session is None:
        return None, None
    token = secrets.token_urlsafe(32)
    now = timezone.now()
    ttl = int(getattr(settings, "SIMULATION_BOOTSTRAP_TOKEN_TTL_SECONDS", 300))
    session.runner_identity = runner_identity
    session.runner_status = SimulationSession.RUNNER_CLAIMED
    session.actual_state = SimulationSession.CLAIMED
    session.token_hash = _token_digest(token)
    session.token_expires_at = now + timedelta(seconds=ttl)
    session.claimed_at = now
    session.heartbeat_at = now
    session.save()
    return session, token


def launch_environment(session, token):
    return {
        "OES_URL": settings.SIMULATOR_OES_URL.rstrip("/"),
        "SIMULATOR_SITE_ID": str(session.site.source_id),
        "SIMULATOR_SESSION_ID": str(session.pk),
        "SIMULATOR_TOKEN": token,
        "SIMULATOR_CONFIG_REVISION": str(session.configuration_revision),
        "SIMULATOR_CONFIG_CHECKSUM": session.configuration_checksum,
        "SIMULATOR_VERSION": session.simulator_version,
        "SIMULATOR_TIME_SCALE": str(session.time_scale),
        "MQTT_HOST": settings.MQTT_HOST,
        "MQTT_PORT": str(settings.MQTT_PORT),
        "MQTT_USERNAME": settings.MQTT_USERNAME,
        "MQTT_PASSWORD": settings.MQTT_PASSWORD,
        "MQTT_CLIENT_ID": f"sim-{session.pk}",
        "SPARKPLUG_GROUP_ID": session.sparkplug_group_id,
        "SPARKPLUG_NODE_ID": session.sparkplug_node_id,
        "SPARKPLUG_PRIMARY_HOST_ID": settings.SPARKPLUG_HOST_ID,
        **simulation_settings_environment(session),
    }


def simulation_settings_environment(session):
    """TCL-028: the SIMULATOR_* sim settings for the runner claim environment.

    Values set on OES (``settings.SIMULATOR_SIM_SETTINGS``) are forwarded as
    strings. Per-session values win: a seed or start epoch already fixed on the
    session (a rerun, D-016) and the session id as run id. The command TTL is
    always sent so Godot and OES use the same sim-time TTL (default 600 s).
    """
    environment = {
        name: str(value)
        for name, value in getattr(settings, "SIMULATOR_SIM_SETTINGS", {}).items()
        if name in settings.SIMULATOR_SIM_SETTING_NAMES
    }
    environment.setdefault("SIMULATOR_RUN_ID", str(session.pk))
    if session.seed is not None:
        environment["SIMULATOR_SEED"] = str(session.seed)
    if session.start_epoch_ms is not None:
        environment["SIMULATOR_START_EPOCH_MS"] = str(session.start_epoch_ms)
    environment.setdefault(
        "SIMULATOR_COMMAND_TTL_S", str(int(settings.EQUIPMENT_COMMAND_TTL_SIM_SECONDS)),
    )
    return environment


def validate_session_token(session, token):
    if not token or not session.token_hash:
        return False
    if session.token_expires_at is None or session.token_expires_at <= timezone.now():
        return False
    return hmac.compare_digest(session.token_hash, _token_digest(token))


def bootstrap_document(session, token):
    if not validate_session_token(session, token):
        raise ValidationError("The simulation bootstrap token is invalid or expired.")
    if session.desired_state != SimulationSession.RUNNING_DESIRED:
        raise ValidationError("The simulation session is stopping.")
    document = dict(session.bootstrap_payload)
    document["session"] = {
        "id": str(session.pk),
        "site_id": str(session.site.source_id),
        "configuration_revision": session.configuration_revision,
        "configuration_checksum": session.configuration_checksum,
        "simulator_version": session.simulator_version,
        "time_scale": session.time_scale,
        "sparkplug_group_id": session.sparkplug_group_id,
        "sparkplug_node_id": session.sparkplug_node_id,
        "primary_host_id": settings.SPARKPLUG_HOST_ID,
    }
    return document


@transaction.atomic
def report_runner_status(session_id, runner_identity, runner_status, failure_reason=""):
    session = SimulationSession.objects.select_for_update().get(pk=session_id)
    if not session.runner_identity or not hmac.compare_digest(
        session.runner_identity, runner_identity,
    ):
        raise ValidationError("This runner does not own the simulation session.")
    now = timezone.now()
    session.runner_status = runner_status
    session.heartbeat_at = now
    if runner_status == SimulationSession.RUNNER_STARTING:
        session.actual_state = SimulationSession.STARTING
    elif runner_status == SimulationSession.RUNNER_RUNNING:
        session.started_at = session.started_at or now
    elif runner_status == SimulationSession.RUNNER_STOPPED:
        session.actual_state = SimulationSession.STOPPED
        session.desired_state = SimulationSession.STOPPED_DESIRED
        session.stopped_at = now
    elif runner_status == SimulationSession.RUNNER_FAILED:
        session.actual_state = SimulationSession.FAILED
        session.failure_reason = failure_reason or "Simulator runner failed."
        session.stopped_at = now
    session.save()
    return session


@transaction.atomic
def record_session_heartbeat(session, token):
    locked = SimulationSession.objects.select_for_update().get(pk=session.pk)
    if not validate_session_token(locked, token):
        raise ValidationError("The simulation session token is invalid or expired.")
    locked.heartbeat_at = timezone.now()
    locked.save(update_fields=["heartbeat_at", "updated_at"])
    return locked
