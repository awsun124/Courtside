"""Import SportsDataverse ESPN player CSVs against our audited game inventory."""

import csv
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import subprocess

from .features import validate_games
from .history import identifier, timestamp
from .players import PLAYER_STATS, PLAYER_FEATURE_VERSION

BASE_URL = "https://github.com/sportsdataverse/sportsdataverse-data/releases/download/espn_nba_player_boxscores/"
ALIASES = {"three_pointers_made": "three_point_field_goals_made",
           "three_pointers_attempted": "three_point_field_goals_attempted"}


def cache_csv(year, output):
    filename = f"player_box_{year}.csv"
    path = Path(output) / "raw-bulk" / filename
    meta_path = path.with_suffix(".meta.json")
    url = BASE_URL + filename
    if not path.exists() and not meta_path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        response = subprocess.run(["curl", "--disable", "--location", "--compressed", "--silent", "--show-error",
            "--max-time", "60", "--max-filesize", "25000000", "--proto", "=https", "--proto-redir", "=https",
            "--output", str(temporary), "--write-out", "%{http_code}", url], capture_output=True, timeout=65)
        if response.returncode or response.stdout != b"200":
            temporary.unlink(missing_ok=True)
            raise ValueError("Bulk CSV download failed")
        raw = temporary.read_bytes()
        # Do not cache an HTML error page as valid season data.
        if not raw.startswith(b"game_id,"):
            temporary.unlink(missing_ok=True)
            raise ValueError("Bulk source did not return the expected CSV")
        temporary.replace(path)
        meta_path.write_text(json.dumps(dict(url=url, retrieved_at=datetime.now(timezone.utc).isoformat(),
            sha256=hashlib.sha256(raw).hexdigest(), provider="sportsdataverse/ESPN"), indent=2) + "\n")
    if not path.exists() or not meta_path.exists():
        raise ValueError("Incomplete bulk cache")
    metadata = json.loads(meta_path.read_text())
    if metadata.get("url") != url or metadata.get("sha256") != hashlib.sha256(path.read_bytes()).hexdigest():
        raise ValueError("Bulk cache provenance does not match")
    return path, metadata


def number(value):
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("Invalid bulk player statistic")
    return result


def parse_bulk_game(rows, game):
    players, observations, seen = [], [], set()
    for row in rows:
        player_id, team_id = identifier(row["athlete_id"]), identifier(row["team_id"])
        if player_id in seen:
            raise ValueError("Duplicate bulk player/game")
        seen.add(player_id)
        side = row["home_away"]
        if (side not in {"home", "away"} or team_id != game[side]["team_id"] or
                int(row["team_score"]) != game[side]["points"] or
                timestamp(row["game_date_time"]) != timestamp(game["scheduled_start"])):
            raise ValueError("Bulk date/team/score differs from audited result")
        if row["did_not_play"] not in {"true", "false"}:
            raise ValueError("Unknown bulk DNP flag")
        dnp = row["did_not_play"] == "true"
        unknown = not dnp and row["minutes"] in {"", "NA", "--"}
        observations.append(dict(game_id=game["game_id"], player_id=player_id, team_id=team_id,
                                 did_not_play=dnp, participation_unknown=unknown))
        if dnp:
            continue
        stats = {key: number(row[ALIASES.get(key, key)]) for key in PLAYER_STATS}
        if any(not value.is_integer() for value in stats.values()):
            raise ValueError("Noninteger counting statistic")
        if unknown:
            if any(stats.values()):
                raise ValueError("Missing minutes with nonzero statistics")
            continue
        for made, attempts in (("field_goals_made", "field_goals_attempted"),
                               ("three_pointers_made", "three_pointers_attempted"),
                               ("free_throws_made", "free_throws_attempted")):
            if stats[made] > stats[attempts]:
                raise ValueError("Made shots exceed attempts")
        if (stats["three_pointers_made"] > stats["field_goals_made"] or
                stats["three_pointers_attempted"] > stats["field_goals_attempted"] or
                stats["points"] != 2 * stats["field_goals_made"] + stats["three_pointers_made"] + stats["free_throws_made"]):
            raise ValueError("Bulk shooting totals are inconsistent")
        players.append(dict(game_id=game["game_id"], player_id=player_id, team_id=team_id,
                            name=row["athlete_display_name"], minutes=number(row["minutes"]),
                            **{key: int(value) for key, value in stats.items()}))
    for side in ("home", "away"):
        logs = [p for p in players if p["team_id"] == game[side]["team_id"]]
        if len(logs) < 5 or sum(p["points"] for p in logs) != game[side]["points"]:
            raise ValueError("Bulk player totals do not reconcile")
    return players, observations


def import_bulk(history, paths, sources):
    if history["coverage"].get("complete") is not True:
        raise ValueError("Audited game source is incomplete")
    validate_games(history["games"])
    game_map = {g["game_id"]: g for g in history["games"]}
    by_game = defaultdict(list)
    for path in paths:
        with Path(path).open(newline="") as handle:
            for row in csv.DictReader(handle):
                key = identifier(row["game_id"])
                if key in game_map:
                    by_game[key].append(row)
    players, observations, games, rejected = [], [], [], []
    for game in history["games"]:
        try:
            logs, observed = parse_bulk_game(by_game.get(game["game_id"], []), game)
            players.extend(logs)
            observations.extend(observed)
            games.append(game)
        except (ValueError, KeyError, TypeError) as error:
            rejected.append(dict(game_id=game["game_id"], reason=str(error)))
    return dict(coverage=dict(provider="sportsdataverse/ESPN", player_feature_version=PLAYER_FEATURE_VERSION,
        selected_games=len(game_map), validated_games=len(games), source_games=len(game_map),
        selected_complete=not rejected, full_history_complete=not rejected,
        player_rows=len(players), unknown_participation_rows=sum(r["participation_unknown"] for r in observations),
        rejected=rejected), games=games, players=players, observations=observations, sources=sources)
