"""Seven-module contract, derived from the MIT travel-guide schema (see third_party)."""
from datetime import date
from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=3000)]
URL = Annotated[str, StringConstraints(pattern=r'^https://[^\s]+$', max_length=2000)]
Time = Annotated[str, StringConstraints(pattern=r'^([01]\d|2[0-3]):[0-5]\d$')]
CanonicalPlaceId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,127}$')]

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)

class FactSourceRecord(StrictModel):
    fact_name: Text
    value: str | bool | float | dict[str,str|None]
    source_url: URL
    source_type: Literal['official_website','official_booking','amap','trusted_third_party','llm']
    extracted_at: Text
    confidence: float = Field(ge=0,le=1)
    raw_evidence: Text
    provider_place_id: str | None = None

class ResolvedPlaceFact(StrictModel):
    value: str | bool | float | dict[str,str|None] | None
    source_url: URL | None
    source_type: Literal['official_website','official_booking','amap','trusted_third_party','llm'] | None
    extracted_at: Text | None
    confidence: float = Field(ge=0,le=1)
    raw_evidence: str
    status: Literal['verified','provider_verified','partially_verified','needs_recheck','unavailable','conflicting']
    provider_place_id: str | None = None
    sources: list[FactSourceRecord] = Field(default_factory=list,max_length=20)

class Budget(StrictModel):
    total: float = Field(ge=0, le=10000000)
    accommodation: float = Field(default=0, ge=0)
    food: float = Field(default=0, ge=0)
    transportation: float = Field(default=0, ge=0)
    activities: float = Field(default=0, ge=0)
    shopping: float = Field(default=0, ge=0)
    other: float = Field(default=0, ge=0)

    @model_validator(mode='after')
    def allocation(self):
        if sum(v for k, v in self.model_dump().items() if k != 'total') > self.total + .001:
            raise ValueError('分类预算之和不能超过总预算')
        return self

class Preferences(StrictModel):
    pace: Literal['relaxed', 'balanced', 'intensive'] = 'balanced'
    interests: list[Text] = Field(default_factory=list, max_length=20)
    budgetLevel: Literal['agent', 'economy', 'comfort', 'quality', 'premium'] = 'agent'
    mustGo: str = Field(default='', max_length=3000)
    avoid: str = Field(default='', max_length=3000)
    constraints: str = Field(default='', max_length=3000)
    longDistance: Literal['agent', 'high_speed_rail', 'flight', 'train'] = 'agent'
    localTransport: list[Literal['transit', 'taxi', 'self_drive', 'cycling']] = Field(default_factory=lambda: ['transit', 'taxi'])
    accommodation: str = Field(default='', max_length=1000)

class ConstraintProvenance(StrictModel):
    source_text: str = Field(default='', max_length=3000)
    source_type: Literal['ui','notes','llm_fallback','default']
    parser: Text
    confidence: float = Field(ge=0,le=1)

class CityStayConstraint(StrictModel):
    city: Text
    days: int = Field(ge=1,le=30)
    provenance: ConstraintProvenance

class ArrivalConstraint(StrictModel):
    date: date
    city: Text
    arrival_time: Time
    provenance: ConstraintProvenance

class DepartureConstraint(StrictModel):
    date: date
    city: Text
    departure_time: Time
    provenance: ConstraintProvenance

class TextTripConstraint(StrictModel):
    text: Text
    provenance: ConstraintProvenance

class StructuredTripConstraints(StrictModel):
    city_stay_constraints: list[CityStayConstraint] = Field(default_factory=list,max_length=8)
    arrival_constraints: list[ArrivalConstraint] = Field(default_factory=list,max_length=8)
    departure_constraints: list[DepartureConstraint] = Field(default_factory=list,max_length=8)
    must_visit_constraints: list[TextTripConstraint] = Field(default_factory=list,max_length=20)
    avoid_constraints: list[TextTripConstraint] = Field(default_factory=list,max_length=20)
    day_specific_constraints: list[TextTripConstraint] = Field(default_factory=list,max_length=20)
    pace_constraints: list[TextTripConstraint] = Field(default_factory=list,max_length=10)
    transport_constraints: list[TextTripConstraint] = Field(default_factory=list,max_length=10)
    meal_constraints: list[TextTripConstraint] = Field(default_factory=list,max_length=10)
    unparsed_notes: list[str] = Field(default_factory=list,max_length=20)
    parser_version: str = 'trip-constraints-v1'

