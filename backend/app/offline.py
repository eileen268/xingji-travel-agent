"""Authored Hangzhou knowledge fallback. No live prices, hours, ratings or coordinates."""
from datetime import timedelta
from .models import BuildInput
from .validators import map_url
from .place_names import normalize_place_names

RECHECK = '出发前请复核'
SIGHTS = [
 ('西湖湖滨','沿湖滨步道观察湖面与城市的关系，选择相邻的一段慢行即可。带老人或儿童时就近休息，不必为了绕湖而连续步行。'),
 ('断桥残雪','从断桥看白堤与湖岸展开的层次，适合结合北山街安排短距离漫步。桥面拥挤时先让行，不在桥中央停留拍摄。'),
 ('浙江省博物馆孤山馆区','以孤山馆区为文化停靠点，了解浙江历史与西湖文化的联系。展览及馆区开放安排可能调整，先核对具体展厅再决定参观路线。'),
 ('岳王庙','围绕岳飞纪念建筑了解杭州的人文叙事，参观时注意碑刻与院落的关系。纪念场所保持安静，具体入口和开放安排以公告为准。'),
 ('曲院风荷','在曲院风荷选择临水步道观察园林布局与水面倒影，适合与西湖北岸同区安排。荷花具有季节性，不把花期景观作为全年保证。'),
 ('花港观鱼','结合西湖南侧路线走访园林与水边步道，选择可随时折返的一段。不要投喂或攀越护栏，带儿童时先约定集合地点。'),
 ('雷峰塔','从雷峰塔一带理解西湖南岸的景观层次，也可只安排外围步行。登塔条件和收费需要另行确认，不默认老人儿童都适合登高。'),
 ('中国丝绸博物馆','通过丝绸与服饰相关展览理解杭州的工艺传统，适合作为天气不佳时的文化备选。先核对当期展览与开放馆舍，按体力选择展区。'),
 ('中国茶叶博物馆双峰馆区','从茶叶与茶文化展示认识杭州和龙井茶的关系，参观以展陈和周边环境为主。不要默认提供现场制茶课程或试饮，活动需单独确认。'),
 ('西溪国家湿地公园','选择西溪湿地的一片区域观察水网与植被，不把整个湿地压缩成匆忙打卡。入口、游线及船行安排需核对，雨天注意湿滑路面。'),
]
RESTAURANTS = [
 ('楼外楼孤山路店','传统杭帮菜','东坡肉、龙井虾仁','位于孤山一带的传统杭帮菜餐饮选择，可与孤山文化路线衔接。选择这里是为了理解杭州宴席菜式，具体供应与排队状况仍需到店前确认。'),
 ('知味观仁和路总店','小吃点心','小笼、猫耳朵','湖滨附近的传统点心选择，适合把正餐缩小为按食量点选的小吃组合。同行人对甜咸偏好不同可分别选择，菜单与门店营业需复核。'),
 ('奎元馆解放路总店','传统面馆','片儿川、虾爆鳝面','以杭州面食为主题的一餐，可以与城市中心的步行安排结合。汤面适合作为清晰的用餐停靠，鳝鱼或虾等配料过敏者应先与店员确认。'),
 ('新白鹿龙游路店','家常杭菜','蛋黄鸡翅、糖醋排骨','选择家常菜作为同行人共享的一餐，适合在湖滨北侧活动后按实际体力前往。菜式可能变化，先看当前菜单再按人数点菜，避免用套餐价格推算预算。'),
 ('杭州酒家延安路店','杭州风味正餐','东坡肉、宋嫂鱼羹','在延安路一带安排杭州风味正餐，适合把地方菜与城市日常生活放在同一天认识。先确认鱼类、肉类和调味偏好，再决定实际点菜组合。'),
 ('咬不得高祖生煎高银街店','生煎简餐','生煎、汤品','以生煎作为南宋御街周边活动时的简便用餐候选，适合不想安排长时间正餐的时段。刚出锅的馅汁可能烫口，应放凉后小口食用并复核门店状态。'),
]
EXPERIENCES = [
 ('湖畔与园林','nature',[('西湖湖滨慢行','沿湖滨选择一段可折返的步道，停下观察湖面与城市交界。它适合摄影和亲子同行，也便于按体力随时结束；不要求环湖，不承诺特定天气或日落景观。'),('西溪湿地观察','在西溪湿地已开放的步道观察水边植物与鸟类，保持距离，不投喂野生动物。这个选项适合自然兴趣者，是否乘船另行确认，不把船班和票价当作已知事实。')]),
 ('博物馆里的杭州','culture',[('孤山馆区文化参观','到浙江省博物馆孤山馆区了解当地文化，以当期实际开放展厅为准。先选最感兴趣的主题，再根据同行人的注意力与体力扩展参观，不假定有固定讲解场次。'),('丝绸文化参观','在中国丝绸博物馆观察服饰、材料与工艺的关系，适合文化和设计兴趣者。参观以展览为主，不默认包含手工课程；儿童可先寻找不同纹样，再由同行成人帮助理解。')]),
 ('茶与面食','food_workshop',[('双峰馆区茶文化学习','在中国茶叶博物馆双峰馆区通过展陈了解茶的类别和制作背景，适合想认识龙井文化的初访者。这里的体验是自主参观学习，试饮和课程是否提供需事先确认。'),('奎元馆杭州面食体验','在奎元馆解放路总店尝试理解片儿川等杭州面食的配料组合，点餐前询问食材。这里的体验是用餐观察，不是烹饪课程；对海鲜或其他食材敏感的同行人应另选合适菜品。')]),
]
EXPERIENCE_LINKS={
    '西湖湖滨慢行':'s0','西溪湿地观察':'s9','孤山馆区文化参观':'s2',
    '丝绸文化参观':'s7','双峰馆区茶文化学习':'s8','奎元馆杭州面食体验':'r2',
}

