"""Minimal REST API for external integrations.

Returns media data authenticated via User.token.
"""

import logging

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import Count, F, Max, Q
from django.http import HttpResponse, JsonResponse
from django.utils import timezone

from app.models import MediaTypes
from app.providers import services as provider_services
from events.models import Event

logger = logging.getLogger(__name__)

User = get_user_model()

# Map numeric status codes (used by info-page watchlist.ts) to Yamtrack
# status strings.
STATUS_MAP = {
    "1": "In progress",
    "2": "Completed",
    "3": "Planning",
    "4": "Paused",
    "5": "Dropped",
}


def _max_progress_from_metadata(item):
    """Return total unit count from provider metadata.

    Fallback for sources whose released episodes are not tracked by the
    events app (e.g. MAL anime).
    """
    try:
        metadata = provider_services.get_media_metadata(
            item.media_type,
            item.media_id,
            item.source,
        )
    except Exception:
        logger.debug("Metadata lookup failed for %s", item, exc_info=True)
        return None
    return metadata.get("max_progress") or None


def _batch_event_counts(items):
    """Return {media_id: released_episode_count} for the given items.

    A single query replaces N individual COUNT queries in the item loop.
    """
    media_ids = [m.item.media_id for m in items if m.item]
    if not media_ids:
        return {}
    rows = (
        Event.objects.filter(
            item__media_id__in=media_ids,
            item__media_type=MediaTypes.SEASON.value,
            item__season_number__gt=0,
            content_number__isnull=False,
        )
        .values("item__media_id")
        .annotate(cnt=Count("id"))
    )
    return {r["item__media_id"]: r["cnt"] for r in rows}


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


def _cors_headers(response):
    """Add CORS headers for cross-origin requests."""
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Authorization, Content-Type"
    response["Access-Control-Max-Age"] = "86400"
    return response


def _get_progressed_at(media):
    """Return the relevant watch date for a media instance.

    TV/Season: progressed_at is a @property computed from episode dates.
    Anime/Movie: use the MonitorField progressed_at which auto-updates
    whenever progress changes (i.e. on each episode watched).

    The value is localized to the active timezone so it matches the date
    shown in Yamtrack's activity history (which also renders in local time)
    instead of being serialized as raw UTC.
    """
    value = media.progressed_at
    if value is not None and timezone.is_aware(value):
        return timezone.localtime(value)
    return value


def media_list(request):
    """Return a paginated list of media items for the authenticated user.

    Query parameters:
        media_type -- tv, movie, anime, manga, etc.
        status     -- numeric (1-5) or string status filter
        limit      -- max items to return (default 20, max 100)
        offset     -- number of items to skip

    Response:
        {
            "count": 42,
            "results": [
                {
                    "progress": 5,
                    "progressed_at": "2024-01-01T00:00:00Z",
                    "max_progress": 12,
                    "item": { "media_id": "123", ... }
                }
            ]
        }
    """
    if request.method == "OPTIONS":
        return _cors_headers(HttpResponse(status=204))

    user = _authenticate(request)
    if user is None:
        return _cors_headers(
            JsonResponse({"detail": "Invalid or missing token."}, status=401)
        )

    media_type = request.GET.get("media_type", "tv")
    status_raw = request.GET.get("status", "")
    limit = min(int(request.GET.get("limit", 20)), 100)
    offset = max(int(request.GET.get("offset", 0)), 0)

    valid_types = [t[0] for t in MediaTypes.choices]
    if media_type not in valid_types:
        return _cors_headers(JsonResponse(
            {"detail": f"Invalid media_type. Choose from: {', '.join(valid_types)}"},
            status=400,
        ))

    status_filter = STATUS_MAP.get(status_raw, status_raw)
    if not status_filter or status_filter.lower() == "all":
        status_q = Q()
    else:
        status_q = Q(status=status_filter)

    model = apps.get_model(app_label="app", model_name=media_type)
    base_qs = model.objects.filter(status_q, user=user).select_related("item")

    # --- Ordering: watch-date first, undated items strictly at the bottom ---
    # For models with a progressed_at column (Movie, Anime, ...): order by
    # that field descending with nulls_last so entries without a watch date
    # always sort below every dated entry.
    # For TV / Season (no column): derive from the latest watched episode
    # end_date using an annotation.
    has_date_col = any(
        f.attname == "progressed_at" for f in model._meta.concrete_fields
    )
    if has_date_col:
        queryset = base_qs.order_by(
            F("progressed_at").desc(nulls_last=True),
            "-created_at",
        )
    elif media_type == MediaTypes.TV.value:
        queryset = (
            base_qs
            .annotate(_last_watched=Max("seasons__episodes__end_date"))
            .order_by(F("_last_watched").desc(nulls_last=True), "-created_at")
        )
    else:
        queryset = (
            base_qs
            .annotate(_last_watched=Max("episodes__end_date"))
            .order_by(F("_last_watched").desc(nulls_last=True), "-created_at")
        )

    total = base_qs.count()
    items = list(queryset[offset : offset + limit])

    # --- Batch event counts (one query, not N) ---
    event_counts = _batch_event_counts(items) if items else {}

    results = []
    for media in items:
        max_progress = None
        if media_type == MediaTypes.MOVIE.value:
            max_progress = 1
        elif media_type in (MediaTypes.TV.value, MediaTypes.ANIME.value):
            ec = event_counts.get(media.item.media_id, 0)
            max_progress = ec or _max_progress_from_metadata(media.item) or None

        results.append(
            {
                "score": (
                    float(media.score) if media.score is not None else None
                ),
                "status": media.status,
                "progress": media.progress,
                "max_progress": max_progress,
                "progressed_at": _get_progressed_at(media),
                "item": {
                    "media_id": media.item.media_id,
                    "source": media.item.source,
                    "media_type": media.item.media_type,
                    "title": media.item.title,
                    "image": media.item.image,
                },
            }
        )

    return _cors_headers(JsonResponse({"count": total, "results": results}))


# Exempt from LoginRequiredMiddleware (token auth handled inside the view)
media_list.login_required = False
