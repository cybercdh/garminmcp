# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "mcp>=2",
#     "garminconnect>=0.2.25",
# ]
# ///
"""MCP server for Garmin Connect.

Exposes Garmin watch data (activities, daily wellness, sleep, HRV, training
status) as MCP tools so Claude can see whole training load, not just erg
sessions, when critiquing workouts and planning training.

Auth: run auth_setup.py once. It logs in with your Garmin credentials
(including MFA) and saves OAuth tokens to ~/.garminconnect (override with
GARMINTOKENS). Tokens last about a year; the server never sees the password.
"""

from __future__ import annotations

import os
from datetime import date as _date, datetime, timedelta
from typing import Any, Optional

from garminconnect import Garmin
from mcp.server.mcpserver import MCPServer

TOKEN_STORE = os.environ.get("GARMINTOKENS", os.path.expanduser("~/.garminconnect"))

mcp = MCPServer("garmin")

_garmin: Optional[Garmin] = None


def _client() -> Garmin:
    global _garmin
    if _garmin is None:
        g = Garmin()
        try:
            g.login(TOKEN_STORE)
        except Exception as exc:  # token store missing, corrupt, or expired
            setup = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth_setup.py")
            raise RuntimeError(
                f"Not authenticated with Garmin Connect ({exc}). Run "
                f"`uv run {setup}` in a terminal to log in once; tokens are "
                f"then cached in {TOKEN_STORE} and last about a year."
            ) from exc
        _garmin = g
    return _garmin


def _fmt_secs(seconds: Optional[float]) -> Optional[str]:
    if seconds is None:
        return None
    total = int(round(float(seconds)))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _pace_per_km(speed_mps: Optional[float]) -> Optional[str]:
    if not speed_mps or speed_mps <= 0:
        return None
    return _fmt_secs(1000.0 / speed_mps)


def _pace_per_100m(speed_mps: Optional[float]) -> Optional[str]:
    if not speed_mps or speed_mps <= 0:
        return None
    return _fmt_secs(100.0 / speed_mps)


def _round(v: Any, nd: int = 1) -> Any:
    return round(v, nd) if isinstance(v, (int, float)) else v


def _try(fn, *args, **kwargs) -> Any:
    """Call a Garmin API method, returning None instead of raising, so one
    missing data type (e.g. no HRV on an older watch) doesn't sink a tool."""
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


def _summarize_activity(a: dict) -> dict:
    type_key = ((a.get("activityType") or {}).get("typeKey")) or a.get("activityTypeDTO", {}).get("typeKey")
    speed = a.get("averageSpeed")
    out = {
        "id": a.get("activityId"),
        "name": a.get("activityName"),
        "type": type_key,
        "start": a.get("startTimeLocal"),
        "duration": _fmt_secs(a.get("duration")),
        "moving_time": _fmt_secs(a.get("movingDuration")),
        "distance_m": _round(a.get("distance"), 0),
        "calories": a.get("calories"),
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "aerobic_training_effect": _round(a.get("aerobicTrainingEffect")),
        "anaerobic_training_effect": _round(a.get("anaerobicTrainingEffect")),
        "training_load": _round(a.get("activityTrainingLoad"), 0),
    }
    if type_key and "swim" in type_key:
        out["pace_per_100m"] = _pace_per_100m(speed)
        out["avg_swolf"] = a.get("averageSwolf")
        out["strokes"] = a.get("strokes")
        out["active_lengths"] = a.get("activeLengths")
        # The list endpoint reports poolLength in centimeters, the detail
        # endpoint in meters; normalize to meters.
        pool_len = a.get("poolLength")
        if pool_len and pool_len > 100:
            pool_len = pool_len / 100.0
        out["pool_length_m"] = pool_len
    elif type_key and ("strength" in type_key or "training" in type_key):
        out["total_sets"] = a.get("totalSets")
        out["total_reps"] = a.get("totalReps")
    else:
        out["pace_per_km"] = _pace_per_km(speed)
    return {k: v for k, v in out.items() if v is not None}


@mcp.tool()
def get_profile() -> dict:
    """Get the authenticated Garmin Connect user's name and unit system."""
    g = _client()
    return {
        "full_name": _try(g.get_full_name),
        "unit_system": _try(g.get_unit_system),
        "token_store": TOKEN_STORE,
    }


