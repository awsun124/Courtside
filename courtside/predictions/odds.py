"""Server-only NBA moneyline collection with bounded free-tier usage."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .history import timestamp

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENDPOINT = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds?regions=us&markets=h2h&oddsFormat=decimal&dateFormat=iso"
MIN_INTERVAL = timedelta(hours=6)
MONTHLY_REQUEST_LIMIT = 124


def load_key(env_file=PROJECT_ROOT / ".env"):
    key = os.environ.get("ODDS_API_KEY", "").strip()
    if not key and Path(env_file).is_file():
        for line in Path(env_file).read_text().splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() == "ODDS_API_KEY":
                key = value.strip().strip("\"'")
                break
    if (not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", key) or
            key.lower() in {"your_actual_key_here", "your_api_key", "your_api_key_here"}):
        raise ValueError("Set a valid ODDS_API_KEY in the server .env file or environment")
    return key


def fetch_odds(key):
    with tempfile.TemporaryDirectory() as folder:
        headers = Path(folder) / "headers"
        try:
            response = subprocess.run([
                "curl", "--disable", "--compressed", "--silent", "--max-time", "20",
                "--connect-timeout", "8", "--max-filesize", "5000000", "--proto", "=https",
                "--header", "@-", "--dump-header", str(headers), "--write-out", "\n%{http_code}", ENDPOINT,
            ], input=f"x-api-key: {key}\n", capture_output=True, text=True, timeout=23)
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("Odds source connection failed; no retry was made") from None
        body, _, status = response.stdout.rpartition("\n")
        if response.returncode:
            raise ValueError("Odds source connection failed; no retry was made")
        if status != "200":
            messages = {"401": "ODDS_API_KEY was rejected", "403": "Odds source denied access",
                        "402": "Odds source quota unavailable", "429": "Odds source rate/quota limit reached"}
            raise ValueError(messages.get(status, "Odds source request failed") + "; no retry was made")
        if key in body:
            raise ValueError("Unexpected credential in odds response; response was not saved")
        try:
            payload = json.loads(body)
        except ValueError:
            raise ValueError("Odds source returned invalid JSON") from None
        usage = {}
        for line in headers.read_text().splitlines():
            name, separator, value = line.partition(":")
            if separator and name.lower() in {"x-requests-remaining", "x-requests-used", "x-requests-last"}:
                if not value.strip().isdigit():
                    raise ValueError("Odds source returned invalid quota metadata")
                usage[name.lower()] = int(value.strip())
        return payload, usage


def decimal_odds(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 1:
        raise ValueError("Invalid decimal moneyline")
    return float(value)


def odds_timestamp(value):
    if not isinstance(value, str):
        raise ValueError("Invalid odds timestamp")
    return timestamp(value)


def parse_events(payload, retrieved_at):
    if not isinstance(payload, list) or len(payload) > 500:
        raise ValueError("Odds source must return a bounded event list")
    games, rejected, skipped, seen = [], [], [], set()
    for event in payload:
        try:
            key = event["id"]
            if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key) or key in seen:
                raise ValueError("Invalid or duplicate odds event ID")
            seen.add(key)
            if event["sport_key"] != "basketball_nba":
                raise ValueError("Odds event is not NBA")
            start = odds_timestamp(event["commence_time"])
            if start <= retrieved_at:
                skipped.append(dict(game_id="odds:" + key, reason="Already started"))
                continue
            home, away = event["home_team"], event["away_team"]
            if any(not isinstance(name, str) or not name.strip() or len(name) > 100 for name in (home, away)) or home == away:
                raise ValueError("Invalid matchup names")
            if not isinstance(event["bookmakers"], list) or len(event["bookmakers"]) > 100:
                raise ValueError("Invalid bookmaker list")
            books, book_ids = [], set()
            for book in event["bookmakers"]:
                try:
                    book_id = book["key"]
                    if not isinstance(book_id, str) or not re.fullmatch(r"[a-z0-9_]{1,100}", book_id) or book_id in book_ids:
                        raise ValueError("Invalid or duplicate bookmaker")
                    book_ids.add(book_id)
                    markets = [market for market in book["markets"] if market["key"] == "h2h"]
                    if len(markets) != 1:
                        raise ValueError("Expected one moneyline market")
                    market = markets[0]
                    updated = odds_timestamp(market.get("last_update") or book["last_update"])
                    if updated > retrieved_at:
                        raise ValueError("Odds timestamp is in the future")
                    outcomes = market["outcomes"]
                    if len(outcomes) != 2 or {row["name"] for row in outcomes} != {home, away}:
                        raise ValueError("Moneyline sides do not match matchup")
                    prices = {row["name"]: decimal_odds(row["price"]) for row in outcomes}
                    home_break_even, away_break_even = 1 / prices[home], 1 / prices[away]
                    total = home_break_even + away_break_even
                    books.append(dict(bookmaker=book_id, available_at=updated.isoformat(),
                        retrieved_at=retrieved_at.isoformat(), stale=retrieved_at - updated > MIN_INTERVAL,
                        home_decimal_odds=prices[home], away_decimal_odds=prices[away],
                        home_break_even_probability=home_break_even, away_break_even_probability=away_break_even,
                        home_market_probability=home_break_even / total, away_market_probability=away_break_even / total,
                        overround=total - 1))
                except (ValueError, KeyError, TypeError, IndexError):
                    rejected.append(dict(game_id="odds:" + key, scope="bookmaker", reason="Invalid or unavailable bookmaker moneyline"))
            games.append(dict(game_id="odds:" + key, commence_time=start.isoformat(), home_team=home,
                              away_team=away, bookmakers=books))
        except (ValueError, KeyError, TypeError, IndexError):
            rejected.append(dict(scope="event", reason="Invalid or duplicate NBA odds event"))
    return games, rejected, skipped


def atomic_json(path, data):
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(data, handle, indent=2)
        handle.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def collection_lock(folder):
    with (folder / ".collection.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Odds collection is already running") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def read_latest(folder, now=None):
    pointer = Path(folder) / "latest.json"
    if not pointer.exists():
        return None
    metadata = json.loads(pointer.read_text())
    filename = metadata["snapshot"]
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise ValueError("Invalid odds cache pointer")
    raw = (Path(folder) / "snapshots" / filename).read_bytes()
    if hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
        raise ValueError("Odds snapshot checksum does not match")
    snapshot = json.loads(raw)
    if now is not None:
        upcoming = []
        for game in snapshot["games"]:
            if timestamp(game["commence_time"]) <= now:
                snapshot["skipped"].append(dict(game_id=game["game_id"], reason="Started since collection"))
                continue
            for book in game["bookmakers"]:
                book["stale"] = now - timestamp(book["available_at"]) > MIN_INTERVAL
            upcoming.append(game)
        snapshot["games"] = upcoming
    return snapshot


def collect_odds(folder, env_file=PROJECT_ROOT / ".env", transport=fetch_odds, clock=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with collection_lock(folder):
        now = clock()
        latest = read_latest(folder, now)
        if latest and timedelta(0) <= now - timestamp(latest["retrieved_at"]) < MIN_INTERVAL:
            return latest, True
        ledger_path = folder / f"requests-{now:%Y-%m}.json"
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else dict(attempts=[], last_usage={})
        if len(ledger["attempts"]) >= MONTHLY_REQUEST_LIMIT or ledger["last_usage"].get("x-requests-remaining", 1) == 0:
            raise ValueError("Odds collection budget exhausted; saved snapshots were retained")
        key = load_key(env_file)
        ledger["attempts"].append(now.isoformat())
        atomic_json(ledger_path, ledger)
        payload, usage = transport(key)
        retrieved_at = clock()
        # Reject a credential even when a custom transport is used.
        if key in json.dumps(payload):
            raise ValueError("Unexpected credential in odds response; response was not saved")
        games, rejected, skipped = parse_events(payload, retrieved_at)
        ledger["last_usage"] = usage
        atomic_json(ledger_path, ledger)
        snapshot = dict(provider="the-odds-api", request_url=ENDPOINT, requested_at=now.isoformat(),
            retrieved_at=retrieved_at.isoformat(), usage=usage, games=games, rejected=rejected,
            skipped=skipped, raw_events=payload,
            limitations=["Provider IDs are not yet mapped to ESPN games.",
                         "Market probabilities remove the two-sided margin; break-even prices remain separate.",
                         "Stale prices must not be used as current market benchmarks."])
        snapshots = folder / "snapshots"
        snapshots.mkdir(exist_ok=True)
        path = snapshots / (retrieved_at.strftime("%Y%m%dT%H%M%S%fZ") + ".json")
        if path.exists():
            raise ValueError("An odds snapshot with this timestamp already exists")
        atomic_json(path, snapshot)
        atomic_json(folder / "latest.json", dict(snapshot=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        return snapshot, False
