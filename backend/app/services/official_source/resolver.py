from __future__ import annotations
import re
from difflib import SequenceMatcher
from urllib.parse import urlparse
from .models import OfficialSourceCandidate,OfficialSourceDecision
from ..official.registry import OFFICIAL_SOURCE_REGISTRY

BLOCKED_DOMAINS=('trip.com','ctrip.com','dianping.com','mafengwo.cn','tripadvisor.','baike.baidu.com',
 'wikipedia.org','zhihu.com','weibo.com','xiaohongshu.com','douyin.com','bilibili.com','youtube.com','youtu.be',
 'bendibao.com','sohu.com','qq.com','163.com')
OFFICIAL_WORDS=('官网','官方网站','官方站点','official','主办单位','版权所有')
ARTICLE_PATHS=('/news/','/article/','/blog/','/travel/','/youji/','/gonglue/')
USER_HOSTED_DOMAINS=('sites.google.com','weebly.com','wixsite.com','wordpress.com','blogspot.com')
PORTFOLIO_PATHS=('/case','/cases','/project','/projects','/portfolio','/showcase')

def _text(value):return re.sub(r'[\s·•（）()\-—_|]+','',str(value or '')).casefold()
def _blocked(domain):return any(token in domain for token in BLOCKED_DOMAINS+USER_HOSTED_DOMAINS)

class OfficialSourceResolver:
    VERIFIED_THRESHOLD=.90;PROBABLE_THRESHOLD=.75
    def __init__(self):
        self.reviewed={domain:entry['canonical_name'] for entry in OFFICIAL_SOURCE_REGISTRY for domain in entry['domains']}
    def score(self,place,candidate):
        title=_text(candidate.title);snippet=_text(candidate.snippet);evidence=title+' '+snippet
        name=_text(place.get('canonical_name') or place.get('local_name'))
        title_contains=name and name in title
        if len(name)<=3:
            title_contains=bool(name and title.startswith(name))
        name_score=1.0 if title_contains else SequenceMatcher(None,name,title).ratio()
        city_score=1.0 if _text(place.get('city')) and _text(place.get('city')) in evidence else 0.0
        official_score=1.0 if any(_text(word) in title for word in OFFICIAL_WORDS) else (
            .7 if ('版权所有' in str(candidate.snippet or '') or '主办单位' in str(candidate.snippet or '')) else 0.0)
        reviewed_domain=next((domain for domain in self.reviewed if candidate.domain==domain or candidate.domain.endswith('.'+domain)),None)
        parsed=urlparse(candidate.url);path=parsed.path.casefold()
        deep_article=any(token in path for token in ARTICLE_PATHS)
        portfolio_page=any(token in path for token in PORTFOLIO_PATHS) or '案例' in str(candidate.title or '')
        domain_score=1.0 if reviewed_domain else (.9 if candidate.domain.endswith(('.gov.cn','.org.cn')) else (.75 if not deep_article else .25))
        rank_score=max(0.0,1-(candidate.rank-1)*.18)
        breakdown={'name':name_score,'city':city_score,'official_signal':official_score,'domain':domain_score,'rank':rank_score}
        score=.40*name_score+.15*city_score+.20*official_score+.15*domain_score+.10*rank_score
        if reviewed_domain and name_score>=.75:score=max(score,.96)
        strong_identity = bool(reviewed_domain) or (
            name_score >= .90 and official_score >= .70 and domain_score >= .75
        )
        if not strong_identity:score=min(score,.89)
        # A design/agency showcase may mention the complete attraction name and
        # copyright language while still being a third-party project page.
        if portfolio_page and not reviewed_domain:score=min(score,.74)
        if parsed.query and not reviewed_domain:score=min(score,.89)
        if urlparse(candidate.url).scheme!='https' and not reviewed_domain:score=min(score,.89)
        return round(min(1,score),4),{key:round(value,4) for key,value in breakdown.items()},bool(reviewed_domain)
    def resolve(self,place,candidates):
        ranked=[];rejected=[]
        for candidate in candidates:
            if _blocked(candidate.domain):
                rejected.append({'url':candidate.url,'reason':'third_party_or_content_platform','provider':candidate.provider});continue
            score,breakdown,reviewed=self.score(place,candidate)
            ranked.append((score,candidate,breakdown,reviewed))
        ranked.sort(key=lambda item:item[0],reverse=True)
        if not ranked:
            return OfficialSourceDecision(status='third_party' if rejected else 'unverified',confidence=0,rejected_candidates=rejected,evidence={'reason':'no eligible candidate'})
        score,candidate,breakdown,reviewed=ranked[0]
        rejected.extend({'url':item[1].url,'reason':'lower_score','score':item[0]} for item in ranked[1:])
        status='verified_official' if score>=self.VERIFIED_THRESHOLD else ('probable_official' if score>=self.PROBABLE_THRESHOLD else 'unverified')
        return OfficialSourceDecision(selected_url=candidate.url if status!='unverified' else None,status=status,
            confidence=score,selected_candidate=candidate if status!='unverified' else None,rejected_candidates=rejected,
            evidence={'score_breakdown':breakdown,'reviewed_domain':reviewed})
