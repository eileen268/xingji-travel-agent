from __future__ import annotations

import re
from html import unescape
from html.parser import HTMLParser
from typing import Any,Literal

from pydantic import BaseModel,ConfigDict,Field

from .fetcher import FetchedOfficialPage


class _Text(HTMLParser):
    def __init__(self):super().__init__();self.parts=[];self.skip=0
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'):self.skip+=1
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript') and self.skip:self.skip-=1
    def handle_data(self,data):
        if not self.skip and data.strip():self.parts.append(data.strip())


def page_text(html):
    parser=_Text();parser.feed(html or '')
    return re.sub(r'\s+',' ',unescape(' '.join(parser.parts))).strip()


class FactCandidate(BaseModel):
    model_config=ConfigDict(extra='forbid')
    fact_name: str
    value: str|bool|float|dict[str,str|None]
    source_url: str
    source_type: Literal['official_website','official_booking','amap','trusted_third_party','llm']
    extracted_at: str
    confidence: float=Field(ge=0,le=1)
    raw_evidence: str
    provider_place_id: str|None=None


def _evidence(text,match,radius=100):
    return text[max(0,match.start()-radius):min(len(text),match.end()+radius)][:500]


class FactExtractor:
    TIME_RE=re.compile(r'(?<!\d)([0-2]?\d[:：][0-5]\d)\s*(?:[-—–~～至到])\s*([0-2]?\d[:：][0-5]\d)')
    PHONE_RE=re.compile(r'(?:(?:\+?86[- ]?)?0\d{2,3}[- ]?\d{7,8}|400[- ]?\d{3}[- ]?\d{4})')

    def extract(self,page: FetchedOfficialPage,official_url: str) -> list[FactCandidate]:
        if not page.content:return []
        text=page_text(page.content);facts=[];source='official_booking' if page.page_type in ('ticket','reservation') else 'official_website'
        def add(name,value,evidence,confidence=.9,url=None):
            facts.append(FactCandidate(fact_name=name,value=value,source_url=url or page.final_url,
                source_type=source,extracted_at=page.fetched_at,confidence=confidence,raw_evidence=evidence[:500]))
        if page.page_type=='homepage':add('official_url',official_url,text[:200] or official_url,.98,official_url)
        times=list(self.TIME_RE.finditer(text))
        if times:
            values=list(dict.fromkeys(f'{m.group(1).replace("：",":")}-{m.group(2).replace("：",":")}' for m in times))
            add('opening_hours','；'.join(values),_evidence(text,times[0]),.9)
        ticket=re.search(r'([^。！？]{0,80}(?:票价|门票|购票|免费开放)[^。！？]{0,180}[。！？]?)',text)
        if ticket:add('ticket_info',ticket.group(1).strip(),ticket.group(1).strip(),.88)
        negative=re.search(r'(?:免预约|无需预约|散客免预约|不需要预约)',text)
        positive=re.search(r'(?:须|需|必须|提前).{0,10}预约|预约购票|实名制购票',text)
        if negative and not positive:add('reservation_required',False,_evidence(text,negative),.9)
        elif positive and not negative:add('reservation_required',True,_evidence(text,positive),.9)
        elif positive and negative:
            # Mixed rules usually mean different visitor or exhibition scopes.
            add('reservation_required','部分项目或时段需要预约',_evidence(text,positive),.75)
        if page.page_type=='reservation' and re.search(r'预约|购票',text):add('reservation_url',page.final_url,text[:250],.85)
        phone=self.PHONE_RE.search(text)
        if phone:add('phone',phone.group(0),_evidence(text,phone),.9)
        notice=re.search(r'([^。！？]{0,50}(?:参观须知|游客须知|重要提示|临时关闭|停止入场)[^。！？]{0,180}[。！？]?)',text)
        if notice:add('important_notice',notice.group(1).strip(),notice.group(1).strip(),.85)
        return facts