class FallbackCityStay(StrictModel):
    city: Text
    days: int = Field(ge=1,le=30)

class FallbackArrival(StrictModel):
    date: date
    city: Text
    arrival_time: Time

class FallbackDeparture(StrictModel):
    date: date
    city: Text
    departure_time: Time

class ConstraintFallbackDecision(StrictModel):
    city_stay_constraints: list[FallbackCityStay] = Field(default_factory=list,max_length=8)
    arrival_constraints: list[FallbackArrival] = Field(default_factory=list,max_length=8)
    departure_constraints: list[FallbackDeparture] = Field(default_factory=list,max_length=8)

class BuildInput(StrictModel):
    origin: Text
    destinations: list[Text] = Field(min_length=1, max_length=8)
    mainDestination: Text
    startDate: date
    endDate: date
    adults: int = Field(ge=0, le=12, strict=True)
    children: int = Field(ge=0, le=12, strict=True)
    seniors: int = Field(ge=0, le=12, strict=True)
    travelStyle: Text
    budget: Budget
    preferences: Preferences = Field(default_factory=Preferences)
    notes: str = Field(default='', max_length=3000)
    outboundPeriod: Literal['morning', 'afternoon', 'evening', 'flexible'] = 'flexible'
    returnPeriod: Literal['morning', 'afternoon', 'evening', 'flexible'] = 'flexible'
    structuredConstraints: StructuredTripConstraints | None = None

    @property
    def days(self):
        return (self.endDate - self.startDate).days + 1

    @model_validator(mode='after')
    def consistent(self):
        if not 1 <= self.days <= 30:
            raise ValueError('日期必须按顺序，MVP 支持 1–30 天')
        if self.adults + self.children + self.seniors < 1:
            raise ValueError('至少需要一位同行人')
        if len(set(self.destinations)) != len(self.destinations) or self.mainDestination not in self.destinations:
            raise ValueError('目的地不能重复，主要目的地必须在目的地列表内')
        if len(self.destinations) > self.days:
            raise ValueError('旅行天数不能少于目的地数量')
        return self

class Place(StrictModel):
    id: CanonicalPlaceId
    type: Literal['sight', 'experience', 'restaurant', 'chain']
    city: Text
    canonical_name: Text
    local_name: Text
    english_name: Text | None
    display_name: Text
    name_source: Literal['official','map_provider','offline_reference','model_knowledge','derived']
    provider: Literal['amap','llm_generated','manual','offline_reference'] = 'llm_generated'
    provider_place_id: str | None = None
    district: str | None = None
    address: str | None = None
    latitude: float | None = Field(default=None,ge=-90,le=90)
    longitude: float | None = Field(default=None,ge=-180,le=180)
    category: str = ''
    subcategory: str | None = None
    place_type: Literal['sight','restaurant','experience','transport','hotel','generated_experience','chain'] | None = None
    source_confidence: float | None = Field(default=None,ge=0,le=1)
    source_metadata: dict = Field(default_factory=dict)
    fact_provenance: dict = Field(default_factory=dict)
    official_facts: dict[str,ResolvedPlaceFact] = Field(default_factory=dict)
    verification_status: Literal['verified','partially_verified','unverified','generated']
    entity_kind: Literal['poi','experience_concept']
    experience_title: Text | None
    linked_place_id: CanonicalPlaceId | None
    booking_status: Literal['verified_bookable','unverified','not_bookable','not_applicable']
    description: Text
    official_url: URL | None
    map_url: URL | None
    coordinates: None
    rating: None
    review_count: None
    hours_note: Text
    ticket_note: Text
    reservation_note: Text
    recheck_note: Literal['出发前请复核']
    knowledge_status: Literal['model_knowledge', 'offline_reference']
    cuisine: str
    signature_dishes: str
    experience_type: str
    semantic_tags: list[Text] = Field(default_factory=list, max_length=40)
    interest_affinity: dict[str, float] = Field(default_factory=dict)
    semantic_source: list[Text] = Field(default_factory=list, max_length=10)
    semantic_confidence: dict[str, Literal['low','medium','high']] = Field(default_factory=dict)
    affinity_reasons: dict[str, list[Text]] = Field(default_factory=dict)
    semantic_version: Text = 'interest-taxonomy-v2'
    user_created: bool = False
    custom_activity: dict | None = None
    experience_status: Literal['verified_experience','suggested_experience'] | None = None

    @model_validator(mode='after')
    def affinity_range(self):
        if any(value < 0 or value > 1 for value in self.interest_affinity.values()):
            raise ValueError('interest_affinity 必须在 0 到 1 之间')
        return self


