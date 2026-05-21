"""Profile-scoped goal runtime cron spec loader.

The goal runtime is opt-in and profile-local. It reads ``cron/*.yaml`` from
the active ``HERMES_HOME`` and mirrors those specs into the existing cron job
store so the normal scheduler owns execution, locking, output, and status.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


def _load_spec(path: Path) -> dict[str, Any] | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skipping goal runtime cron spec %s: %s", path, exc)
            return None
    return data if isinstance(data, dict) else None


def _normalize_toolsets(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple, set)):
        items = list(value)
    else:
        return None
    normalized = [str(item).strip() for item in items if str(item).strip()]
    return normalized or None


def _job_updates_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    enabled = bool(spec.get("enabled", True))
    updates: dict[str, Any] = {
        "name": str(spec.get("name") or spec["id"]),
        "schedule": str(spec["schedule"]),
        "prompt": str(spec["prompt"]),
        "deliver": str(spec.get("deliver") or "local"),
        "enabled": enabled,
        "state": "scheduled" if enabled else "paused",
    }
    toolsets = _normalize_toolsets(spec.get("enabled_toolsets"))
    if toolsets is not None:
        updates["enabled_toolsets"] = toolsets
    if spec.get("workdir"):
        updates["workdir"] = str(spec["workdir"])
    if spec.get("model"):
        updates["model"] = str(spec["model"])
    if spec.get("provider"):
        updates["provider"] = str(spec["provider"])
    if spec.get("base_url"):
        updates["base_url"] = str(spec["base_url"])
    return updates


def sync_profile_goal_cron_specs(profile_dir: Path | None = None) -> dict[str, int]:
    """Mirror active-profile ``cron/*.yaml`` specs into cron jobs.

    Returns counters for observability: ``loaded``, ``created``, ``updated``,
    and ``skipped``.
    """
    from cron.jobs import create_job, get_job, load_jobs, save_jobs, update_job

    root = profile_dir or get_hermes_home()
    cron_dir = root / "cron"
    summary = {"loaded": 0, "created": 0, "updated": 0, "skipped": 0}
    if not cron_dir.is_dir():
        return summary

    for path in sorted([*cron_dir.glob("*.yaml"), *cron_dir.glob("*.yml")]):
        spec = _load_spec(path)
        if not spec or not spec.get("id") or not spec.get("schedule") or not spec.get("prompt"):
            summary["skipped"] += 1
            continue

        job_id = str(spec["id"]).strip()
        if not job_id:
            summary["skipped"] += 1
            continue

        updates = _job_updates_from_spec({**spec, "id": job_id})
        summary["loaded"] += 1
        if get_job(job_id):
            update_job(job_id, updates)
            summary["updated"] += 1
            continue

        create_kwargs = dict(updates)
        enabled = bool(create_kwargs.pop("enabled", True))
        state = str(create_kwargs.pop("state", "scheduled"))
        job = create_job(**create_kwargs)
        raw_jobs = load_jobs()
        for raw in raw_jobs:
            if raw.get("id") == job["id"]:
                raw["id"] = job_id
                raw["enabled"] = enabled
                raw["state"] = state
                break
        save_jobs(raw_jobs)
        summary["created"] += 1

    return summary
