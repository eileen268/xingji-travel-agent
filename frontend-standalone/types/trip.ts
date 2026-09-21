import type { Profile } from "@/lib/api";
import type { ContentRole, DayComposition, IndoorOutdoor, PlaceTimePreference, PreferenceKey, RecommendationRole, RecommendationScoreBreakdown, SemanticParentCategory } from "@/types/recommendation";

export type Coord = [number, number];

export type ActivityKind = "attraction" | "meal" | "hotel" | "transport";
export type TripStatus = "draft" | "completed";
export type LongDistancePreference = "agent" | "high_speed_rail" | "flight" | "train";
export type LocalTransportPreference = "transit" | "taxi" | "self_drive" | "cycling";
export type TravelTimePeriod = "morning" | "afternoon" | "evening" | "flexible";
export type TravelPace = "relaxed" | "balanced" | "intensive";
export type ActivityIntensity = 1 | 2 | 3;
export type LockReason = "user" | "confirmed_transport" | "system_boundary";
export type MealSlot = "breakfast" | "lunch" | "snack" | "dinner";
export type AccommodationEventKind = "check_in" | "check_out" | "change_hotel" | "luggage" | "pickup_luggage" | "explicit_return";

export interface AccommodationAnchor {
  anchorId: string;
  hotelId: string;
  city: string;
  latitude: number;
  longitude: number;
  checkInDate: string;
  checkOutDate: string;
}

export interface PlaceReference {
  name: string;
  city?: string;
  latitude: number;
  longitude: number;
  kind?: "airport" | "train_station" | "bus_station" | "place";
}

export interface TripBoundaryPoint {
  date: string;
  time: string;
  location: PlaceReference;
}

export interface TripBoundaryInput {
  arrival: TripBoundaryPoint;
  departure: TripBoundaryPoint;
}

export interface IntercityBoundaryInput {
  id: string;
  origin: string;
  destination: string;
  departure: TripBoundaryPoint;
  arrival: TripBoundaryPoint;
}

export interface IntercityTransportSelection {
  id: string;
  fromCity: string;
  toCity: string;
  travelDate: string;
  arrivalDate: string;
  mode: "high_speed_rail" | "pending";
  trainCode?: string;
  departureStation: PlaceReference;
  arrivalStation: PlaceReference;
  departureTime: string;
  arrivalTime: string;
  durationMinutes: number;
  price?: number;
  selectionSource: "agent" | "user";
  confirmationStatus: "agent_recommended" | "user_confirmed";
  bookingStatus: "not_booked" | "booked";
  scheduleFixed: true;
  userLocked: boolean;
  replaceable: boolean;
  providerRef: string;
  observedAt: string;
  expiresAt?: string;
  verificationStatus: "verified" | "unverified" | "expired" | "failed";
  isPlaceholder?: boolean;
}

export interface TripTransportPlan {
  arrivalBoundary: TripBoundaryPoint;
  intercitySegments: IntercityTransportSelection[];
  departureBoundary: TripBoundaryPoint;
}

export interface MealRequirement {
  slot: "breakfast" | "lunch" | "dinner";
  preferredWindow: { startMinute: number; endMinute: number };
  required: boolean;
  fulfillment?: "restaurant" | "en_route" | "hotel" | "self_arranged" | "skipped";
}

export interface InternalRouteLeg {
  id: string;
  dayId: string;
  fromNodeId: string;
  toNodeId: string;
  mode: string;
  durationMinutes: number;
  distanceKm: number;
  providerRef?: string;
  observedAt?: string;
  expiresAt?: string;
  verificationStatus: "verified" | "unverified" | "expired" | "failed";
  recommendedMode?: "walking" | "transit" | "taxi" | "driving";
  selectedMode?: "walking" | "transit" | "taxi" | "driving";
  distanceMeters?: number;
  source?: "amap" | "estimate";
  userOverridden?: boolean;
}

export interface SchedulePreference {
  slot?: MealSlot;
  preferredWindow?: { startMinute: number; endMinute: number };
  /** Verified opening/availability boundary. Unlike preferredWindow this is hard. */
  availabilityWindow?: { startMinute: number; endMinute: number };
  timePreference?: PlaceTimePreference;
}

export interface TripDestination {
  id: string;
  name: string;
  role: "primary" | "secondary" | "day_trip";
  targetTimeShare?: number;
  isAccommodationBase?: boolean;
  priorityWeight?: number;
}

export interface TravelPaceProfile {
  pace: TravelPace;
  targetActivityBlocks: { min: number; max: number };
  activeHours: { min: number; max: number };
  bufferMinutes: { min: number; max: number };
  mealMinutes: { min: number; max: number };
  maxDailyLoad: number;
  maxContinuousActivityMinutes: number;
  preferMiddayBreak: boolean;
  allowEveningActivities: boolean;
  latestNormalEndTime: string;
  endTimeIsSoftConstraint: true;
}