class ResearchPlace(StrictModel):
    """Compact provider contract; deterministic facts and semantic provenance are hydrated later."""
    id: CanonicalPlaceId
    type: Literal['sight', 'experience', 'restaurant', 'chain']
    city: Text
    canonical_name: str = ''
    local_name: Text
    english_name: Text | None = None
    display_name: Text
    name_source: Literal['official','map_provider','offline_reference','model_knowledge','derived'] = 'model_knowledge'
    verification_status: Literal['verified','unverified'] = 'unverified'
    entity_kind: Literal['poi','experience_concept'] = 'poi'
    experience_title: Text | None = None
    linked_place_id: CanonicalPlaceId | None = None
    booking_status: Literal['verified_bookable','unverified','not_bookable','not_applicable'] = 'not_applicable'
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
    cuisine: str
    signature_dishes: str
    experience_type: str

    @model_validator(mode='before')
    @classmethod
    def normalize_names(cls, value):
        if isinstance(value,dict):
            from .place_names import normalize_place_names
            normalized=normalize_place_names(value)
            item=dict(value)
            for key in ('canonical_name','local_name','english_name','display_name','name_source',
                        'verification_status','entity_kind','experience_title','linked_place_id','booking_status'):
                item[key]=normalized[key]
            # Research output precedes real-world resolution; its status vocabulary
            # stays intentionally smaller than the final Canonical Place contract.
            if item['verification_status'] not in ('verified','unverified'):item['verification_status']='unverified'
            return item
        return value

class SightPlace(Place):
    type: Literal['sight']

class ExperiencePlace(Place):
    type: Literal['experience']

class RestaurantPlace(Place):
    type: Literal['restaurant']

class ChainPlace(Place):
    type: Literal['chain']

class PlaceRef(StrictModel):
    place_id: CanonicalPlaceId

class PlaceGroup(StrictModel):
    title: Text
    items: list[PlaceRef] = Field(min_length=1, max_length=30)

class Note(StrictModel):
    title: Text
    note: Text

class MenuTerm(StrictModel):
    term: Text
    meaning: Text
    ordering_note: Text

class MenuGuide(StrictModel):
    title: Text
    intro: Text
    cards: list[Note] = Field(default_factory=list, max_length=8)
    dictionary: list[MenuTerm] = Field(default_factory=list, max_length=20)

class Snack(StrictModel):
    name: Text
    description: Text
    where_to_find: Text
    ordering_note: Text

class Food(StrictModel):
    menu_guide: MenuGuide
    local_snacks: list[Snack] = Field(default_factory=list, max_length=4)
    dedicated_trip: list[PlaceRef] = Field(min_length=1, max_length=30)
    reliable_chains: list[PlaceRef] = Field(default_factory=list, max_length=4)

class ChecklistItem(StrictModel):
    id: Text
    title: Text
    note: Text

