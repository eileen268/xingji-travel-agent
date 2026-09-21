"""Deterministic content budgets derived from time available in each city."""


def sights_for_city(stay_days: int) -> int:
    if stay_days <= 2:return 6
    if stay_days <= 5:return min(10,stay_days*2)
    return min(18,stay_days*2)


def experiences_for_city(stay_days: int) -> int:
    if stay_days <= 2:return 3
    if stay_days <= 5:return min(6,stay_days+1)
    return min(9,stay_days+2)


def restaurants_for_city(stay_days: int) -> int:
    if stay_days <= 2:return 4
    return 6


def enrichment_budget(days: int) -> dict[str,int]:
    if days <= 2:
        return {'language_groups':3,'language_items':3,'travel_note_categories':3,'travel_note_items':2,
                'menu_cards':2,'menu_terms':2,'snacks':2,'checklist':12}
    return {'language_groups':5,'language_items':5,'travel_note_categories':5,'travel_note_items':4,
            'menu_cards':4,'menu_terms':4,'snacks':4,'checklist':24}
