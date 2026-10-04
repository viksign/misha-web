import json
from urllib.parse import unquote


def cookie_preferences(request):
    value = request.COOKIES.get('misha_cookie_consent')
    if not value:
        return None
    try:
        preferences = json.loads(unquote(value))
    except (TypeError, ValueError):
        return None
    if not isinstance(preferences, dict):
        return None
    return {
        'analytics': preferences.get('analytics') is True,
        'advertising': preferences.get('advertising') is True,
    }


def has_cookie_consent(request, category):
    preferences = cookie_preferences(request)
    return bool(preferences and preferences.get(category))