class Preparation(StrictModel):
    essentials: list[ChecklistItem] = Field(default_factory=list, max_length=60)
    confirm_ahead: list[ChecklistItem] = Field(default_factory=list, max_length=60)

    @model_validator(mode='after')
    def count(self):
        items = self.essentials + self.confirm_ahead
        if len({x.title for x in items}) != len(items) or len({x.id for x in items}) != len(items):
            raise ValueError('清单标题和ID不能重复')
        return self

class LanguageItem(StrictModel):
    text: Text
    meaning: Text
    pronunciation: str

class LanguageGroup(StrictModel):
    title: Text
    items: list[LanguageItem] = Field(min_length=1, max_length=5)

class Language(StrictModel):
    edition: Text
    keyword_groups: list[LanguageGroup] = Field(default_factory=list, max_length=5)
    phrase_groups: list[LanguageGroup] = Field(default_factory=list, max_length=5)

class TravelNote(StrictModel):
    category: Literal['weather', 'culture', 'transport', 'safety', 'payment']
    title: Text
    summary: Text
    items: list[Note] = Field(min_length=1, max_length=4)

class Sights(StrictModel):
    scheduled: list[PlaceRef]
    optional: list[PlaceRef]

class Modules(StrictModel):
    sights: Sights
    experiences: list[PlaceGroup] = Field(min_length=1, max_length=9)
    food: Food
    preparation: Preparation
    language: Language
    travel_notes: list[TravelNote] = Field(default_factory=list, max_length=5)

class Period(StrictModel):
    title: Text
    description: Text

class Periods(StrictModel):
    morning: Period
    afternoon: Period
    evening: Period

class RouteInput(StrictModel):
    origin_place_id: CanonicalPlaceId
    destination_place_id: CanonicalPlaceId
    origin_latitude: float | None = None
    origin_longitude: float | None = None
    destination_latitude: float | None = None
    destination_longitude: float | None = None
    selected_mode: Literal['walking','driving','transit']

class Stop(StrictModel):
    place_id: CanonicalPlaceId
    arrival_time: Time
    dwell_minutes: int = Field(ge=15, le=480)
    transport_mode: Text
    transfer_minutes: int | None = Field(default=None, ge=1, le=1440)
    route_minutes: int | None = Field(default=None, ge=1, le=1440)
    buffer_minutes: int = Field(default=0, ge=0, le=1440)
    fallback_schedule_minutes: int = Field(default=0, ge=0, le=1440)
    idle_minutes: int = Field(default=0, ge=0, le=1440)
    distance_km: float | None = Field(default=None, gt=0, le=5000)
    estimated_cost: None
    route_status: Literal['not_applicable','verified','estimated_by_distance','unresolved'] = 'unresolved'
    route_provider: Literal['amap','deterministic_estimate'] | None = None
    route_queried_at: str | None = None
    route_input: RouteInput | None = None
    route_error_code: str | None = None
    route_http_status: int | None = None
    route_provider_code: str | None = None
    cost_note: Text
    practical_note: Text
    time_guard: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    preferred_start_time: Time | None = None
    hard_start_time: Time | None = None
    locked: bool = False
    must_keep: bool = False
    user_created: bool = False
    note: str = ''
    priority: int = Field(default=3, ge=1, le=5)
    transport_to_next: Literal['auto','walking','public_transit','driving','taxi'] | None = None
    meal_role: Literal['breakfast','lunch','dinner','snack','cafe','flexible'] | None = None
    meal_window_preferred_start: Time | None = None
    meal_window_preferred_end: Time | None = None
    meal_window_acceptable_start: Time | None = None
    meal_window_acceptable_end: Time | None = None
    meal_window_status: Literal['preferred','acceptable','violation','not_applicable'] = 'not_applicable'
    meal_timing_penalty: int = Field(default=0,ge=0,le=10000)
    meal_timing_reason: str = ''


