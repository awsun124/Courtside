"""Small, cached ESPN historical samples for validating the research pipeline."""

from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
from zoneinfo import ZoneInfo

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/"
PARSER_VERSION = 2
EASTERN = ZoneInfo("America/New_York")


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Game timestamp is missing a timezone")
    return parsed.astimezone(timezone.utc)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise ValueError("Expected a numeric provider ID stored as a string")
    return "espn:" + value


def integer(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value):
        raise ValueError("Expected a nonnegative integer statistic")
    return int(value)


def minutes(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]+(?::[0-5][0-9])?", value):
        raise ValueError("Invalid player minutes")
    whole, _, seconds = value.partition(":")
    return int(whole) + (int(seconds) / 60 if seconds else 0)


def fetch_bytes(url):
    try:
        response = subprocess.run([
            "curl", "--disable", "--compressed", "--silent", "--show-error", "--max-time", "20",
            "--connect-timeout", "8", "--max-filesize", "5000000", "--proto", "=https",
            "--write-out", "\n%{http_code}", url,
        ], capture_output=True, timeout=23)
    except (OSError, subprocess.TimeoutExpired):
        raise ValueError("Historical source could not be fetched; curl is required") from None
    body, _, status = response.stdout.rpartition(b"\n")
    if response.returncode or status != b"200":
        raise ValueError("Historical source request failed; cached files were retained")
    return body


def read_source(kind, key, folder, transport=fetch_bytes):
    """Cache raw bytes with provenance; verify the checksum before reuse."""
    if kind not in {"scoreboard", "summary"} or not re.fullmatch(r"[0-9]+", key):
        raise ValueError("Invalid historical source request")
    parameter = "dates" if kind == "scoreboard" else "event"
    url = BASE_URL + kind + "?" + parameter + "=" + key
    raw_path = Path(folder) / "raw" / f"{kind}-{key}.json"
    metadata_path = raw_path.with_suffix(".meta.json")
    if raw_path.exists() or metadata_path.exists():
        if not raw_path.exists() or not metadata_path.exists():
            raise ValueError("Incomplete raw cache; remove the affected pair to fetch again")
        raw = raw_path.read_bytes()
        metadata = json.loads(metadata_path.read_text())
        if metadata.get("url") != url or metadata.get("sha256") != hashlib.sha256(raw).hexdigest():
            raise ValueError("Raw cache checksum or source URL does not match")
        payload = json.loads(raw)
    else:
        raw = transport(url)
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Historical source did not return a JSON object")
        metadata = dict(url=url, retrieved_at=datetime.now(timezone.utc).isoformat(),
                        sha256=hashlib.sha256(raw).hexdigest(), provider="espn")
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(raw)
        metadata_path.write_text(json.dumps(metadata, indent=2) + "\n")
    if not isinstance(payload, dict):
        raise ValueError("Historical source did not return a JSON object")
    return payload, metadata


def game_ids(payload, requested_date):
    """Reject duplicate or out-of-day events instead of silently changing scope."""
    requested = date.fromisoformat(requested_date)
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("Scoreboard is missing its events list")
    result = []
    for event in events:
        value = event["id"]
        identifier(value)
        if value in result:
            raise ValueError("Duplicate scoreboard game")
        if timestamp(event["date"]).astimezone(EASTERN).date() != requested:
            raise ValueError("Scoreboard game is outside the requested Eastern date")
        result.append(value)
    return result