def make_place(pid, kind, name, description, **extra):
    place=dict(id=pid, type=kind, city='杭州', display_name=name, local_name=name,
                description=description, official_url=None, map_url=map_url('杭州', name),
                coordinates=None, rating=None, review_count=None,
                hours_note='营业与开放安排可能调整，出发前请复核',
                ticket_note='费用与适用条件以现场或官方说明为准，出发前请复核',
                reservation_note='是否需要预约及预约渠道，出发前请复核',
                recheck_note=RECHECK, knowledge_status='offline_reference', cuisine='',
                signature_dishes='', experience_type='', **{}) | extra
    place['name_source']='offline_reference'
    return normalize_place_names(place)

def language():
    words = [
      ('地名与方位',[('湖滨','西湖东侧临湖区域'),('孤山','西湖北侧的人文游览区域'),('白堤','连接西湖北部景观的堤岸'),('南山路','西湖东南侧道路名称'),('双峰','茶文化馆区所在片区名称')]),
      ('面食与点心',[('片儿川','杭州常见的一种汤面'),('虾爆鳝面','含虾与鳝鱼的面食，过敏者先询问'),('猫耳朵','形似猫耳的面点'),('小笼','带馅蒸制点心，注意馅汁温度'),('葱包桧','杭州传统街头小吃名称')]),
      ('交通与入口',[('进站口','进入车站的入口'),('出站口','离开车站的出口'),('换乘','从一条线路转往另一条线路'),('步行入口','适合步行进入的入口'),('无障碍通道','有需要时优先询问的通行路径')]),
      ('点餐与支付',[('少辣','希望减少辣味'),('忌口','不能吃或不愿吃的食材'),('分餐','希望分开食用或使用公筷'),('账单','确认消费明细'),('打包','把剩余食物妥善带走')]),
      ('参观与求助',[('预约','提前登记参观或用餐'),('闭馆','场馆暂停对外开放'),('寄存','暂时存放随身物品'),('服务台','向工作人员咨询的位置'),('集合点','走散后按约定会合的位置')])]
    phrases = [
      ('问路',[('请问步行入口在哪里？','请工作人员指明入口'),('这里可以从哪边回到湖滨？','确认返程方向'),('最近的地铁入口怎么走？','问清入口再导航'),('有没有不用走楼梯的路线？','表达无障碍需求'),('这个地点是在同一个馆区吗？','避免走错场馆')]),
      ('点餐',[('这道菜包含哪些食材？','确认主要原料'),('我对这些食材过敏，请帮我确认。','展示自己的过敏信息'),('可以少辣少油吗？','询问是否能够调整'),('这一份适合几个人分享？','根据实际食量点餐'),('请帮我看看账单明细。','核对消费内容')]),
      ('交通',[('这班车经过这个站吗？','上车前确认方向'),('请问应该在哪一站换乘？','确认换乘位置'),('我们有老人，想少走一点路。','说明同行需求'),('这里能安全上下车吗？','确认上下车位置'),('请按这个地图地点导航。','用完整名称减少误会')]),
      ('参观',[('今天哪些展厅开放？','确认实际开放范围'),('这个展览需要提前预约吗？','询问预约要求'),('这里可以拍照吗？','先确认拍摄规则'),('附近哪里可以坐下休息？','主动安排休息'),('请问行李可以在哪里寄存？','了解寄存条件')]),
      ('需要帮助',[('我们走散了，请帮忙联系同行人。','向现场工作人员求助'),('我不舒服，需要找工作人员帮忙。','表达身体不适'),('我的手机没有电了，能帮我联系家人吗？','请求联系协助'),('请把地点名称写下来。','保留可出示的信息'),('我没有听清楚，请慢一点说。','请求清晰沟通')])]
    def groups(rows): return [dict(title=title, items=[dict(text=a, meaning=b, pronunciation='') for a,b in entries]) for title,entries in rows]
    return dict(edition='普通话与杭州当地用语', keyword_groups=groups(words), phrase_groups=groups(phrases))