class RouteSegment(StrictModel):
    """Canonical transport edge between two adjacent stops.

    Stop route fields remain readable for legacy profiles, but new scheduling,
    validation and clients use this object as the route source of truth.
    """
    segment_id: Text
    from_place_id: CanonicalPlaceId
    to_place_id: CanonicalPlaceId
    mode: Literal['walking','public_transit','driving']
    distance_meters: int | None = Field(default=None, gt=0, le=5_000_000)
    duration_minutes: int | None = Field(default=None, ge=1, le=1440)
    buffer_minutes: int = Field(default=0, ge=0, le=1440)
    fallback_schedule_minutes: int = Field(default=0, ge=0, le=1440)
    idle_minutes: int = Field(default=0, ge=0, le=1440)
    route_status: Literal['verified','estimated_by_distance','unresolved'] = 'unresolved'
    route_provider: Literal['amap','deterministic_estimate'] | None = None
    route_queried_at: str | None = None
    route_input: RouteInput | None = None
    route_error_code: str | None = None
    route_http_status: int | None = None
    route_provider_code: str | None = None


class CustomActivity(StrictModel):
    activity_id: CanonicalPlaceId
    title: Text
    note: str = ''
    start_time_preference: Time | None = None
    stay_minutes: int = Field(default=60, ge=15, le=480)
    location_optional: str | None = None
    linked_place_id: CanonicalPlaceId | None = None
    user_created: Literal[True] = True

class Photo(StrictModel):
    title: Text
    note: Text

class DensityTargetRange(StrictModel):
    minimum: float = Field(ge=0,le=1)
    maximum: float = Field(ge=0,le=1)

class DayDensityEvaluation(StrictModel):
    available_minutes: int = Field(ge=0,le=1440)
    scheduled_minutes: int = Field(ge=0,le=1440)
    visit_minutes: int = Field(ge=0,le=1440)
    meal_minutes: int = Field(ge=0,le=1440)
    total_route_minutes: int = Field(ge=0,le=1440)
    fallback_schedule_minutes: int = Field(default=0,ge=0,le=1440)
    configured_buffer_minutes: int = Field(ge=0,le=1440)
    utilization: float = Field(ge=0,le=1)
    target_range: DensityTargetRange
    stop_count: int = Field(ge=0,le=30)
    attraction_count: int = Field(ge=0,le=30)
    meal_count: int = Field(ge=0,le=10)
    idle_gap_minutes: int = Field(ge=0,le=1440)
    longest_idle_gap_minutes: int = Field(ge=0,le=1440)
    fill_attempt_count: int = Field(ge=0,le=2)
    added_place_ids: list[CanonicalPlaceId] = Field(default_factory=list,max_length=2)
    remaining_candidate_count: int = Field(ge=0,le=30)
    reduced_window: bool = False
    issues: list[Text] = Field(default_factory=list,max_length=10)

class WarningRecord(StrictModel):
    """Canonical warning retained from producer through profile response."""
    code: Text
    message: Text
    severity: Literal['warning'] = 'warning'
    day: int | None = Field(default=None, ge=1, le=30)
    place_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    pointer: str | None = None
    invalid_value: Any | None = None

