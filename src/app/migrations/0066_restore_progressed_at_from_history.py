"""Restore progressed_at for media records where it is stale or missing.

After MonitorField was replaced with a plain DateTimeField (migration 0065),
existing rows retained their old MonitorField value (= record creation time)
and subsequent progress changes no longer updated the field automatically.

This migration walks the django-simple-history tables for every media type
that has a progressed_at column, finds the most-recent history entry where
progress changed to a non-zero value, and writes that history_date back into
progressed_at.  Records whose progress is still 0 or whose history contains
no progress change are left untouched (NULL).
"""

from django.db import migrations

MEDIA_MODELS = [
    ("anime",      "historicalanime"),
    ("basicmedia", "historicalbasicmedia"),
    ("boardgame",  "historicalboardgame"),
    ("book",       "historicalbook"),
    ("comic",      "historicalcomic"),
    ("game",       "historicalgame"),
    ("manga",      "historicalmanga"),
    ("movie",      "historicalmovie"),
]


def restore_progressed_at(apps, schema_editor):
    for model_name, hist_name in MEDIA_MODELS:
        Model = apps.get_model("app", model_name)
        Hist  = apps.get_model("app", hist_name)

        # For every instance that has progress > 0, find the history record
        # with the latest history_date where progress > 0.
        # We do this in Python to stay DB-agnostic (SQLite / Postgres).
        ids = list(Model.objects.filter(progress__gt=0).values_list("id", flat=True))
        if not ids:
            continue

        # Fetch relevant history rows ordered newest-first.
        history_rows = (
            Hist.objects
            .filter(id__in=ids, progress__gt=0)
            .order_by("id", "-history_date")
            .values("id", "history_date")
        )

        # Keep only the latest history_date per object id.
        latest = {}
        for row in history_rows:
            if row["id"] not in latest:
                latest[row["id"]] = row["history_date"]

        if not latest:
            continue

        to_update = []
        for media in Model.objects.filter(id__in=latest.keys()):
            new_date = latest[media.id]
            # Only overwrite if the stored value is missing or older than the
            # most-recent progress history entry.
            if media.progressed_at is None or media.progressed_at < new_date:
                media.progressed_at = new_date
                to_update.append(media)

        if to_update:
            Model.objects.bulk_update(to_update, ["progressed_at"])


def noop(apps, schema_editor):
    pass  # irreversible data migration; rolling back leaves fields as-is


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0065_alter_anime_progressed_at_and_more"),
    ]

    operations = [
        migrations.RunPython(restore_progressed_at, noop),
    ]
