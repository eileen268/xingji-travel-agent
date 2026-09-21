#!/usr/bin/env python3
"""Early, local research-pack contract check with conservative syntax repair."""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "assets" / "research-pack-contract.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

PACKS = {
    "framing": (dict, ("destination","display_name","country","trip","cover","transport","stays","render_bindings_file")),
    "itinerary": (list, ()), "places-core": (dict, ("sights","support")),
    "places-shopping": (dict, ("shops","souvenirs")), "places-experiences": (list, ()),
    "places-food": (list, ()), "modules-discovery": (dict, ("shopping","experiences")),
    "modules-practical": (dict, ("food","preparation")),
    "modules-language-notes": (dict, ("language","travel_notes")),
}

def pointer(parts):
    return "/" + "/".join(str(p).replace("~","~0").replace("/","~1") for p in parts)

def safe_text_repair(text):
    value = text.lstrip("\ufeff").strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", value, re.I | re.S)
    if fence: value = fence.group(1)
    return re.sub(r",\s*([}\]])", r"\1", value)

def required(obj, fields, base=()):
    errors=[]
    for field in fields:
        if field not in obj or obj[field] in (None,"",[],{}):
            errors.append({"pointer":pointer((*base,field)),"code":"required","message":"missing or empty required field"})
    return errors

def present(obj, fields, base=()):
    return [{"pointer":pointer((*base,field)),"code":"required","message":"missing required field"}
            for field in fields if field not in obj]

def required_paths(obj, paths, base=()):
    errors=[]
    for raw in paths:
        value=obj; parts=raw.split("."); missing=False
        for part in parts:
            if not isinstance(value,dict) or part not in value:
                missing=True; break
            value=value[part]
        if missing or value in (None, "", {}):
            errors.append({"pointer":pointer((*base,*parts)),"code":"required","message":"missing required contract path"})
    return errors