class Day(StrictModel):
    date: date
    city: Text
    theme: Text
    summary: Text
    periods: Periods
    stops: list[Stop] = Field(min_length=1, max_length=10)
    segments: list[RouteSegment] = Field(default_factory=list, max_length=9)
    photo_advice: list[Photo] = Field(min_length=2, max_length=3)
    derived_interests: list[Text] = Field(default_factory=list, max_length=20)
    day_interest_strength: dict[str, float] = Field(default_factory=dict)
    total_travel_minutes: int = Field(default=0, ge=0, le=1440)
    total_visit_minutes: int = Field(default=0, ge=0, le=1440)
    schedule_warnings: list[WarningRecord] = Field(default_factory=list, max_length=20)
    density_evaluation: DayDensityEvaluation | None = None
    planning_notes: list[WarningRecord] = Field(default_factory=list,max_length=20)

    @model_validator(mode='before')
    @classmethod
    def legacy_route_segments(cls,value):
        if not isinstance(value,dict):return value
        data=dict(value)
        if 'schedule_warnings' in data:
            from .warnings import dedupe_warnings
            data['schedule_warnings']=dedupe_warnings(('legacy_schedule', data.get('schedule_warnings')))
        if data.get('segments') or not data.get('stops'):return data
        segments=[];stops=data.get('stops',[])
        for previous,current in zip(stops,stops[1:]):
            raw=str(current.get('transport_mode',''))
            mode='walking' if '步行' in raw else ('public_transit' if '公共' in raw else 'driving')
            status=current.get('route_status','unresolved')
            segments.append({'segment_id':f'{previous["place_id"]}->{current["place_id"]}',
                'from_place_id':previous['place_id'],'to_place_id':current['place_id'],'mode':mode,
                'distance_meters':round(current['distance_km']*1000) if current.get('distance_km') else None,
                'duration_minutes':current.get('transfer_minutes'),'buffer_minutes':current.get('buffer_minutes',0),
                'fallback_schedule_minutes':current.get('fallback_schedule_minutes',0),'idle_minutes':current.get('idle_minutes',0),
                'route_status':'unresolved' if status=='not_applicable' else status,
                'route_provider':current.get('route_provider'),'route_queried_at':current.get('route_queried_at'),
                'route_input':current.get('route_input'),'route_error_code':current.get('route_error_code'),
                'route_http_status':current.get('route_http_status'),'route_provider_code':current.get('route_provider_code')})
        data['segments']=segments;return data

    @model_validator(mode='after')
    def day_strength_range(self):
        if any(value < 0 or value > 1 for value in self.day_interest_strength.values()):
            raise ValueError('day_interest_strength 必须在 0 到 1 之间')
        return self

class PlanDay(StrictModel):
    day: int = Field(ge=1, le=30)
    date: date
    city: Text
    area_labels: list[Text] = Field(min_length=1, max_length=2)
    primary_anchor_id: CanonicalPlaceId
    candidate_place_ids: list[CanonicalPlaceId] = Field(default_factory=list, max_length=4)
    backup_candidate_ids: list[CanonicalPlaceId] = Field(default_factory=list, max_length=4)
    fixed_meal_stop_id: CanonicalPlaceId | None = None
    intent: Text

class ItineraryPlan(StrictModel):
    days: list[PlanDay] = Field(min_length=1, max_length=30)

class PlanDecisionDay(StrictModel):
    area_labels: list[Text] = Field(min_length=1, max_length=2)
    primary_anchor_id: CanonicalPlaceId
    candidate_place_ids: list[CanonicalPlaceId] = Field(default_factory=list, max_length=4)
    backup_candidate_ids: list[CanonicalPlaceId] = Field(default_factory=list, max_length=4)
    intent: Text

class ItineraryPlanDecision(StrictModel):
    days: list[PlanDecisionDay] = Field(min_length=1, max_length=30)

class DayDecision(StrictModel):
    optional_stop_order: list[CanonicalPlaceId] = Field(default_factory=list, max_length=4)
    day_intent: Text
    practical_notes: list[Text] = Field(min_length=1, max_length=6)

class PlacesPack(StrictModel):
    places: list[Place] = Field(min_length=1, max_length=250)

class PlacesCorePack(StrictModel):
    places: list[SightPlace] = Field(min_length=1, max_length=250)

class PlacesExperiencesPack(StrictModel):
    places: list[ExperiencePlace] = Field(min_length=1, max_length=250)

class PlacesFoodPack(StrictModel):
    places: list[RestaurantPlace | ChainPlace] = Field(min_length=1, max_length=250)

class ItineraryPack(StrictModel):
    itinerary: list[Day] = Field(min_length=1, max_length=30)

class PracticalPack(StrictModel):
    experiences: list[PlaceGroup] = Field(min_length=1, max_length=9)
    food: Food
    preparation: Preparation

class LanguagePack(StrictModel):
    language: Language
    travel_notes: list[TravelNote] = Field(default_factory=list, max_length=5)

class FramingPack(StrictModel):
    preferences: BuildInput
    constraints: StructuredTripConstraints = Field(default_factory=StructuredTripConstraints)

