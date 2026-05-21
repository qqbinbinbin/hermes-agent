"""Tests for profile-scoped goal runtime cron specs."""

import json


def test_sync_profile_goal_cron_specs_creates_jobs(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes" / "profiles" / "director"
    cron_dir = hermes_home / "cron"
    cron_dir.mkdir(parents=True)
    (cron_dir / "director-loop.yaml").write_text(
        """
id: director-loop
name: Director goal loop
schedule: every 5m
prompt: |
  Run one Plan -> Act -> Observe -> Diagnose -> Re-plan cycle.
enabled_toolsets:
  - fuxi_contract
  - todo
deliver: local
""".strip(),
        encoding="utf-8",
    )

    import cron.jobs as jobs_mod
    import cron.profile_goal_runtime as goal_runtime

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(jobs_mod, "CRON_DIR", hermes_home / "cron")
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", hermes_home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", hermes_home / "cron" / "output")

    summary = goal_runtime.sync_profile_goal_cron_specs()

    assert summary == {"loaded": 1, "created": 1, "updated": 0, "skipped": 0}
    jobs = jobs_mod.list_jobs(include_disabled=True)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["id"] == "director-loop"
    assert job["name"] == "Director goal loop"
    assert job["schedule_display"] == "every 5m"
    assert job["enabled_toolsets"] == ["fuxi_contract", "todo"]
    assert job["deliver"] == "local"


def test_sync_profile_goal_cron_specs_updates_existing_job(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes" / "profiles" / "director"
    cron_dir = hermes_home / "cron"
    cron_dir.mkdir(parents=True)

    import cron.jobs as jobs_mod
    import cron.profile_goal_runtime as goal_runtime

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(jobs_mod, "CRON_DIR", hermes_home / "cron")
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", hermes_home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", hermes_home / "cron" / "output")

    jobs_mod.create_job(
        prompt="old prompt",
        schedule="every 30m",
        name="old",
        enabled_toolsets=["todo"],
    )
    raw = jobs_mod.load_jobs()
    raw[0]["id"] = "director-loop"
    jobs_mod.save_jobs(raw)

    (cron_dir / "director-loop.yaml").write_text(
        """
id: director-loop
name: Director loop updated
schedule: every 10m
prompt: new prompt
enabled_toolsets: [fuxi_contract]
deliver: local
""".strip(),
        encoding="utf-8",
    )

    summary = goal_runtime.sync_profile_goal_cron_specs()

    assert summary == {"loaded": 1, "created": 0, "updated": 1, "skipped": 0}
    job = jobs_mod.get_job("director-loop")
    assert job["name"] == "Director loop updated"
    assert job["prompt"] == "new prompt"
    assert job["enabled_toolsets"] == ["fuxi_contract"]
    assert job["schedule_display"] == "every 10m"


def test_sync_profile_goal_cron_specs_skips_yaml_without_id_or_prompt(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes" / "profiles" / "director"
    cron_dir = hermes_home / "cron"
    cron_dir.mkdir(parents=True)
    (cron_dir / "bad.yaml").write_text("name: missing required fields\n", encoding="utf-8")

    import cron.jobs as jobs_mod
    import cron.profile_goal_runtime as goal_runtime

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(jobs_mod, "CRON_DIR", hermes_home / "cron")
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", hermes_home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", hermes_home / "cron" / "output")

    summary = goal_runtime.sync_profile_goal_cron_specs()

    assert summary == {"loaded": 0, "created": 0, "updated": 0, "skipped": 1}
    assert jobs_mod.list_jobs(include_disabled=True) == []


def test_sync_profile_goal_cron_specs_accepts_json_yaml_fallback(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes" / "profiles" / "director"
    cron_dir = hermes_home / "cron"
    cron_dir.mkdir(parents=True)
    (cron_dir / "director-loop.yaml").write_text(
        json.dumps(
            {
                "id": "director-loop",
                "name": "Director JSON loop",
                "schedule": "every 15m",
                "prompt": "run one loop",
            }
        ),
        encoding="utf-8",
    )

    import cron.jobs as jobs_mod
    import cron.profile_goal_runtime as goal_runtime

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(jobs_mod, "CRON_DIR", hermes_home / "cron")
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", hermes_home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", hermes_home / "cron" / "output")

    summary = goal_runtime.sync_profile_goal_cron_specs()

    assert summary == {"loaded": 1, "created": 1, "updated": 0, "skipped": 0}
    assert jobs_mod.get_job("director-loop")["name"] == "Director JSON loop"


def test_sync_profile_goal_cron_specs_preserves_disabled_specs(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes" / "profiles" / "director"
    cron_dir = hermes_home / "cron"
    cron_dir.mkdir(parents=True)
    (cron_dir / "director-loop.yaml").write_text(
        """
id: director-loop
name: Director disabled loop
schedule: every 5m
prompt: do not run until enabled
enabled: false
""".strip(),
        encoding="utf-8",
    )

    import cron.jobs as jobs_mod
    import cron.profile_goal_runtime as goal_runtime

    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(jobs_mod, "CRON_DIR", hermes_home / "cron")
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", hermes_home / "cron" / "jobs.json")
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", hermes_home / "cron" / "output")

    summary = goal_runtime.sync_profile_goal_cron_specs()

    assert summary == {"loaded": 1, "created": 1, "updated": 0, "skipped": 0}
    jobs = jobs_mod.list_jobs(include_disabled=True)
    assert len(jobs) == 1
    assert jobs[0]["id"] == "director-loop"
    assert jobs[0]["enabled"] is False
