import { SharedTrip } from "@/components/share/shared-trip";
export default async function Page({params}:{params:Promise<{shareId:string}>}){const {shareId}=await params;return <SharedTrip shareId={shareId}/>;}