def notes():
    rows = [
      ('weather','天气', [('湖边风雨','西湖岸边风雨会直接影响步行舒适度，季节印象不能代替当天预报。出发前请复核天气，并把室内参观留作雨天备选。'),('湿地路面','西溪湿地的户外步道在降雨后可能湿滑，鞋底抓地力会影响体验。出发前请复核开放情况，选择防滑鞋并减少连续步行。'),('室内备选','孤山与丝绸相关馆区可以作为天气变化时的文化候选，但并非随时开放。出发前请复核馆区公告，再交换当天的户外活动。'),('衣物调整','杭州的季节变化和室内外温差需要结合出行日期判断，不能预先给出确定温度。出发前请复核预报，以便穿脱的衣物安排为主。')]),
      ('culture','文化礼仪',[('纪念场所','岳王庙等纪念场所适合安静参观，拍摄时避免挡住他人视线和通道。先阅读现场说明，对限制拍摄的区域主动收起相机。'),('展厅秩序','博物馆展厅需要照顾其他观众的参观体验，儿童也可以先了解规则。进入展厅前确认拍摄要求，把讨论和休息安排在合适位置。'),('湖岸共享','西湖岸边既是游览空间也是市民日常生活的地方，窄路上停留会影响通行。合影时先让出通道，拍摄陌生人前征得对方同意。'),('茶文化学习','茶文化展示可以帮助理解地方生活，但不同活动的接待方式并不一致。参观前说明自己的兴趣，试饮或讲解安排以现场回复为准。')]),
      ('transport','交通',[('湖区慢行','西湖周边可以按片区步行游览，但地图上的相邻地点不等于体力消耗很小。先选择短段路线，同行人疲劳时就近折返或休息。'),('车站边界','杭州的出发和到达地点需要用完整站名确认，不应仅凭城市名称判断。按自己的已购票信息核对车站，并预留进出站缓冲。'),('湿地入口','西溪湿地的入口与所选游线相关，错误入口可能造成额外折返。出发前请复核目的入口，把完整地点名称交给导航或司机。'),('换乘判断','公共交通线路和道路通行情况会变化，离线行程不能提供实时换乘结论。出发前请复核导航结果，按实际情况选择公交或打车。')]),
      ('safety','安全',[('临水活动','西湖与湿地游览常靠近水边，拍摄倒影时容易忽略脚下和护栏。与岸边保持安全距离，照看儿童并避免为了取景翻越围挡。'),('走散预案','湖滨和热门场馆可能出现人流集中，同行人应提前约定可识别的集合点。把联系方式保存在可离线查看的位置，走散时先联系工作人员。'),('饮食沟通','地方菜和点心可能含有不容易从名称判断的配料，不能凭菜名排除过敏风险。点餐时明确说明忌口，请店员确认后再决定是否食用。'),('体力安排','老人和儿童的连续步行耐受不同，完成所有停靠点不应成为固定要求。出现疲劳就减少活动，身体不适时及时向现场人员寻求帮助。')]),
      ('payment','支付',[('核对金额','不同门店的支付方式和账单构成可能不同，菜单上的描述也可能调整。付款前核对实际金额与消费明细，不按攻略估计价格结账。'),('备用方式','手机网络或电量不足可能影响扫码支付，旅途中应保留合适的备用方式。出发前确认自己可用的支付工具，避免全部依赖一部手机。'),('预约渠道','景点与场馆的预约入口可能变化，陌生链接不应直接当作官方渠道使用。出发前请复核官网或现场公告，再提交必要的预约信息。'),('保留凭证','门票与餐饮消费的确认信息有助于核对订单，临时调整时尤其需要留存。保存实际支付凭证和商家回复，离店前确认金额没有重复扣取。')])]
    return [dict(category=k,title=t,summary=f'围绕杭州的{t}安排做出具体选择，结合同行人体力和实际情况调整，出发前请复核。',items=[dict(title=a,note=b) for a,b in entries]) for k,t,entries in rows]

