from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from app.models import Anime, Item, MediaTypes, Sources, Status


class MediaListAPITests(TestCase):
    """Test the REST API endpoint (/api/media/)."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="test",
            password="12345",
        )
        self.token = self.user.token

    def _create_anime(self, title, progressed_at=None, progress=1):
        item = Item.objects.create(
            media_id=f"mal-{title.replace(' ', '-')}",
            source=Sources.MAL.value,
            media_type=MediaTypes.ANIME.value,
            title=title,
            image="http://example.com/image.jpg",
        )
        return Anime.objects.create(
            item=item,
            user=self.user,
            status=(
                Status.IN_PROGRESS.value if progressed_at else Status.PLANNING.value
            ),
            progress=progress,
        )

    def _set_progressed_at(self, media, value):
        media.progressed_at = value
        media.save(update_fields=["progressed_at"])

    def test_requires_token(self):
        response = self.client.get("/api/media/", {"media_type": "anime"})
        self.assertEqual(response.status_code, 401)

    def test_no_date_entries_do_not_float_to_top(self):
        """Entries without a watch date sort below recently watched ones."""
        watched = self._create_anime("Watched Recently", progress=5)
        no_date_old = self._create_anime("Old No Date")
        no_date_new = self._create_anime("New No Date")

        # "watched" was added first but has a recent watch date
        watched.created_at = timezone.now() - timezone.timedelta(days=30)
        watched.save()
        self._set_progressed_at(watched, timezone.now())

        # no-date entries were imported later (created_at defaults to now)
        no_date_old.created_at = timezone.now() - timezone.timedelta(days=2)
        no_date_old.save()
        no_date_new.refresh_from_db()

        response = self.client.get(
            "/api/media/",
            {"media_type": "anime", "limit": 100},
            headers={"Authorization": f"Token {self.token}"},
        )
        self.assertEqual(response.status_code, 200)

        titles = [r["item"]["title"] for r in response.json()["results"]]
        self.assertEqual(
            titles,
            ["Watched Recently", "New No Date", "Old No Date"],
        )

    @mock.patch("app.models.Item.fetch_releases")
    @mock.patch("app.providers.services.get_media_metadata")
    def test_tv_sorted_by_latest_watched_episode(self, mock_meta, _mock_fetch):
        """TV entries have no progressed_at column, sort by episode dates."""
        from app.models import Episode, Season, TV

        mock_meta.return_value = {
            "season/1": {"episodes": [{"episode_number": 1}]},
        }
        tv_item = Item.objects.create(
            media_id="tv-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV",
            image="http://example.com/image.jpg",
        )
        tv = TV.objects.create(
            item=tv_item,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        season_item = Item.objects.create(
            media_id="tv-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            related_tv=tv,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

        ep_item = Item.objects.create(
            media_id="tv-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.EPISODE.value,
            title="E1",
            image="http://example.com/image.jpg",
            season_number=1,
            episode_number=1,
        )
        Episode.objects.create(
            item=ep_item,
            related_season=season,
            end_date=timezone.now(),
        )
        tv.refresh_from_db()

        planned_item = Item.objects.create(
            media_id="tv-2",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Planned TV",
            image="http://example.com/image.jpg",
        )
        TV.objects.create(
            item=planned_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        # pretend the planned entry was added a while ago
        TV.objects.filter(item=planned_item).update(
            created_at=timezone.now() - timezone.timedelta(days=30),
        )

        response = self.client.get(
            "/api/media/",
            {"media_type": "tv"},
            headers={"Authorization": f"Token {self.token}"},
        )
        self.assertEqual(response.status_code, 200)

        titles = [r["item"]["title"] for r in response.json()["results"]]
        self.assertEqual(titles, ["Test TV", "Planned TV"])

    def test_max_progress_fallback_to_metadata(self):
        """Anime without tracked events get max_progress from metadata."""
        self._create_anime("Some Anime")

        with mock.patch(
            "app.api._max_progress_from_metadata",
            return_value=12,
        ):
            response = self.client.get(
                "/api/media/",
                {"media_type": "anime"},
                headers={"Authorization": f"Token {self.token}"},
            )

        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertTrue(results)
        self.assertTrue(all(r["max_progress"] == 12 for r in results))
