import { PreparationAgentPage } from "@/components/preparation/preparation-agent-page";
export default async function Page({params}:{params:Promise<{tripId:string}>}) { const {tripId}=await params; return <PreparationAgentPage tripId={tripId}/>; }