class AllocationOwner(StrictModel):
    place_id: CanonicalPlaceId
    day: int = Field(ge=1, le=30)
    role: Literal['primary','preferred','backup','selected']
    reservation: Literal['hard','soft']
    status: Literal['reserved','selected','released']
    fixed: bool = False

class AllocationInterestState(StrictModel):
    coverage: int = Field(ge=0)
    strength: float = Field(ge=0)

class AllocationDay(StrictModel):
    day: int = Field(ge=1, le=30)
    date: date
    city: Text
    area_labels: list[Text] = Field(default_factory=list, max_length=2)
    intent: Text
    primary_anchor_id: CanonicalPlaceId
    preferred_candidate_ids: list[CanonicalPlaceId] = Field(default_factory=list, max_length=4)
    backup_candidate_ids: list[CanonicalPlaceId] = Field(default_factory=list, max_length=4)
    fixed_meal_stop_id: CanonicalPlaceId | None = None

class ItineraryAllocationPack(StrictModel):
    canonical_place_ids: list[CanonicalPlaceId]
    place_assignments: dict[CanonicalPlaceId, AllocationOwner]
    days: list[AllocationDay] = Field(min_length=1, max_length=30)
    available_place_ids: list[CanonicalPlaceId]
    released_place_ids: list[CanonicalPlaceId]
    selected_interest_ids: list[Text]
    trip_interest_state: dict[str, AllocationInterestState]

class CoherencePack(StrictModel):
    passed: Literal[True]
    errors: list[dict] = Field(default_factory=list, max_length=0)

class EnrichmentState(StrictModel):
    practical: Literal['complete','deferred']
    language_notes: Literal['complete','deferred']
    pending_packs: list[Text] = Field(default_factory=list)

class GenerationManifest(StrictModel):
    mode: Literal['REAL_LLM', 'REPLAY', 'MOCK']
    model_name: str | None
    model_version: Text
    prompt_version: Text
    pipeline_version: Text
    schema_version: Text
    validator_version: Text
    scheduler_version: Text
    allocator_version: Text
    poi_provider: Text = 'none'
    poi_resolution_version: Text = 'not-applicable'
    route_provider: Text = 'none'
    fact_verifier_version: Text = 'not-applicable'
    replan_version: Text = 'not-applicable'
    created_at: Text