def parse_result(payload, expected_id):
    header = payload["header"]
    if header["id"] != expected_id:
        raise ValueError("Summary returned a different game")
    game_id = identifier(expected_id)
    season = header["season"]
    if type(season["year"]) is not int or season["type"] != 2:
        raise ValueError("Expected a regular-season game")
    competitions = header["competitions"]
    if len(competitions) != 1:
        raise ValueError("Expected one game competition")
    competition = competitions[0]
    if competition["status"]["type"].get("completed") is not True:
        raise ValueError("Game is not final")
    played_at = timestamp(competition["date"]).isoformat()
    sides = {}
    for competitor in competition["competitors"]:
        side = competitor["homeAway"]
        if side not in {"home", "away"} or side in sides:
            raise ValueError("Invalid or duplicate home/away team")
        team = competitor["team"]
        sides[side] = dict(team_id=identifier(team["id"]), name=team["displayName"],
                           points=integer(competitor["score"]))
    if set(sides) != {"home", "away"} or sides["home"]["team_id"] == sides["away"]["team_id"]:
        raise ValueError("Expected two distinct teams")
    if sides["home"]["points"] == sides["away"]["points"]:
        raise ValueError("Final NBA game cannot be tied")
    return dict(game_id=game_id, season=f"{season['year'] - 1}-{str(season['year'])[-2:]}",
                scheduled_start=played_at, home=sides["home"], away=sides["away"],
                home_win=sides["home"]["points"] > sides["away"]["points"],
                neutral_site=competition.get("neutralSite"))


def parse_game(payload, expected_id):
    game = parse_result(payload, expected_id)
    game_id = game["game_id"]
    sides = {side: game[side] for side in ("home", "away")}
    players, seen, team_ids = [], set(), set()
    for group in payload["boxscore"]["players"]:
        team_id = identifier(group["team"]["id"])
        if team_id in team_ids or team_id not in {s["team_id"] for s in sides.values()}:
            raise ValueError("Unknown or duplicate box-score team")
        team_ids.add(team_id)
        if len(group["statistics"]) != 1:
            raise ValueError("Expected one full-game player statistics table")
        table = group["statistics"][0]
        labels = table["labels"]
        required = {"MIN", "PTS", "FG", "3PT", "FT", "REB", "AST", "TO", "STL", "BLK"}
        if len(labels) != len(set(labels)) or not required.issubset(labels):
            raise ValueError("Missing or duplicate box-score columns")
        for athlete in table["athletes"]:
            player_id = identifier(athlete["athlete"]["id"])
            if player_id in seen:
                raise ValueError("Duplicate player in game")
            seen.add(player_id)
            # DNP is an outcome, not evidence of a pregame injury status.
            if athlete.get("didNotPlay") is True:
                continue
            if len(athlete["stats"]) != len(labels):
                raise ValueError("Player statistics do not match columns")
            stats = dict(zip(labels, athlete["stats"]))
            # Some listed players have no recorded minutes despite didNotPlay=False.
            # Retain them as observations elsewhere, not as numeric played logs.
            if stats["MIN"] == "--" and all(value in {"0", "+0", "-0", "0-0", "--"}
                                               for label, value in stats.items() if label != "MIN"):
                continue
            row = dict(game_id=game_id, team_id=team_id, player_id=player_id,
                       name=athlete["athlete"]["displayName"], minutes=minutes(stats["MIN"]))
            for source, target in (("PTS", "points"), ("REB", "rebounds"), ("AST", "assists"),
                                   ("TO", "turnovers"), ("STL", "steals"), ("BLK", "blocks")):
                row[target] = integer(stats[source])
            for source, target in (("FG", "field_goals"), ("3PT", "three_pointers"), ("FT", "free_throws")):
                made, separator, attempted = stats[source].partition("-")
                if not separator:
                    raise ValueError("Invalid made/attempted shooting statistic")
                row[target + "_made"], row[target + "_attempted"] = integer(made), integer(attempted)
                if row[target + "_made"] > row[target + "_attempted"]:
                    raise ValueError("Made shots exceed attempts")
            if (row["three_pointers_made"] > row["field_goals_made"] or
                    row["three_pointers_attempted"] > row["field_goals_attempted"]):
                raise ValueError("Three-point shots exceed all field goals")
            if row["points"] != 2 * row["field_goals_made"] + row["three_pointers_made"] + row["free_throws_made"]:
                raise ValueError("Player points do not match shooting totals")
            players.append(row)
    if len(team_ids) != 2:
        raise ValueError("Missing box-score team")
    for side in sides.values():
        team_players = [p for p in players if p["team_id"] == side["team_id"]]
        if len(team_players) < 5 or sum(p["points"] for p in team_players) != side["points"]:
            raise ValueError("Player points do not reconcile to the final score")
    return game, players