def make_packs(t: BuildInput):
    if any(city not in ('杭州','杭州市') for city in t.destinations):
        raise ValueError('离线完整档案仅覆盖杭州；请配置智谱后生成其他城市')
    city=t.destinations[0]
    places=[make_place(f's{i}', 'sight',n,d) for i,(n,d) in enumerate(SIGHTS)]
    places += [make_place(f'r{i}','restaurant',n,d,cuisine=c,signature_dishes=s) for i,(n,c,s,d) in enumerate(RESTAURANTS)]
    groups=[]
    for i,(title,kind,items) in enumerate(EXPERIENCES):
        ids=[]
        for j,(name,desc) in enumerate(items):
            pid=f'e{i}-{j}'; ids.append(dict(place_id=pid)); places.append(make_place(
                pid,'experience',name,desc,experience_type=kind,entity_kind='experience_concept',
                linked_place_id=EXPERIENCE_LINKS[name],booking_status='unverified'))
        groups.append(dict(title=title,items=ids))
    for i,name in enumerate(['老娘舅','新丰小吃']):
        places.append(make_place(f'c{i}','chain',name,f'{name}作为杭州用餐时的连锁备选，仅在附近有营业门店且菜单适合同行人时使用。具体分店不在离线档案中指定，请按当天位置核对，不为寻找门店额外绕行。',cuisine='日常简餐',signature_dishes='按实际门店菜单选择'))
    for p in places:
        p['city']=city
    by_place_id={p['id']:p for p in places}
    for p in places:
        target=by_place_id.get(p.get('linked_place_id'))
        if p['entity_kind']=='experience_concept' and not target:p['map_url']=None
        else:p['map_url']=map_url(city,target['display_name'] if target else p['display_name'])
    packing=['身份证件','证件电子备份','手机充电线','充电宝','防滑步行鞋','便携雨具','适合日期的外套','饮水杯','纸巾湿巾','个人常用药','离线联系方式','轻便随身包']
    booking=['抵达车站核对','返程地点核对','住宿地址核对','馆区开放核对','展览预约核对','景点入口核对','湿地游线核对','餐厅营业核对','用餐忌口确认','天气预报复核','支付方式确认','同行集合点确认']
    prep=dict(essentials=[dict(id=f'pack-{i}',title=x,note=f'结合杭州步行与{t.adults+t.children+t.seniors}位同行人的实际需要准备{x}，随身物品以便携为主。') for i,x in enumerate(packing)], confirm_ahead=[dict(id=f'confirm-{i}',title=x,note=f'按本次杭州行程逐项完成{x}；使用实际订单或官方信息，出发前请复核。') for i,x in enumerate(booking)])
    snacks=[('葱包桧','薄饼裹入葱段等食材后压制的小吃，口感和配料以实际摊店为准。'),('定胜糕','米粉蒸制的传统糕点，适合少量尝试，甜度与馅料应现场询问。'),('西湖藕粉','以藕粉冲调的小食，稠度与添加配料不同，点单时说明个人偏好。'),('猫耳朵','形似猫耳的面点，可搭配不同汤底和配料，适合了解杭州点心。')]
    food=dict(menu_guide=dict(title='先认菜名，再按食量点餐',intro='杭州面食、点心与杭帮菜可以分餐体验。菜单供应、食材和价格以实际门店为准，出发前请复核。',cards=[dict(title=a,note=b) for a,b in [('先问配料','片儿川、虾爆鳝面等名称不能覆盖所有配料信息。点餐前说明过敏与忌口，请店员逐项确认后再选择。'),('点心分量','小笼、糕点和面食的份量随店家变化，不用固定人数套用套餐。先少量点选，再按同行人的食量补充。'),('地方菜搭配','杭帮菜可以结合鱼虾、肉类和蔬菜共同点选，不必一餐尝遍所有招牌。依据同行人的口味选择，并确认实际供应。'),('排队与备选','热门门店是否排队要以当天情况为准，不承诺立即入座。等待影响下一个活动时，就近选择已确认营业的替代门店。')]],dictionary=[dict(term=n,meaning=d,ordering_note='确认配料、份量与实际供应后再点单。') for n,d in [('片儿川','杭州汤面的一种'),('虾爆鳝面','含虾和鳝鱼的面食'),('宋嫂鱼羹','杭州传统鱼羹菜式'),('龙井虾仁','以茶香搭配虾仁的菜式')]]),local_snacks=[dict(name=n,description=d,where_to_find='杭州传统小吃店或点心店，按当天营业情况选择。',ordering_note='先询问配料与份量，价格出发前请复核。') for n,d in snacks],dedicated_trip=[dict(place_id=f'r{i}') for i in range(6)],reliable_chains=[dict(place_id=f'c{i}') for i in range(2)])
    routes=[([0,1],1),([2,3],0),([4,5],3),([6,7],4),([8,9],2)]
    days=[]
    by_id={p['id']:p for p in places}
    for i in range(t.days):
        sights,restaurant=routes[i%len(routes)]
        ids=[f's{sights[0]}',f'r{restaurant}',f's{sights[1]}']
        times=['09:30','12:00','14:30']
        if i==0 and t.outboundPeriod=='afternoon': times=['14:00','16:00','18:00']
        if i==0 and t.outboundPeriod=='evening': times=['19:00','20:15','21:30']
        if i==t.days-1 and t.returnPeriod=='morning': ids=ids[:1]; times=['07:00']
        if i==t.days-1 and t.returnPeriod=='afternoon': ids=ids[:2]
        stops=[dict(place_id=pid,arrival_time=times[j],dwell_minutes=45 if t.preferences.pace=='relaxed' else 60,transport_mode='按当天导航选择，出发前请复核',transfer_minutes=None,distance_km=None,estimated_cost=None,cost_note='费用未知，出发前请复核',practical_note=f'在{by_id[pid]["display_name"]}先确认实际入口与接待情况，再决定参观或用餐；同行人疲劳时缩短停留。',time_guard='建议排队超过二十分钟就放弃等待，先确保下一项安排有缓冲。') for j,pid in enumerate(ids)]
        names=[by_id[x]['display_name'] for x in ids]
        days.append(dict(date=(t.startDate+timedelta(days=i)).isoformat(),city=city,theme=' · '.join(names),summary=f'以{names[0]}为这一日的起点，结合{names[-1]}安排文化、湖岸与用餐。时间均为建议，实际交通和开放需复核。',periods={k:dict(title=label,description=f'{(t.startDate+timedelta(days=i)).isoformat()} {label}围绕{names[min(j,len(names)-1)]}按实际抵离时间与体力安排，无法衔接时优先休息。') for j,(k,label) in enumerate([('morning','上午安排'),('afternoon','下午安排'),('evening','晚间收尾')])},stops=stops,photo_advice=[dict(title=f'{names[0]}的环境记录',note='站在不挡路的位置，以平视视角保留周围环境；人多时先让行，拍摄前看清现场规定。'),dict(title=f'{names[-1]}的旅行细节',note='选择允许拍摄的细节作为画面主体，不打扰用餐或参观的人；未经同意不近距离拍摄陌生人。')]))
    return {'places-core':dict(places=places),'itinerary':dict(itinerary=days),'modules-practical':dict(experiences=groups,food=food,preparation=prep),'modules-language-notes':dict(language=language(),travel_notes=notes())}
