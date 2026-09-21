import type { Trip, Activity } from "@/types/trip";
export type Note = { title:string; text:string };
type CityNotes = { experiences:Note[]; food:Note[]; words:Note[]; tips:Note[] };
const notes:Record<string,CityNotes> = {
  杭州:{experiences:[{title:"西湖慢行",text:"沿湖看山水与桥影，把一段湖边时光留给散步。"},{title:"茶香里的杭州",text:"在茶馆认识龙井茶，体验闻香、观色与慢慢品茶。"}],food:[{title:"片儿川",text:"雪菜、笋片与肉片搭配的杭州面食，可作为一顿简便午餐。"},{title:"葱包桧",text:"薄饼包着油条和葱，适合散步途中尝一小份。"}],words:[{title:"片儿川",text:"杭州常见的雪菜笋片肉片汤面。"},{title:"龙井",text:"既是茶名，也出现在村落与地名中，问路时说清目的地。"}],tips:[{title:"寺院参观礼仪",text:"进入寺院保持安静，拍摄前查看标识，不打扰礼佛者。"}]},
  绍兴:{experiences:[{title:"坐一程乌篷船",text:"从水面看石桥与老街，出发前确认天气与船班。"}],food:[{title:"茴香豆",text:"绍兴常见的小食，可以搭配一杯茶慢慢尝。"},{title:"梅干菜烧肉",text:"梅干菜香浓，点餐时可询问分量与咸淡。"}],words:[{title:"乌篷船",text:"带黑色篷顶的传统小船。"},{title:"咸亨",text:"绍兴常见的文化地标名称，找店时核对具体地址。"}],tips:[{title:"老街也是居民的家",text:"经过民居放低音量，拍摄居民与院落前先征得同意。"}]},
  宁波:{experiences:[{title:"江畔看城市入夜",text:"在老外滩沿江慢走，感受港城的建筑与江景。"}],food:[{title:"宁波汤圆",text:"糯米外皮包裹甜馅，小份品尝，也可先确认馅料。"},{title:"年糕",text:"可尝汤年糕或炒年糕，点单时说明喜欢的做法。"}],words:[{title:"老外滩",text:"宁波的江畔历史街区，叫车时注明宁波。"},{title:"天一阁",text:"以藏书文化闻名的参观地点。"}],tips:[{title:"藏书与展览空间",text:"遵守展厅的摄影规定，不触摸展品，保持安静。"}]},
  象山:{experiences:[{title:"影视场景漫游",text:"在影视城的街巷与建筑间漫步，留意当天现场活动安排。"}],food:[{title:"海鲜面",text:"点单前确认海鲜种类、分量和价格。"}],words:[{title:"石浦",text:"象山的渔港地名，出发前确认是否在当天路线内。"}],tips:[{title:"尊重拍摄与作业区域",text:"不进入封闭片场和港口作业区域，按现场指引通行。"}]},
  兰州:{experiences:[{title:"黄河边喝三炮台",text:"找一处河岸茶座坐下，在茶香里慢慢看黄河。"},{title:"黄河游船",text:"从水面看桥梁和两岸山城，班次与开放情况需当天确认。"}],food:[{title:"牛肉面",text:"点单时选择面型，牛肉可另点；辣椒和香菜按口味说明。"},{title:"牛奶鸡蛋醪糟",text:"热乎的地方甜食，点单前可询问配料。"},{title:"甜醅",text:"当地发酵谷物小食，先尝小份，留意个人饮食禁忌。"}],words:[{title:"二细 / 毛细",text:"牛肉面的面型：中等偏细 / 很细的圆面。"},{title:"韭叶 / 大宽",text:"韭菜叶宽的扁面 / 宽面带。"},{title:"单切",text:"另点一份熟牛肉。"},{title:"中山桥",text:"也常被叫作黄河铁桥。"},{title:"中川机场",text:"兰州机场，出行时核对完整地点。"}],tips:[{title:"尊重清真餐饮习惯",text:"进入标示清真的餐厅，尊重店内饮食要求，自带食品饮品前先问店员。"},{title:"寺观先看再举机",text:"参观时穿着得体，遇礼拜、法事或禁拍标识，收起相机并保持安静。"},{title:"黄河边不越护栏",text:"拍照留在开放步道内，不靠近水边或越过护栏。"}]},
  巴厘岛:{experiences:[{title:"巴厘式按摩",text:"给行程留一段放松时间，体验前说明力度偏好与不适部位。"}],food:[{title:"印尼炒饭",text:"可尝当地炒饭，点单时说明辣度和饮食要求。"}],words:[{title:"Terima kasih",text:"谢谢。"},{title:"Tidak pedas",text:"不辣。"}],tips:[{title:"尊重宗教空间",text:"进入寺庙遵循现场着装与参观指引，不打扰仪式。"}]},
};
export function cityNotes(trip:Trip) {
  return [...new Set([...trip.destinations,...trip.days.map(day=>day.city)])].map(city=>({city,notes:notes[city]??{experiences:[],food:[],words:[],tips:[]}}));
}
export function mapLink(item:Pick<Activity,"title"|"city"|"coord">) {
  const [lat,lon]=item.coord;
  return lat&&lon ? `https://uri.amap.com/marker?position=${lon},${lat}&name=${encodeURIComponent(item.title)}&coordinate=gaode&callnative=0` : `https://uri.amap.com/search?keyword=${encodeURIComponent(item.title)}&city=${encodeURIComponent(item.city)}&callnative=0`;
}
export const sightNotes:Record<string,string>={
  西湖:"湖面、堤岸与远山构成杭州的山水画卷。沿湖步行，可以从桥影、树荫与水面倒影中感受不同的景致。",
  灵隐寺:"灵隐寺坐落在杭州山林之间，古寺与周围山石林木相映，适合放慢脚步感受寺院建筑与清幽环境。",
  鲁迅故里:"围绕鲁迅生活与文学记忆展开的历史街区，可以在故居、街巷与展陈中认识绍兴文化。",
  沈园:"绍兴的古典园林，将江南庭院与陆游、唐琬的文学故事联系在一起。乌篷船体验需另行确认登船点。",
  天一阁:"以藏书文化闻名的历史建筑群，园林、院落和展陈共同呈现宁波深厚的文脉。",
  老外滩:"宁波江畔的历史街区，沿江建筑与开阔水面适合慢行，白天与夜晚各有氛围。",
  象山影视城:"以影视场景与主题建筑为特色，可以在不同片区间游览，感受镜头背后的场景空间。",
  宁波博物院:"通过展览了解宁波的地方历史与海洋文化，也可以留意建筑本身的材料与空间。",
  中山桥:"兰州辨识度很高的黄河地标，适合步行观赏钢桁架、河流与白塔山的层次。",
};
