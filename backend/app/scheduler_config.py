"""Single source of truth for deterministic scheduler pace and density targets."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PaceConfig:
    initial_optional_limit: int
    sight_minutes: int
    experience_minutes: int
    restaurant_minutes: int
    route_buffer_minutes: int
    target_utilization_min: float
    target_utilization_max: float
    max_major_stops: int
    max_idle_gap_minutes: int
    max_fill_detour_meters: int


@dataclass(frozen=True)
class MealWindow:
    preferred_start: int
    preferred_end: int
    acceptable_start: int
    acceptable_end: int


def _minutes(value: str) -> int:
    hour,minute=map(int,value.split(':'))
    return hour*60+minute


MEAL_WINDOWS={
    'breakfast':MealWindow(_minutes('07:00'),_minutes('09:30'),_minutes('06:30'),_minutes('10:30')),
    'lunch':MealWindow(_minutes('11:30'),_minutes('13:30'),_minutes('11:00'),_minutes('14:00')),
    'dinner':MealWindow(_minutes('17:30'),_minutes('19:30'),_minutes('17:00'),_minutes('20:30')),
    'snack':MealWindow(_minutes('14:00'),_minutes('17:00'),_minutes('00:00'),_minutes('23:59')),
    'cafe':MealWindow(_minutes('14:00'),_minutes('17:00'),_minutes('00:00'),_minutes('23:59')),
    'flexible':MealWindow(_minutes('00:00'),_minutes('23:59'),_minutes('00:00'),_minutes('23:59')),
}

MEAL_TIMING_PENALTY_OUTSIDE_ACCEPTABLE=1000


PACE_CONFIG={
    'relaxed':PaceConfig(2,105,90,75,12,.55,.70,3,150,5000),
    'balanced':PaceConfig(3,90,75,60,10,.65,.80,4,120,7000),
    'intensive':PaceConfig(4,75,60,50,8,.75,.90,5,90,9000),
}

MAX_FILL_ITERATIONS=2
# Extra time reserved when an add-place feasibility check has to use a
# coordinate-based route estimate instead of a verified provider route.
ADD_PLACE_ESTIMATED_ROUTE_SAFETY_MARGIN_MINUTES=15
# Missing coordinates cannot produce a route duration. This value is used only
# for internal time placement; the persisted/displayed route duration remains null.
UNRESOLVED_ROUTE_SCHEDULE_MINUTES=30
# Arrival/departure constraints describe terminal times, not activity times.
# Keep each component visible and configurable for later product tuning.
ARRIVAL_LOCAL_TRANSFER_MINUTES=45
ARRIVAL_CHECKIN_BUFFER_MINUTES=30
ARRIVAL_ACTIVITY_BUFFER_MINUTES=15
DEPARTURE_LOCAL_TRANSFER_MINUTES=45
DEPARTURE_TERMINAL_BUFFER_MINUTES=45
FILL_SCORE_WEIGHTS={
    'interest_match':35.0,
    'marginal_preference_gain':20.0,
    'area_fit':15.0,
    'signature_bonus':10.0,
    'gap_fit':15.0,
    'detour_penalty':20.0,
    'repetition_penalty':8.0,
    'meal_timing_penalty':40.0,
}