@mcp.tool()
def list_activities(
    from_date: str,
    to_date: str,
    activity_type: Optional[str] = None,
) -> dict:
    """List Garmin activities between two dates (inclusive), oldest first.
    Covers every sport synced to Garmin Connect: pool swims, strength
    sessions, runs, rides, indoor rowing recorded on the watch, and so on.

    Args:
        from_date: Start date, YYYY-MM-DD.
        to_date: End date, YYYY-MM-DD.
        activity_type: Optional Garmin type filter such as swimming, running,
            cycling, fitness_equipment (gym/strength), walking.

    Returns activity summaries. Use get_activity with an id for laps, splits,
    and strength exercise sets.
    """
    g = _client()
    acts = g.get_activities_by_date(from_date, to_date, activity_type) or []
    acts = sorted(acts, key=lambda a: a.get("startTimeLocal") or "")
    return {
        "from": from_date,
        "to": to_date,
        "count": len(acts),
        "activities": [_summarize_activity(a) for a in acts],
    }


@mcp.tool()
def get_activity(activity_id: int) -> dict:
    """Get one activity in detail: summary, laps/splits, HR time in zones,
    and for strength sessions the exercise sets (exercise name, reps, weight).

    Args:
        activity_id: The activity id from list_activities.
    """
    g = _client()
    raw = _try(g.get_activity, activity_id) or {}
    summary_dto = raw.get("summaryDTO") or {}
    base = _summarize_activity({**summary_dto, **raw})
    base["id"] = activity_id

    splits = _try(g.get_activity_splits, activity_id) or {}
    laps = []
    for lap in splits.get("lapDTOs") or []:
        laps.append({k: v for k, v in {
            "lap": lap.get("lapIndex"),
            "time": _fmt_secs(lap.get("duration")),
            "distance_m": _round(lap.get("distance"), 0),
            "avg_hr": lap.get("averageHR"),
            "max_hr": lap.get("maxHR"),
            "pace_per_km": _pace_per_km(lap.get("averageSpeed")),
            "avg_swolf": lap.get("averageSwolf"),
            "strokes": lap.get("totalNumberOfStrokes"),
        }.items() if v is not None})
    if laps:
        base["laps"] = laps

    zones = _try(g.get_activity_hr_in_timezones, activity_id)
    if zones:
        base["hr_zones"] = [
            {
                "zone": z.get("zoneNumber"),
                "low_bpm": z.get("zoneLowBoundary"),
                "time": _fmt_secs(z.get("secsInZone")),
            }
            for z in zones
        ]

    sets = _try(g.get_activity_exercise_sets, activity_id) or {}
    exercise_sets = []
    for s in sets.get("exerciseSets") or []:
        if s.get("setType") == "REST":
            continue
        exercises = s.get("exercises") or [{}]
        name = (exercises[0].get("name") or exercises[0].get("category")) if exercises else None
        exercise_sets.append({k: v for k, v in {
            "exercise": name,
            "reps": s.get("repetitionCount"),
            "weight_kg": _round((s.get("weight") or 0) / 1000.0) or None,
            "time": _fmt_secs((s.get("duration") or 0)) if s.get("duration") else None,
        }.items() if v is not None})
    if exercise_sets:
        base["exercise_sets"] = exercise_sets

    return base


@mcp.tool()
def daily_wellness(date: str) -> dict:
    """Daily wellness snapshot for one date: steps, calories, resting and
    min/max heart rate, stress, body battery, and intensity minutes.

    Args:
        date: The date, YYYY-MM-DD.
    """
    g = _client()
    s = _try(g.get_user_summary, date) or {}
    return {
        "date": date,
        "steps": s.get("totalSteps"),
        "total_kilocalories": s.get("totalKilocalories"),
        "active_kilocalories": s.get("activeKilocalories"),
        "resting_hr": s.get("restingHeartRate"),
        "min_hr": s.get("minHeartRate"),
        "max_hr": s.get("maxHeartRate"),
        "avg_stress": s.get("averageStressLevel"),
        "body_battery_high": s.get("bodyBatteryHighestValue"),
        "body_battery_low": s.get("bodyBatteryLowestValue"),
        "body_battery_now": s.get("bodyBatteryMostRecentValue"),
        "moderate_intensity_minutes": s.get("moderateIntensityMinutes"),
        "vigorous_intensity_minutes": s.get("vigorousIntensityMinutes"),
    }


