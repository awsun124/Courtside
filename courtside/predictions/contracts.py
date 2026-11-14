"""Internal record shapes for the prediction pipeline.

These are type contracts, not runtime validators or an external API schema.
Provider adapters must validate timestamps, probability ranges, identifiers,
and data availability before constructing records. See docs/predictions.md.
"""

from datetime import datetime
from typing import Literal, TypedDict


class PregamePlayer(TypedDict):
    game_id: str
    player_id: str
    team_id: str
    available_at: datetime
    source: str
    availability: Literal["available", "questionable", "out", "unknown"]
    projected_minutes: float


class MoneylineSnapshot(TypedDict):
    game_id: str
    bookmaker: str
    available_at: datetime
    retrieved_at: datetime
    home_decimal_odds: float
    away_decimal_odds: float


class PredictionSnapshot(TypedDict):
    game_id: str
    cutoff_at: datetime
    generated_at: datetime
    model_version: str
    feature_version: str
    home_win_probability: float
    data_quality: Literal["complete", "limited"]
    warnings: list[str]
    mode: Literal["historical_reconstruction", "live"]
