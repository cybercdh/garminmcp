# garminmcp

An MCP server that gives Claude access to your Garmin Connect data, the same data your watch syncs after every activity and overnight. Sitting alongside [ergmcp](https://github.com/cybercdh/ergmcp), it lets Claude see whole training load, pool swims, strength sessions, sleep, HRV, and recovery, not just what happens on the erg. For the full AI training coach setup these servers were built for, see the [coaching guide](https://github.com/cybercdh/ergmcp/blob/main/COACHING.md).

## What it exposes

The server wraps Garmin Connect through the [garminconnect](https://github.com/cyberjunky/python-garminconnect) library and provides nine tools.

- `get_profile` returns the logged in account's name and unit system.
- `list_activities` lists activities between two dates for any sport, with duration, distance, calories, heart rate, training effect, and sport specific extras such as SWOLF for swims or set counts for strength work.
- `get_activity` returns one activity in detail, laps and splits, time in heart rate zones, and for strength sessions each exercise set with reps and weight.
- `get_activity_track` returns an activity's sample by sample track: latitude, longitude, elevation, elapsed and moving time, distance, heart rate, speed, power, cadence, and e-bike assist mode and battery level when recorded. Use it to time a segment between two GPS points, compute VAM or gradient, or inspect the power and assist profile the summary hides. Evenly downsampled to `max_points` (default 1000) to stay compact.
- `daily_wellness` returns one day's steps, calories, resting heart rate, stress, body battery, and intensity minutes.
- `wellness_range` returns compact day by day wellness rows across up to 31 days, useful for judging recovery trend before planning a week.
- `get_sleep` returns one night's duration, stages, sleep score, overnight resting HR and HRV, and body battery change.
- `get_hrv` returns HRV status with last night's average against the weekly average and baseline.
- `training_status` returns Garmin's training readiness score, training status phrase, VO2 max, and acute load ratio.

## Setup

1. Install [uv](https://docs.astral.sh/uv/) if you don't have it (`brew install uv`). Dependencies resolve automatically on first run.
2. Log in once. This prompts for your Garmin email, password, and MFA code if enabled, then caches OAuth tokens in `~/.garminconnect` (override with `GARMINTOKENS`). Your password is not stored, and the tokens last about a year.

   ```sh
   uv run /path/to/garminmcp/auth_setup.py
   ```

3. From this directory, Claude Code picks the server up automatically via `.mcp.json`. To register it globally so every project can use it, run

   ```sh
   claude mcp add --scope user garmin -- uv run /path/to/garminmcp/server.py
   ```

   For Claude Desktop, add the same command and args to `claude_desktop_config.json`.

## Notes

- Garmin has no official public API for personal use; the library speaks to the same endpoints the Garmin Connect app uses. If Garmin changes those endpoints a library update (`uv cache clean garminconnect`) usually fixes it.
- When tokens expire the tools return a message telling you to rerun `auth_setup.py`.
- Responses are trimmed to training relevant fields; the raw Garmin payloads are far larger.