@mcp.tool()
def wellness_range(from_date: str, to_date: str) -> dict:
    """Compact day-by-day wellness rows (steps, resting HR, stress, body
    battery range) across a date range, at most 31 days. Use this to judge
    recovery and overall load trend before planning a training week.

    Args:
        from_date: Start date, YYYY-MM-DD.
        to_date: End date, YYYY-MM-DD.
    """
    g = _client()
    start = _date.fromisoformat(from_date)
    end = _date.fromisoformat(to_date)
    if (end - start).days > 30:
        raise ValueError("Range too large; request at most 31 days.")
    days = []
    d = start
    while d <= end:
        cdate = d.isoformat()
        s = _try(g.get_user_summary, cdate) or {}
        days.append({
            "date": cdate,
            "steps": s.get("totalSteps"),
            "resting_hr": s.get("restingHeartRate"),
            "avg_stress": s.get("averageStressLevel"),
            "body_battery_high": s.get("bodyBatteryHighestValue"),
            "body_battery_low": s.get("bodyBatteryLowestValue"),
        })
        d += timedelta(days=1)
    return {"from": from_date, "to": to_date, "days": days}


@mcp.tool()
def get_sleep(date: str) -> dict:
    """Sleep detail for one night: duration, stages, sleep score, overnight
    resting HR and HRV, and body battery change.

    Args:
        date: The wake-up date, YYYY-MM-DD.
    """
    g = _client()
    data = _try(g.get_sleep_data, date) or {}
    dto = data.get("dailySleepDTO") or {}
    scores = dto.get("sleepScores") or {}
    return {
        "date": date,
        "sleep_time": _fmt_secs(dto.get("sleepTimeSeconds")),
        "deep": _fmt_secs(dto.get("deepSleepSeconds")),
        "light": _fmt_secs(dto.get("lightSleepSeconds")),
        "rem": _fmt_secs(dto.get("remSleepSeconds")),
        "awake": _fmt_secs(dto.get("awakeSleepSeconds")),
        "sleep_score": (scores.get("overall") or {}).get("value"),
        "sleep_quality": (scores.get("overall") or {}).get("qualifierKey"),
        "overnight_resting_hr": data.get("restingHeartRate"),
        "overnight_avg_hrv": data.get("avgOvernightHrv"),
        "body_battery_change": data.get("bodyBatteryChange"),
    }


@mcp.tool()
def get_hrv(date: str) -> dict:
    """Heart rate variability summary for one date: last night's average,
    weekly average, HRV status, and the balanced baseline range.

    Args:
        date: The date, YYYY-MM-DD.
    """
    g = _client()
    data = _try(g.get_hrv_data, date) or {}
    s = data.get("hrvSummary") or {}
    baseline = s.get("baseline") or {}
    return {
        "date": date,
        "last_night_avg": s.get("lastNightAvg"),
        "last_night_5min_high": s.get("lastNight5MinHigh"),
        "weekly_avg": s.get("weeklyAvg"),
        "status": s.get("status"),
        "baseline_low_upper": baseline.get("lowUpper"),
        "baseline_balanced_low": baseline.get("balancedLow"),
        "baseline_balanced_upper": baseline.get("balancedUpper"),
    }


@mcp.tool()
def training_status(date: str) -> dict:
    """Garmin's training assessment for one date: training readiness score,
    training status, VO2 max, and acute load. Use alongside wellness_range
    when judging whether to advance or repeat a training progression.

    Args:
        date: The date, YYYY-MM-DD.
    """
    g = _client()
    out: dict[str, Any] = {"date": date}

    readiness = _try(g.get_training_readiness, date)
    if isinstance(readiness, list) and readiness:
        readiness = readiness[0]
    if isinstance(readiness, dict):
        out["readiness_score"] = readiness.get("score")
        out["readiness_level"] = readiness.get("level")
        out["readiness_feedback"] = readiness.get("feedbackShort")

    status = _try(g.get_training_status, date) or {}
    most_recent = status.get("mostRecentTrainingStatus") or {}
    latest = (most_recent.get("latestTrainingStatusData") or {})
    if isinstance(latest, dict) and latest:
        first = next(iter(latest.values()), {})
        if isinstance(first, dict):
            out["training_status"] = first.get("trainingStatusFeedbackPhrase")
            out["acute_load"] = first.get("acuteTrainingLoadDTO", {}).get("dailyAcuteChronicWorkloadRatio")
    vo2 = status.get("mostRecentVO2Max") or {}
    generic = (vo2.get("generic") or {})
    if generic.get("vo2MaxValue"):
        out["vo2max"] = generic.get("vo2MaxValue")

    return out


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
