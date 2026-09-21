import { AirplaneTilt, Bicycle, Bus, Car, MapPin, PersonSimpleWalk, Subway, Taxi, Train } from "@phosphor-icons/react";

export function TransportModeIcon({ mode, size = 14 }:{ mode?:string; size?:number }) {
  const value=(mode ?? "").toLowerCase();
  const props={size,weight:"duotone" as const,"aria-hidden":true};
  if(value.includes("飞机")||value.includes("航空")) return <AirplaneTilt {...props}/>;
  if(value.includes("地铁")) return <Subway {...props}/>;
  if(value.includes("公交")||value.includes("巴士")) return <Bus {...props}/>;
  if(value.includes("高铁")||value.includes("动车")||value.includes("火车")||value.includes("铁路")) return <Train {...props}/>;
  if(value.includes("步行")||value.includes("徒步")) return <PersonSimpleWalk {...props}/>;
  if(value.includes("骑行")||value.includes("自行车")) return <Bicycle {...props}/>;
  if(value.includes("打车")||value.includes("出租")) return <Taxi {...props}/>;
  if(value.includes("自驾")||value.includes("包车")||value.includes("汽车")) return <Car {...props}/>;
  return <MapPin {...props}/>;
}
