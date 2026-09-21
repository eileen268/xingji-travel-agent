import { TripWorkspace } from "@/components/trip/trip-workspace";

export default async function TripPage({ params }: { params: Promise<{ tripId: string }> }) {
  const { tripId } = await params;
  return <TripWorkspace tripId={tripId} />;
}
