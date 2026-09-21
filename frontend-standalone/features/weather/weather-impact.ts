import type { TripDay } from "@/types/trip";

type DayWeather = TripDay["weather"];

export function weatherImpactNote(weather: DayWeather) {
  const temperature = `${weather.low} 至 ${weather.high}℃`;
  const condition = weather.label.trim();

  if (!condition || /待更新|未知/.test(condition)) {
    return "天气预报暂不可用，临近出发时请刷新天气，并根据实时情况调整衣物与户外活动。";
  }
  if (/雷|暴雨|大雨/.test(condition)) {
    return `预计${condition}，${temperature}。建议优先安排室内活动，减少长时间户外停留，并为交通预留更多缓冲。`;
  }
  if (/雨|雪/.test(condition)) {
    return `预计${condition}，${temperature}。建议携带雨具或防滑装备，户外活动留意临时关闭与路面情况。`;
  }
  if (/高温|炎热/.test(condition) || weather.high >= 32) {
    return `预计${condition}，${temperature}。午后注意防晒补水，可把户外活动尽量放在上午或傍晚。`;
  }
  if (/晴/.test(condition)) {
    return `预计${condition}，${temperature}。适合户外游览，仍建议做好防晒，并根据早晚温差增减衣物。`;
  }
  return `预计${condition}，${temperature}。整体可按计划出行，建议临近出发时再次确认逐小时天气。`;
}
