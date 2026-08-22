"""Minimal REST API for external integrations.

Returns media data authenticated via User.token.
"""

import logging

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_not_required
from django.db.models import Q
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from app.models import MediaTypes

logger = logging.getLogger(__name__)

User = get_user_model()

# Map numeric status codes (used by info-page) to Yamtrack status strings.
STATUS_MAP = {
    "1": "In progress",
    "2": "Completed",
    "3": "Planning",
    "4": "Paused",
    "5": "Dropped",
}


def _authenticate(request):
    """Authenticate via Authorization header with a User.token value.

    Supports both ``Token <token>`` and ``Bearer <token>`` schemes.
    Returns the authenticated User or None.
    """
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if not auth_header:
        return None

    parts = auth_header.split(" ", 1)
    if len(parts) != 2:
        return None

    scheme, token = parts
    if scheme.lower() not in ("token", "bearer"):
        return None

    token = token.strip()
    if not token:
        return None

    try:
        return User.objects.get(token=token)
    except User.DoesNotExist:
        logger.debug("API auth failed for token: %s...", token[:8])
        return None


@login_not_required
@require_GET
def media_list(request):
    """Return a paginated list of media items for the authenticated user.

    Query parameters:
        media_type – tv, movie, anime, manga, etc.
        status     – numeric (1-5) or string status filter
        limit      – max items to return (default 20, max 100)
        offset     – number of items to skip

    Response format (compatible with info-page watchlist.ts):
        {
            "results": [
                {
                    "progress": 5,
                    "progressed_at": "2024-01-01T00:00:00Z",
                    "item": {
                        "media_id": "123",
                        "source": "tmdb",
                        "media_type": "tv",
                        "title": "Show Name",
                        "image": "https://..."
                    }
                }
            ]
        }
    """
    user = _authenticate(request)
    if user is None:
        return JsonResponse({"detail": "Invalid or missing token."}, status=401)

    media_type = request.GET.get("media_type", "tv")
    status_raw = request.GET.get("status", "")
    limit = min(int(request.GET.get("limit", 20)), 100)
    offset = max(int(request.GET.get("offset", 0)), 0)

    # Validate media_type
    valid_types = [t[0] for t in MediaTypes.choices]
    if media_type not in valid_types:
        return JsonResponse(
            {"detail": f"Invalid media_type. Choose from: {', '.join(valid_types)}"},
            status=400,
        )

    # Build status filter
    status_filter = STATUS_MAP.get(status_raw, status_raw)
    if not status_filter or status_filter.lower() == "all":
        status_q = Q()
    else:
        status_q = Q(status=status_filter)

    # Resolve the correct Django model for this media type
    model = apps.get_model(app_label="app", model_name=media_type)

    queryset = (
        model.objects.filter(user=user, status_q)
        .select_related("item")
        .order_by("-progressed_at")
    )

    items = queryset[offset : offset + limit]
    results = []
    for media in items:
        results.append(
            {
                "progress": media.progress,
                "progressed_at": (
                    media.progressed_at.isoformat() if media.progressed_at else None
                ),
                "item": {
                    "media_id": media.item.media_id,
                    "source": media.item.source,
                    "media_type": media.item.media_type,
                    "title": media.item.title,
                    "image": media.item.image,
                },
            }
        )

    return JsonResponse({"results": results})
