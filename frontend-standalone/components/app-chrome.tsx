"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { House, MapTrifold, Plus, SuitcaseRolling } from "@phosphor-icons/react";
import clsx from "clsx";
import { useTripStore } from "@/store/trip-store";

export function Brand({ compact = false }: { compact?: boolean }) {
  return <Link href="/" className={clsx("brand", compact && "brand-compact")} aria-label="行迹首页"><span className="brand-cn">行迹</span>{!compact && <span className="brand-en">Journey Notes</span>}</Link>;
}

export function AppHeader({ minimal = false }: { minimal?: boolean }) {
  const pathname = usePathname();
  const currentTripId = useTripStore((state) => state.currentTripId);
  return <header className="app-header"><Brand />{!minimal && <nav aria-label="主导航">
    <Link className={pathname === "/" ? "active" : ""} href="/"><House size={18} />灵感</Link>
    <Link className={pathname === "/create" ? "active" : ""} href="/create"><Plus size={18} />新旅程</Link>
    <Link className={pathname.startsWith("/trip/") ? "active" : ""} href={`/trip/${currentTripId}`}><MapTrifold size={18} />当前行程</Link>
    <Link className={pathname === "/trips" ? "active" : ""} href="/trips"><SuitcaseRolling size={18} />我的行程</Link>
  </nav>}</header>;
}

export function PaperPage({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <main className={`paper-page ${className}`}>
    <div className="paper-botanical-layer" aria-hidden="true">
      <span className="paper-botanical paper-botanical-flower" />
      <span className="paper-botanical paper-botanical-leaves" />
      <span className="paper-botanical paper-botanical-cloud" />
    </div>
    {children}
  </main>;
}