export interface TravelerConstraints {
  hasChildren: boolean;
  hasElderly: boolean;
  mobilityLevel: "standard" | "moderate" | "limited";
  needsFrequentBreaks: boolean;
}

export interface ActivityBlock {
  id: string;
  title: string;
  activityIds: string[];
  intensity: ActivityIntensity;
}

export interface TransportDetail {
  mode: string;
  durationMinutes: number | null;
  distanceKm: number | null;
  note?: string;
  localRouteId?: string;
  verificationStatus?: "verified" | "unverified" | "expired" | "failed";
}

export interface DayRouteSegment {
  id: string;
  fromPlaceId: string;
  toPlaceId: string;
  fromName: string;
  toName: string;
  mode: "walking" | "public_transit" | "driving";
  durationMinutes: number | null;
  distanceMeters: number | null;
  bufferMinutes: number;
  fallbackScheduleMinutes: number;
  idleMinutes: number;
  routeStatus: "verified" | "estimated_by_distance" | "unresolved";
  routeProvider?: "amap" | "deterministic_estimate" | null;
}

export interface Activity {
  coordinatesUnknown?: boolean;
  id: string;
  dayId: string;
  title: string;
  city: string;
  kind: ActivityKind;
  start: string;
  end: string;
  startAt?: string;
  endAt?: string;
  duration: string;
  cost: number;
  costStatus?: "confirmed" | "estimated" | "unknown";
  reason: string;
  coord: Coord;
  locked: boolean;
  lockReason?: LockReason;
  schedulePreference?: SchedulePreference;
  minimumDurationMinutes?: number;
  intensity?: ActivityIntensity;
  blockId?: string;
  transport?: TransportDetail;
  recommendationRole?: RecommendationRole;
  recommendationScore?: RecommendationScoreBreakdown;
  recommendationTags?: PreferenceKey[];
  contentRole?: ContentRole;
  semanticCategory?: SemanticParentCategory;
  placeId?: string;
  mustKeep?: boolean;
  userCreated?: boolean;
  stayMinutes?: number;
  preferredStartTime?: string | null;
  hardStartTime?: string | null;
  priority?: number;
  transportToNext?: "auto" | "walking" | "public_transit" | "driving" | "taxi" | null;
  mealRole?: "breakfast" | "lunch" | "dinner" | "snack" | "cafe" | "flexible" | null;
  mealWindow?: { preferredStart: string; preferredEnd: string; status: "preferred" | "acceptable" | "violation" | "not_applicable" } | null;
  mealTimingReason?: string;
  placeMatch?: { rawInput: string; provider: "amap"; confidence: number; status: "verified" | "ambiguous"; matchedAddress?: string };
  placeQualificationStatus?: "qualified" | "qualified_with_warnings";
  placeQualificationWarnings?: string[];
  longDistanceSegmentId?: string;
  accommodationAnchorId?: string;
  stayAction?: AccommodationEventKind | "depart" | "return" | "prepare";
  schedulePhase?: "pre_transfer" | "post_transfer";
}

export interface BackupPoi {
  id: string;
  name: string;
  city: string;
  category: string;
  contentRole: ContentRole;
  semanticCategory: SemanticParentCategory;
  coord: Coord;
  estimatedVisitMinutes: number;
  estimatedCost?: number;
  indoorOutdoor: IndoorOutdoor;
  physicalIntensity: ActivityIntensity;
  timePreference: PlaceTimePreference;
  primaryTags: PreferenceKey[];
  secondaryTags: PreferenceKey[];
  recommendationRole: RecommendationRole;
  scoreBreakdown?: RecommendationScoreBreakdown;
  clusterId?: string;
  reason: string;
  qualificationStatus?: "qualified" | "qualified_with_warnings";
  qualificationWarnings?: string[];
  schedulePreference?: SchedulePreference;
}

export interface TripDay {
  id: string;
  dayNumber: number;
  date: string;
  city: string;
  weather: { icon: string; label: string; low: number; high: number };
  routeColor: string;
  note: string;
  activities: Activity[];
  segments?: DayRouteSegment[];
  activityBlocks?: ActivityBlock[];
  backupPois?: BackupPoi[];
  recommendationClusterId?: string;
  destinationId?: string;
  accommodationBaseId?: string;
  accommodationAnchorId?: string;
  mealRequirements?: MealRequirement[];
  dayType?: "full" | "arrival" | "departure" | "transfer" | "day_trip" | "rest";
  dayComposition?: DayComposition;
  contentMetrics?: DayContentMetrics;
  planningObservability?: DayPlanningObservability;
}

export interface DayContentMetrics {
  availableSightseeingMinutes: number;
  discretionaryActivityCount: number;
  coreExperienceCount: number;
  boundaryOnly: boolean;
}

