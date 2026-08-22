"""Minimal REST API for external integrations.

Returns media data authenticated via User.token.
"""

import logging

from django.apps import apps
from django.contrib.auth import get_user_model
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
        model.objects.filter(status_q, user=user)
        .select_related("item")
        .order_by("-created_at")
    )

    items = queryset[offset : offset + limit]
    results = []
    for media in items:
        # Calculate max_progress (total episodes / items)
        max_progress = None
        if media_type == MediaTypes.MOVIE.value:
            max_progress = 1
        elif media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
            # Count released episodes via Events
            from events.models import Event
            from app.models import MediaTypes as MT
            max_progress = Event.objects.filter(
                item__media_id=media.item.media_id,
                item__source=media.item.source,
                item__media_type=MT.SEASON.value,
                item__season_number__gt=0,
                content_number__isnull=False,
            ).count() or None

        results.append(
            {
                "score": (
                    float(media.score) if media.score is not None else None
                ),
                "status": media.status,
                "progress": media.progress,
                "max_progress": max_progress,
                "progressed_at": (
                    media.progressed_at.isoformat() if hasattr(media, 'progressed_at') and media.progressed_at else None
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


# Exempt from LoginRequiredMiddleware (token auth handled inside the view)
media_list.login_required = False