class Generation(StrictModel):
    mode: Literal['llm', 'offline', 'replay', 'mock']
    degraded: bool
    warnings: list[WarningRecord]
    model: str | None
    created_at: Text
    validation_version: Literal['seven-v1']
    manifest: GenerationManifest | None = None
    diagnostics: list[WarningRecord] = Field(default_factory=list)

    @model_validator(mode='before')
    @classmethod
    def normalize_legacy_warnings(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        from .warnings import dedupe_warnings
        data['warnings'] = dedupe_warnings(('generation', data.get('warnings', [])))
        return data

class PreferenceWarning(StrictModel):
    pointer: Text
    code: Text
    message: Text
    interest_id: str | None = None
    strength: float | None = Field(default=None,ge=0,le=1)

class PreferenceEvaluation(StrictModel):
    selected_interest_ids: list[Text]
    interest_coverage: float = Field(ge=0,le=1)
    interest_strength: dict[str,float]
    interest_diversity: float = Field(ge=0,le=1)
    preference_fit_score: float = Field(ge=0,le=100)
    warnings: list[PreferenceWarning]

    @model_validator(mode='after')
    def strength_range(self):
        if any(value < 0 or value > 1 for value in self.interest_strength.values()):
            raise ValueError('interest_strength 必须在 0 到 1 之间')
        return self

class QualityWarning(StrictModel):
    pointer: Text
    code: Text
    message: Text

class FoodQualityEvaluation(StrictModel):
    recommended_scene_target: int = Field(ge=1,le=4)
    actual_scene_count: int = Field(ge=0)
    food_city_coverage: float = Field(ge=0,le=1)
    food_scene_diversity: float = Field(ge=0,le=1)
    local_food_relevance: float = Field(ge=0,le=1)
    food_interest_match: float = Field(ge=0,le=1)
    meal_schedule_coverage: float = Field(ge=0,le=1)
    food_quality_score: float = Field(ge=0,le=100)

class QualityEvaluation(StrictModel):
    food: FoodQualityEvaluation
    warnings: list[QualityWarning]

class FactVerificationWarning(StrictModel):
    pointer: Text
    code: Text
    message: Text
    day: int | None = Field(default=None,ge=1,le=30)
    place_id: CanonicalPlaceId | None = None
    metadata: dict = Field(default_factory=dict)

class FactVerificationSummary(StrictModel):
    version: Text
    verified_place_count: int = Field(ge=0)
    partially_verified_place_count: int = Field(ge=0)
    unverified_place_count: int = Field(ge=0)
    generated_experience_count: int = Field(ge=0)
    verified_place_rate: float = Field(ge=0,le=1)
    route_leg_count: int = Field(ge=0)
    verified_route_count: int = Field(ge=0)
    estimated_route_count: int = Field(ge=0)
    unresolved_route_count: int = Field(ge=0)
    verified_route_rate: float = Field(ge=0,le=1)
    real_poi_resolution_rate: float = Field(default=0,ge=0,le=1)
    scheduled_real_poi_count: int = Field(default=0,ge=0)
    resolved_real_poi_count: int = Field(default=0,ge=0)
    scheduled_real_poi_resolution_rate: float = Field(default=0,ge=0,le=1)
    ambiguous_real_poi_count: int = Field(default=0,ge=0)
    unresolved_real_poi_count: int = Field(default=0,ge=0)
    llm_place_count_in_final_itinerary: int = Field(default=0,ge=0)
    route_segment_count: int = Field(default=0,ge=0)
    fallback_route_count: int = Field(default=0,ge=0)
    route_coverage_rate: float = Field(default=0,ge=0,le=1)
    official_source_count: int = Field(ge=0)
    verified_dynamic_fact_count: int = Field(ge=0)
    conflicting_dynamic_fact_count: int = Field(ge=0)
    unavailable_dynamic_fact_count: int = Field(ge=0)
    medium_facts_needing_recheck: int = Field(ge=0)
    verified_experience_count: int = Field(default=0,ge=0)
    suggested_experience_count: int = Field(default=0,ge=0)
    generated_experience_in_schedule_count: int = Field(default=0,ge=0)
    scheduled_place_fact_coverage: float = Field(default=0,ge=0,le=1)
    official_fact_coverage: float = Field(default=0,ge=0,le=1)
    provider_fact_coverage: float = Field(default=0,ge=0,le=1)
    needs_recheck_count: int = Field(default=0,ge=0)
    scheduled_fact_details: list[dict] = Field(default_factory=list)
    hard_fact_errors: list[FactVerificationWarning]
    warnings: list[FactVerificationWarning]
    verified_at: Text

class DestinationProfile(StrictModel):
    schema_version: Literal['seven-v1']
    id: Text
    destination: Text
    display_name: Text
    country: Literal['中国']
    year: str
    trip: BuildInput
    generation: Generation
    itinerary: list[Day] = Field(min_length=1, max_length=30)
    places: list[Place] = Field(min_length=1, max_length=250)
    module_groups: Modules
    enrichment: EnrichmentState | None = None
    preference_evaluation: PreferenceEvaluation
    quality_evaluation: QualityEvaluation
    fact_verification: FactVerificationSummary

PACK_MODELS = {
    'framing': FramingPack,
    'places-core': PlacesCorePack,
    'places-experiences': PlacesExperiencesPack,
    'places-food': PlacesFoodPack,
    'itinerary-plan': ItineraryPlan,
    'itinerary-allocation': ItineraryAllocationPack,
    'itinerary-coherence': CoherencePack,
    'modules-practical': PracticalPack,
    'modules-language-notes': LanguagePack,
    'destination-profile': DestinationProfile,
}

def pack_model(pack_id: str):
    if pack_id.startswith('itinerary-day-'):
        return Day
    return PACK_MODELS.get(pack_id)