def validate(pack_id, data):
    errors=[]; expected, fields = PACKS.get(pack_id, (None,()))
    if expected and not isinstance(data, expected):
        return [{"pointer":"/","code":"container_type","message":f"expected {expected.__name__}, got {type(data).__name__}"}]
    if isinstance(data, dict): errors += required(data, fields)
    if pack_id == "framing" and isinstance(data,dict):
        errors += required_paths(data, CONTRACT["packs"]["framing"]["required_paths"])
        trip=data.get("trip"); cover=data.get("cover"); transport=data.get("transport")
        if isinstance(trip,dict): errors += present(trip,("start_date","end_date","days","rhythm","travelers","interests","constraints"),("trip",))
        if isinstance(cover,dict): errors += required(cover,("kicker","title","summary","image","tags"),("cover",))
        if isinstance(transport,dict): errors += present(transport,("status","legs"),("transport",))
        if not isinstance(data.get("stays"),list) or not data.get("stays"):
            errors.append({"pointer":"/stays","code":"count","message":"expected at least one stay status record"})
    if pack_id == "itinerary" and isinstance(data, list):
        for i, day in enumerate(data):
            if not isinstance(day, dict): errors.append({"pointer":pointer((i,)),"code":"item_type","message":"day must be object"}); continue
            errors += required(day, ("date","theme","summary","periods","stops"),(i,))
            errors += required_paths(day, CONTRACT["packs"]["itinerary"]["item_required_paths"], (i,))
            periods=day.get("periods")
            if isinstance(periods,dict): errors += required(periods,("morning","afternoon","evening"),(i,"periods"))
    if pack_id.startswith("places-"):
        families = data.values() if isinstance(data,dict) else [data]
        for family in families:
            if not isinstance(family,list): continue
            for i, place in enumerate(family):
                if not isinstance(place,dict): continue
                errors += required(place,("id","type","display_name","map_query","source_url"),(i,))
    if pack_id == "modules-discovery" and isinstance(data,dict):
        for key in ("shopping","experiences"):
            if key in data and not isinstance(data[key],list): errors.append({"pointer":pointer((key,)),"code":"container_type","message":"expected array of groups"})
        experiences=data.get("experiences")
        if isinstance(experiences,list):
            option_count=sum(len(group.get("items",[])) for group in experiences if isinstance(group,dict))
            experience_mode=str(data.get("experience_mode","standard")).lower()
            if experience_mode=="standard" and (len(experiences)!=3 or option_count!=6): errors.append({"pointer":"/experiences","code":"count","message":f"Standard mode expects exactly 3 groups and 6 options, got {len(experiences)} groups and {option_count} options"})
            if experience_mode=="constrained" and (len(experiences)!=2 or option_count!=4): errors.append({"pointer":"/experiences","code":"count","message":f"Constrained mode expects exactly 2 groups and 4 options, got {len(experiences)} groups and {option_count} options"})
            if experience_mode=="expanded" and (len(experiences)<3 or option_count<6): errors.append({"pointer":"/experiences","code":"count","message":f"Expanded mode expects at least 3 groups and 6 options, got {len(experiences)} groups and {option_count} options"})
    if pack_id == "modules-practical" and isinstance(data,dict):
        for key in ("food","preparation"):
            if key in data and not isinstance(data[key],dict): errors.append({"pointer":pointer((key,)),"code":"container_type","message":"expected object"})
        food=data.get("food")
        if isinstance(food,dict):
            errors += required(food,("menu_guide","menu_primer","local_snacks","dedicated_trip","reliable_chains"),("food",))
            guide=food.get("menu_guide")
            if isinstance(guide,dict):
                errors += required(guide,("kicker","title","intro","cards"),("food","menu_guide"))
                cards=guide.get("cards")
                if isinstance(cards,list) and len(cards)<4: errors.append({"pointer":"/food/menu_guide/cards","code":"count","message":f"expected at least 4 cards, got {len(cards)}"})
    if pack_id == "modules-language-notes" and isinstance(data,dict):
        language=data.get("language")
        if isinstance(language,dict):
            for family_name in ("keyword_groups","phrase_groups","english_keyword_groups","english_phrase_groups"):
                for i,group in enumerate(language.get(family_name,[]) if isinstance(language.get(family_name,[]),list) else []):
                    if isinstance(group,dict) and not re.search(r"[\u3400-\u9fff]",str(group.get("title",""))):
                        errors.append({"pointer":pointer(("language",family_name,i,"title")),"code":"language","message":"group title must be Chinese; bilingual text belongs inside cards"})
        notes=data.get("travel_notes")
        if isinstance(notes,list) and len(notes)!=5: errors.append({"pointer":"/travel_notes","code":"count","message":f"expected exactly 5 groups, got {len(notes)}"})
        for i, group in enumerate(notes if isinstance(notes,list) else []):
            items=group.get("items") if isinstance(group,dict) else None
            if isinstance(items,list) and len(items)!=4: errors.append({"pointer":pointer(("travel_notes",i,"items")),"code":"count","message":f"expected exactly 4 topics, got {len(items)}"})
    return errors

