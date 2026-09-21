export interface ChinaCityOption {
  name: string;
  province: string;
  keywords?: string[];
}

const regions: Array<[string, string[]]> = [
  ["北京市", ["北京"]],
  ["天津市", ["天津"]],
  ["河北省", ["石家庄", "唐山", "秦皇岛", "邯郸", "保定", "张家口", "承德", "廊坊"]],
  ["山西省", ["太原", "大同", "晋中", "临汾", "运城"]],
  ["内蒙古自治区", ["呼和浩特", "包头", "赤峰", "鄂尔多斯", "呼伦贝尔"]],
  ["辽宁省", ["沈阳", "大连", "鞍山", "丹东", "锦州", "营口"]],
  ["吉林省", ["长春", "吉林", "延边", "延吉", "白山"]],
  ["黑龙江省", ["哈尔滨", "齐齐哈尔", "牡丹江", "佳木斯", "漠河"]],
  ["上海市", ["上海"]],
  ["江苏省", ["南京", "苏州", "无锡", "常州", "扬州", "镇江", "南通", "徐州", "连云港"]],
  ["浙江省", ["杭州", "宁波", "温州", "嘉兴", "湖州", "绍兴", "金华", "衢州", "舟山", "台州", "丽水", "义乌", "象山"]],
  ["安徽省", ["合肥", "黄山", "芜湖", "安庆", "宣城", "池州"]],
  ["福建省", ["福州", "厦门", "泉州", "漳州", "莆田", "南平", "龙岩"]],
  ["江西省", ["南昌", "景德镇", "九江", "赣州", "上饶", "婺源"]],
  ["山东省", ["济南", "青岛", "烟台", "威海", "潍坊", "济宁", "泰安", "日照"]],
  ["河南省", ["郑州", "洛阳", "开封", "安阳", "新乡", "焦作", "南阳"]],
  ["湖北省", ["武汉", "宜昌", "襄阳", "荆州", "恩施", "十堰"]],
  ["湖南省", ["长沙", "张家界", "湘西", "凤凰", "岳阳", "衡阳", "郴州"]],
  ["广东省", ["广州", "深圳", "珠海", "佛山", "东莞", "惠州", "中山", "汕头", "潮州", "湛江", "肇庆"]],
  ["广西壮族自治区", ["南宁", "桂林", "柳州", "北海", "崇左", "百色"]],
  ["海南省", ["海口", "三亚", "琼海", "万宁", "陵水", "儋州"]],
  ["重庆市", ["重庆"]],
  ["四川省", ["成都", "乐山", "绵阳", "德阳", "宜宾", "泸州", "广元", "阿坝", "甘孜", "都江堰"]],
  ["贵州省", ["贵阳", "遵义", "安顺", "黔东南", "黔南", "铜仁"]],
  ["云南省", ["昆明", "大理", "丽江", "西双版纳", "迪庆", "香格里拉", "腾冲", "普洱"]],
  ["西藏自治区", ["拉萨", "日喀则", "林芝", "山南", "阿里"]],
  ["陕西省", ["西安", "宝鸡", "咸阳", "延安", "汉中", "榆林"]],
  ["甘肃省", ["兰州", "敦煌", "嘉峪关", "张掖", "天水", "甘南"]],
  ["青海省", ["西宁", "海东", "海北", "海南州", "海西"]],
  ["宁夏回族自治区", ["银川", "中卫", "吴忠", "固原"]],
  ["新疆维吾尔自治区", ["乌鲁木齐", "喀什", "伊犁", "吐鲁番", "阿勒泰", "克拉玛依", "库尔勒", "和田"]],
];

export const chinaCityOptions: ChinaCityOption[] = regions.flatMap(([province, cities]) => cities.map((name) => ({ name, province })));

function normalize(value: string) {
  return value.trim().toLocaleLowerCase("zh-CN").replace(/[省市自治区特别行政区壮族回族维吾尔]+$/g, "");
}

export function searchChinaCities(query: string, exclude: string[] = [], limit = 12) {
  const needle = normalize(query);
  if (!needle) return [];
  const excluded = new Set(exclude.map(normalize));
  return chinaCityOptions
    .filter((item) => !excluded.has(normalize(item.name)))
    .filter((item) => normalize(item.name).includes(needle) || normalize(item.province).includes(needle) || item.keywords?.some((keyword) => normalize(keyword).includes(needle)))
    .sort((a, b) => Number(normalize(a.name) !== needle) - Number(normalize(b.name) !== needle) || a.name.length - b.name.length)
    .slice(0, limit);
}
