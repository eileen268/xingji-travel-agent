"use client";

import dynamic from "next/dynamic";
import type { Trip } from "@/types/trip";

const TripMapInner = dynamic(() => import("./trip-map-inner").then((module) => module.TripMapInner), { ssr: false, loading: () => <div className="map-loading"><span /><p>正在铺开地图</p></div> });

export function TripMap(props: { trip: Trip; activeDayId: string | "overview"; selectedActivityId: string | null; onSelect: (id: string) => void }) {
  return <TripMapInner {...props} />;
}