def scaffold(pack_id):
    place = {"id": None, "type": None, "display_name": None, "local_name": None, "english_name": None, "map_query": None, "source_url": None, "description": None, "hours": None, "closed_days": [], "latitude": None, "longitude": None, "images": []}
    shapes = {
        "framing": {"destination": None, "display_name": None, "country": None, "year": None, "trip": {"start_date": None, "end_date": None, "days": None, "rhythm": None, "travelers": None, "interests": [], "constraints": []}, "cover": {"kicker": None, "title": None, "summary": None, "image": None, "tags": []}, "transport": {"status": "pending", "legs": []}, "stays": [{"status": "pending", "place_id": None, "check_in": None, "check_out": None, "notes": None}], "render_bindings_file": "render-bindings.json"},
        "itinerary": [{"date": None, "theme": None, "summary": None, "periods": {"morning": {"title": None, "description": None}, "afternoon": {"title": None, "description": None}, "evening": {"title": None, "description": None}}, "stops": [{"place_id": None, "arrival_time": None, "dwell_minutes": None, "transport_mode": None, "transfer_minutes": None, "distance_km": None, "estimated_cost": None, "practical_note": None, "time_guard": None}], "shopping_advice": {"title": None, "description": None, "url": None, "link_label": None}, "photo_advice": {"title": None, "lighting": None, "suitable_shots": [], "portrait_tip": None, "shooting_plan": []}}],
        "places-core": {"sights": [{**place, "type": "sight", "visit_duration": None, "scheduled_label": None}], "support": []},
        "places-shopping": {"shops": [{**place, "type": "shop", "category": None, "buying_tip": None, "brand_highlights": []}], "souvenirs": [{**place, "type": "souvenir", "why_buy": None, "best_for": None, "where_to_buy": None, "buying_tip": None}]},
        "places-experiences": [{**place, "type": "experience", "experience_type": None, "category": None, "scheduled_label": None, "hazardous": False}],
        "places-food": [{**place, "type": "restaurant", "cuisine": None, "signature_dishes": [], "price_per_person": None}],
        "modules-discovery": {"experience_mode": "standard", "shopping": [{"title": None, "subtitle": None, "items": [{"place_id": None}]}], "experiences": [{"title": None, "items": [{"place_id": None}]}]},
        "modules-practical": {"food": {"menu_guide": {"kicker": None, "title": None, "intro": None, "cards": []}, "menu_primer": [], "local_snacks": [], "dedicated_trip": [], "reliable_chains": []}, "preparation": {"essentials": [], "confirm_ahead": []}},
        "modules-language-notes": {"language": {"keyword_groups": [], "phrase_groups": [], "english_keyword_groups": [], "english_phrase_groups": []}, "travel_notes": []},
    }
    return shapes[pack_id]

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("pack_id", nargs="?", choices=sorted(PACKS)); p.add_argument("path",nargs="?",type=Path); p.add_argument("--scaffold",choices=sorted(PACKS)); p.add_argument("--output",type=Path); p.add_argument("--repair-safe",action="store_true"); p.add_argument("--errors",type=Path); args=p.parse_args()
    if args.scaffold:
        rendered=json.dumps(scaffold(args.scaffold),ensure_ascii=False,indent=2)+"\n"
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(rendered,encoding="utf-8"); print(f"SCAFFOLD WROTE {args.scaffold}: {args.output}")
        else: print(rendered,end="")
        return 0
    if not args.pack_id or not args.path: p.error("pack_id and path are required unless --scaffold is used")
    raw=args.path.read_text(encoding="utf-8-sig")
    try: data=json.loads(raw); repaired=False
    except json.JSONDecodeError as first:
        candidate=safe_text_repair(raw)
        try: data=json.loads(candidate)
        except json.JSONDecodeError:
            report={"pack_id":args.pack_id,"path":str(args.path),"errors":[{"pointer":"/","code":"json_syntax","message":str(first)}]}
            if args.errors: args.errors.parent.mkdir(parents=True,exist_ok=True); args.errors.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
            print(f"PACK INVALID {args.pack_id}: JSON syntax"); return 2
        repaired=True
        if args.repair_safe: args.path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    errors=validate(args.pack_id,data); report={"pack_id":args.pack_id,"path":str(args.path),"safe_syntax_repair_available":repaired and not args.repair_safe,"errors":errors}
    if args.errors: args.errors.parent.mkdir(parents=True,exist_ok=True); args.errors.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if errors: print(f"PACK INVALID {args.pack_id}: {len(errors)} errors; first={errors[0]['pointer']} {errors[0]['message']}"); return 2
    print(f"PACK VALID {args.pack_id}" + ("; safe syntax repaired" if repaired and args.repair_safe else "")); return 0

if __name__ == "__main__": sys.exit(main())
