"""Normalize polymorphic AMap JSON values into a stable internal candidate."""
from __future__ import annotations

import re
from typing import Any


def text(value: Any) -> str | None:
    if value is None or value==[]:return None
    if isinstance(value,list):
        value=next((item for item in value if item not in (None,'')),None)
    value=str(value).strip() if value is not None else ''
    return value or None


def coordinates(value: Any) -> tuple[float | None,float | None]:
    value=text(value)
    if not value:return None,None
    try:
        longitude,latitude=value.split(',',1)
        return float(latitude),float(longitude)
    except (TypeError,ValueError):
        return None,None


def number(value: Any) -> float | None:
    value=text(value)
    if not value:return None
    try:return float(value)
    except ValueError:return None


def normalize_poi(raw: dict) -> dict:
    latitude,longitude=coordinates(raw.get('location'))
    category=text(raw.get('type')) or ''
    categories=[part.strip() for part in category.split(';') if part.strip()]
    biz_ext=raw.get('biz_ext') if isinstance(raw.get('biz_ext'),dict) else {}
    business=raw.get('business') if isinstance(raw.get('business'),dict) else {}
    today=text(business.get('opentime_today') or biz_ext.get('open_time'))
    weekly=text(business.get('opentime_week'))
    tag_value=business.get('tag')
    tags=[str(item).strip() for item in (tag_value if isinstance(tag_value,list) else re.split(r'[;,，]',str(tag_value or ''))) if str(item).strip()]
    alias_value=business.get('alias')
    aliases=[str(item).strip() for item in (alias_value if isinstance(alias_value,list) else re.split(r'[;,，]',str(alias_value or ''))) if str(item).strip()]
    normalized_business={
        'opentime_today':today,'opentime_week':weekly,
        'tel':text(business.get('tel') or raw.get('tel')),
        'rating':number(business.get('rating') or biz_ext.get('rating')),
        'cost':number(business.get('cost') or biz_ext.get('cost')),
        'business_area':text(business.get('business_area')),
        'tag':tags,
    }
    return {
        'provider':'amap',
        'provider_place_id':text(raw.get('id')),
        'name':text(raw.get('name')) or '',
        'city':text(raw.get('cityname')) or '',
        'district':text(raw.get('adname')),
        'address':text(raw.get('address')),
        'latitude':latitude,
        'longitude':longitude,
        'category':category,
        'subcategory':categories[-1] if categories else None,
        'typecode':text(raw.get('typecode')),
        'adcode':text(raw.get('adcode')),
        'citycode':text(raw.get('citycode')),
        'website':text(raw.get('website')),
        'phone':normalized_business['tel'],
        'opening_hours':weekly or today,
        'opening_hours_today':today,
        'opening_hours_weekly':weekly,
        'provider_rating':normalized_business['rating'],
        'provider_cost':normalized_business['cost'],
        'business_area':normalized_business['business_area'],
        'business_tags':tags,
        'aliases':aliases,
        'business':normalized_business,
    }
