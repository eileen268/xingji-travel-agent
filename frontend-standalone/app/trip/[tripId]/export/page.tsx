import Link from "next/link";
import { AppHeader, PaperPage } from "@/components/app-chrome";
import { ExportStudio } from "@/components/export/export-studio";
export default async function Page({params}:{params:Promise<{tripId:string}>}) { const {tripId}=await params; return tripId==="demo-trip"?<ExportStudio tripId={tripId}/>:<><AppHeader/><PaperPage><div className="export-wrap"><h1 className="display-title">导出行程</h1><p>后续开放：本轮支持生成与阅读。</p><Link className="back-link" href={`/trip/${tripId}`}>返回行程</Link></div></PaperPage></>; }
