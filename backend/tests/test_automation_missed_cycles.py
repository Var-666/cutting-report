import pytest

from test_automation_recovery import workflow, NOW, STAMP


def backlog_schedule(workflow, **changes):
    repository, metadata, service, run = workflow
    original = repository.get_schedule(run["schedule_id"])
    return repository.create_schedule(
        {**original, "next_run_at": "2026-09-27T09:00:00+08:00", **changes},
        user={"user_id": original["created_by_id"], "display_name": original["created_by_name"]},
        now=STAMP,
    )


def test_reservation_insert_failure_rolls_back_schedule_clock(workflow):
    repository, _, _, _ = workflow
    schedule = backlog_schedule(workflow)
    with repository.connect() as connection:
        connection.execute("""
            CREATE TRIGGER fail_run BEFORE INSERT ON scheduled_report_runs
            BEGIN SELECT RAISE(ABORT, 'test reservation insert failure'); END
        """)
    assert repository.reserve_run(
        schedule, scheduled_for=schedule["next_run_at"], report_date="2026-09-26",
        trigger_type="scheduled", now=STAMP, next_run_at="2026-09-28T09:00:00+08:00",
    ) is None
    unchanged = repository.get_schedule(schedule["schedule_id"])
    assert unchanged["next_run_at"] == schedule["next_run_at"]
    assert unchanged["last_run_at"] is None


def test_due_scan_does_not_let_old_backlog_occupy_every_slot(workflow):
    repository, _, _, _ = workflow
    schedules = [backlog_schedule(workflow) for _ in range(11)]
    first = repository.due_schedules(STAMP)
    assert len(first) == 10
    handled_ids = {schedule["schedule_id"] for schedule in first}
    with repository.connect() as connection:
        for schedule in first:
            connection.execute("UPDATE report_schedules SET last_run_at=? WHERE schedule_id=?",
                               (STAMP, schedule["schedule_id"]))
    second = repository.due_schedules(STAMP)
    waiting_id = next(schedule["schedule_id"] for schedule in schedules
                      if schedule["schedule_id"] not in handled_ids)
    assert second[0]["schedule_id"] == waiting_id


def test_one_corrupt_schedule_does_not_stop_other_due_schedules(workflow):
    repository, _, service, _ = workflow
    broken = backlog_schedule(workflow, run_time="invalid")
    healthy = backlog_schedule(workflow)
    assert service.process_due_schedules(NOW) == 1
    assert repository.get_schedule(broken["schedule_id"])["next_run_at"] == broken["next_run_at"]
    with repository.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM scheduled_report_runs WHERE schedule_id=?",
            (healthy["schedule_id"],),
        ).fetchone()[0] == 1


@pytest.mark.parametrize("frequency,extra,first,second,expected_dates", [
    ("daily", {}, "2026-09-27T09:00:00+08:00", "2026-09-28T09:00:00+08:00",
     ["2026-09-26", "2026-09-27", "2026-09-28", "2026-09-29"]),
    ("weekly", {"weekday": 3}, "2026-09-09T09:00:00+08:00", "2026-09-16T09:00:00+08:00",
     ["2026-09-08", "2026-09-15", "2026-09-22", "2026-09-29"]),
    ("monthly", {"month_day": 1, "report_period": "previous_month"},
     "2026-07-01T09:00:00+08:00", "2026-08-01T09:00:00+08:00",
     ["2026-06-01", "2026-07-01", "2026-08-01"]),
])
def test_missed_cycles_are_caught_up_one_per_scan_with_original_report_dates(
        workflow, frequency, extra, first, second, expected_dates):
    repository, metadata, service, _ = workflow
    schedule = backlog_schedule(workflow, frequency=frequency, next_run_at=first, **extra)
    assert service.process_due_schedules(NOW) == 1
    assert repository.get_schedule(schedule["schedule_id"])["next_run_at"] == second
    for _ in expected_dates[1:]:
        assert service.process_due_schedules(NOW) == 1
    assert service.process_due_schedules(NOW) == 0
    with repository.connect() as connection:
        runs = connection.execute(
            "SELECT * FROM scheduled_report_runs WHERE schedule_id=? ORDER BY scheduled_for",
            (schedule["schedule_id"],),
        ).fetchall()
    assert [run["report_date"] for run in runs] == expected_dates
    assert len({run["report_id"] for run in runs}) == len(expected_dates)
    assert all(metadata.get_job(run["report_id"])["report_date"] == run["report_date"] for run in runs)


def test_duplicate_snapshot_cannot_create_same_cycle_twice(workflow):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime
    from app.services.automation_service import next_run_at

    repository, _, _, _ = workflow
    schedule = backlog_schedule(workflow)
    following = next_run_at(schedule, datetime.fromisoformat(schedule["next_run_at"])).isoformat()

    def reserve(_):
        return repository.reserve_run(
            schedule, scheduled_for=schedule["next_run_at"], report_date="2026-09-26",
            trigger_type="scheduled", now=STAMP, next_run_at=following,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(run is not None for run in results) == 1
    assert repository.get_schedule(schedule["schedule_id"])["next_run_at"] == following
