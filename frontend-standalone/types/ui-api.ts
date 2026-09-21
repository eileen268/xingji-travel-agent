import type { PlaceReference, TripBoundaryInput, TripDestination, TripDraft } from "@/types/trip";

export interface HotelOption {
  id: string;
  name: string;
  address: string;
  city?: string;
  latitude: number;
  longitude: number;
  score?: number;
  review?: string;
  star?: string;
  nearby?: string;
  pricePerNightCny?: number;
  imageUrl?: string;
  detailUrl?: string;
  source: "flyai" | "amap";
}

export interface PlanningContext {
  version: 1;
  createdAt: string;
  draft: TripDraft;
  destinations: Array<{
    city: string;
    stay: { checkInDate: string; checkOutDate: string };
    hotels: HotelOption[];
    planning: TripDestination;
    accommodationBaseCity: string;
  }>;
  destinationPlan: TripDestination[];
  dayAssignments: string[];
  boundaryDefaults: {
    arrival: PlaceReference;
    departure: PlaceReference;
    intercity: Array<{ id: string; origin: string; destination: string; travelDate: string }>;
  };
  warnings: Array<{ scope: string; message: string }>;
}

export interface PlanningConfirmation {
  boundaries: TripBoundaryInput;
  hotelSelections: Record<string, string>;
}

export interface ManualChangeImpact {
  affectedDays: string[];
  updatedRoutes: string[];
  shiftedActivities: string[];
  verifiedPlaces: string[];
  warnings: string[];
}

export interface HotelChangeImpact {
  city: string;
  previousHotel: string;
  nextHotel: string;
  nights: number;
  budgetDelta: number;
  routeChanges: Array<{ dayId: string; dayLabel: string; leg: string; beforeMinutes: number; afterMinutes: number; deltaMinutes: number }>;
  shiftedActivities: string[];
  addedActivities: string[];
  warnings: string[];
}
