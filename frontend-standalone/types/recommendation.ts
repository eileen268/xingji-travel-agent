export type PreferenceKey = "culture" | "nature" | "food" | "photography" | "family" | "museum" | "light_outdoor" | "shopping";
export type IndoorOutdoor = "indoor" | "outdoor" | "mixed";
export type PlaceTimePreference = "morning" | "daytime" | "sunset" | "evening" | "night" | "flexible";
export type RecommendationRole = "must_see" | "preference_match" | "route_filler" | "weather_backup" | "optional";
export type ContentRole = "attraction" | "dining" | "experience" | "shopping" | "accommodation" | "transport";
export type SemanticParentCategory = "attraction" | "culture" | "nature" | "dining" | "shopping" | "entertainment";

export interface DayComposition {
  date: string;
  city: string;
  areaId: string;
  primaryAnchorId?: string;
  secondaryPlaceIds: string[];
  diningPlaceIds: string[];
  optionalEveningPlaceId?: string;
  backupPlaceIds: string[];
  combinationScore: number;
  isLightDay: boolean;
  minimumCoreExperiences: number;
  emptyReason?: string;
}

export interface RecommendationScoreBreakdown {
  preferenceFit: number;
  destinationDistinctiveness: number;
  areaRouteFit: number;
  practicalInformationConfidence: number;
  timeCost: number;
  quality: number;
  locationEfficiency: number;
  weatherFit: number;
  paceFit: number;
  timeFit: number;
  budgetFit: number;
  diversityContribution: number;
  finalScore: number;
}