export interface DayPlanningObservability {
  date: string;
  dayType: TripDay["dayType"];
  earliestStart: string;
  latestSightseeingEnd: string;
  availableSightseeingMinutes: number;
  qualifiedCandidateCount: number;
  unusedCandidateCount: number;
  selectedCoreId?: string;
  droppedCandidateIds: string[];
  finalCoreExperienceCount: number;
  emptyReason?: string;
}

export interface HotelStay {
  city: string;
  hotelId?: string;
  name: string;
  address?: string;
  coord: Coord;
  pricePerNight: number;
  source: "flyai" | "amap" | "legacy";
  staySegmentId?: string;
  accommodationBaseId?: string;
  checkInAt?: string;
  checkOutAt?: string;
  totalCost?: number;
  observedAt?: string;
  expiresAt?: string;
  providerRef?: string;
  verificationStatus?: "verified" | "unverified" | "expired" | "failed";
}

export interface Budget {
  total: number;
  accommodation: number;
  food: number;
  transportation: number;
  activities: number;
  shopping: number;
  other: number;
}

export interface TransportSchedule {
  id: string;
  code: string;
  mode: "高铁" | "飞机" | "火车";
  origin: string;
  destination: string;
  departAt: string;
  arriveAt: string;
  duration: string;
  price: number;
  recommended?: boolean;
  observedAt?: string;
  expiresAt?: string;
  providerRef?: string;
  verificationStatus?: "verified" | "unverified" | "expired" | "failed";
}

export interface TransportSegment {
  id: string;
  kind: "outbound" | "intercity" | "return";
  label: string;
  origin: string;
  destination: string;
  travelDate?: string;
  selectionMode: "schedule" | "custom_time";
  selectedScheduleId?: string;
  customTime?: string;
  customTimeLabel?: "预计到达时间" | "最晚离开时间";
  schedules: TransportSchedule[];
  timelineActivityId?: string;
  observedAt?: string;
  expiresAt?: string;
  providerRef?: string;
  verificationStatus?: "verified" | "unverified" | "expired" | "failed";
  userConfirmed?: boolean;
  confirmationStatus?: "user_confirmed" | "not_confirmed";
  selectionSource?: "agent" | "user" | "legacy";
  agentConfirmationStatus?: "agent_recommended" | "user_confirmed";
  bookingStatus?: "not_booked" | "booked";
  scheduleFixed?: boolean;
  userLocked?: boolean;
  replaceable?: boolean;
  crossesMidnight?: boolean;
}

export interface TripPreferences {
  pace: TravelPace;
  interests: string[];
  budgetLevel?: "agent" | "economy" | "comfort" | "quality" | "premium";
  mustGo?: string;
  avoid?: string;
  constraints?: string;
  longDistance: LongDistancePreference;
  localTransport: LocalTransportPreference[];
  accommodation: string;
}

export interface TripDraft {
  origin: string;
  destinations: string[];
  mainDestination: string;
  destinationPlan?: TripDestination[];
  startDate: string;
  endDate: string;
  adults: number;
  children: number;
  seniors: number;
  travelStyle: string;
  budget: Budget;
  preferences: TripPreferences;
  notes: string;
  outboundPeriod: TravelTimePeriod;
  outboundMode: "schedule" | "custom_time";
  outboundScheduleId?: string;
  outboundArrival?: string;
  returnPeriod: TravelTimePeriod;
  returnMode: "schedule" | "custom_time";
  returnScheduleId?: string;
  returnDeparture?: string;
}

export interface Trip {
  profile?: Profile;
  id: string;
  title: string;
  origin: string;
  destinations: string[];
  mainDestination: string;
  destinationPlan?: TripDestination[];
  startDate: string;
  endDate: string;
  travelers: { adults: number; children: number; seniors: number; style: string };
  status: TripStatus;
  updatedAt: string;
  budget: Budget;
  preferences: TripPreferences;
  days: TripDay[];
  hotelStays?: HotelStay[];
  transportSegments: TransportSegment[];
  shared: boolean;
  shareId?: string;
  dataWarnings?: string[];
  travelTips?: string[];
  preparationItems?: Array<{ title: string; items: string[] }>;
  generatedBy?: { provider: "zhipu"; model: string; createdAt: string };
  revision?: number;
  domainModelVersion?: 1;
  actualCostSummary?: { currency: "CNY"; confirmed: number; estimated: number; unknownItemCount: number; actualTotalKnown: number; budgetDifference: number };
  unmetConstraints?: Array<{ kind: string; message: string; entityIds?: string[] }>;
  accommodationAnchors?: AccommodationAnchor[];
  internalRouteLegs?: InternalRouteLeg[];
  tripBoundary?: TripBoundaryInput;
  intercityBoundaries?: IntercityBoundaryInput[];
  transportPlan?: TripTransportPlan;
}

export interface ReplanPreview {
  title: string;
  affected: string[];
  preserved: string[];
  action: () => void;
}
