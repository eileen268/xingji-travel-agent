from __future__ import annotations

from .models import GooglePlaceCandidate


def normalize_place(raw: dict) -> GooglePlaceCandidate:
    location=raw.get('location') if isinstance(raw.get('location'),dict) else {}
    display=raw.get('displayName') if isinstance(raw.get('displayName'),dict) else {}
    return GooglePlaceCandidate(
        google_place_id=str(raw.get('id') or ''),display_name=str(display.get('text') or ''),
        formatted_address=raw.get('formattedAddress'),latitude=location.get('latitude'),
        longitude=location.get('longitude'),types=list(raw.get('types') or []),
        website_uri=raw.get('websiteUri'),regular_opening_hours=raw.get('regularOpeningHours'),
        national_phone_number=raw.get('nationalPhoneNumber'),
        international_phone_number=raw.get('internationalPhoneNumber'),
        business_status=raw.get('businessStatus'))
