from datetime import UTC, datetime
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from app.forms import SeasonForm, TvForm
from app.models import (
    TV,
    Item,
    MediaTypes,
    Season,
    Sources,
    Status,
)

MOCK_EPISODES = [
    {"episode_number": 1},
    {"episode_number": 2},
    {"episode_number": 3},
]


def fake_metadata(media_type, media_id, source, season_numbers=None, **kwargs):
    """Return fake metadata for any provider call."""
    if media_type == "tv_with_seasons":
        return {
            f"season/{sn}": {"episodes": MOCK_EPISODES}
            for sn in (season_numbers or [1])
        }
    return {"episodes": MOCK_EPISODES, "title": "Test", "image": "http://x"}


class SeasonTrackingTests(TestCase):
    """Test Season sync_progress, sync_episode_state, set_watch_dates."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="test", password="12345"
        )
        self.tv_item = Item.objects.create(
            media_id="tv-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test TV",
            image="http://example.com/image.jpg",
        )
        self.tv = TV.objects.create(
            item=self.tv_item,
            user=self.user,
            status=Status.PLANNING.value,
        )
        self.season_item = Item.objects.create(
            media_id="tv-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test TV",
            image="http://example.com/image.jpg",
            season_number=1,
        )
        self.season = Season.objects.create(
            item=self.season_item,
            related_tv=self.tv,
            user=self.user,
            status=Status.IN_PROGRESS.value,
        )

    @mock.patch("app.models.Item.fetch_releases")
    @mock.patch("app.providers.services.get_media_metadata")
    def test_sync_progress_creates_episodes(self, mock_meta, _mock_fetch):
        """sync_progress creates episodes up to target count."""
        mock_meta.side_effect = fake_metadata
        self.season.sync_progress(2)
        self.assertEqual(self.season.progress, 2)

    @mock.patch("app.models.Item.fetch_releases")
    @mock.patch("app.providers.services.get_media_metadata")
    def test_sync_progress_removes_episodes(self, mock_meta, _mock_fetch):
        """sync_progress removes episodes above target."""
        mock_meta.side_effect = fake_metadata
        self.season.sync_progress(3)
        self.assertEqual(self.season.progress, 3)
        self.season.sync_progress(1)
        self.assertEqual(self.season.progress, 1)

    @mock.patch("app.models.Item.fetch_releases")
    @mock.patch("app.providers.services.get_media_metadata")
    def test_set_watch_dates_distributes(self, mock_meta, _mock_fetch):
        """set_watch_dates distributes dates across watched episodes."""
        mock_meta.side_effect = fake_metadata
        self.season.sync_progress(3)
        start = datetime(2026, 1, 1, tzinfo=UTC)
        end = datetime(2026, 1, 3, tzinfo=UTC)
        self.season.set_watch_dates(start, end)
        eps = sorted(
            self.season.get_watched_episodes(),
            key=lambda e: e.item.episode_number,
        )
        self.assertEqual(eps[0].end_date, start)
        self.assertEqual(eps[2].end_date, end)
        self.assertEqual(eps[1].end_date.replace(tzinfo=None), datetime(2026, 1, 2))

    @mock.patch("app.models.Item.fetch_releases")
    @mock.patch("app.providers.services.get_media_metadata")
    def test_set_watch_dates_clears(self, mock_meta, _mock_fetch):
        """set_watch_dates with end_date=None clears all dates."""
        mock_meta.side_effect = fake_metadata
        self.season.sync_progress(2)
        self.season.set_watch_dates(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        )
        self.season.set_watch_dates(None, None)
        for ep in self.season.get_watched_episodes():
            self.assertIsNone(ep.end_date)

    @mock.patch("app.models.Item.fetch_releases")
    @mock.patch("app.providers.services.get_media_metadata")
    def test_sync_episode_state(self, mock_meta, _mock_fetch):
        """sync_episode_state watches/unwatches individual episodes."""
        mock_meta.side_effect = fake_metadata
        self.season.sync_episode_state({
            1: datetime(2026, 1, 1, tzinfo=UTC),
            2: False,
            3: datetime(2026, 1, 3, tzinfo=UTC),
        })
        watched = {ep.item.episode_number for ep in self.season.get_watched_episodes()}
        self.assertEqual(watched, {1, 3})

    @mock.patch("app.models.Item.fetch_releases")
    @mock.patch("app.providers.services.get_media_metadata")
    def test_sync_episode_state_clears_date(self, mock_meta, _mock_fetch):
        """sync_episode_state with None sets end_date=None (watched, no date)."""
        mock_meta.side_effect = fake_metadata
        self.season.sync_episode_state({
            1: datetime(2026, 1, 1, tzinfo=UTC),
        })
        ep = self.season.get_watched_episodes()[0]
        self.assertIsNotNone(ep.end_date)

        self.season.sync_episode_state({1: None})
        ep.refresh_from_db()
        self.assertIsNone(ep.end_date)
        watched = {e.item.episode_number for e in self.season.get_watched_episodes()}
        self.assertIn(1, watched)


class TvFormDateTests(TestCase):
    """Test TvForm with start_date / end_date fields."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="test", password="12345"
        )

    @mock.patch("app.models.Item.fetch_releases")
    @mock.patch("app.providers.services.get_media_metadata")
    def test_tv_form_has_date_fields(self, mock_meta, _mock_fetch):
        """TvForm includes start_date and end_date fields."""
        tv_item = Item.objects.create(
            media_id="tv-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test",
            image="http://example.com/img.jpg",
        )
        tv = TV.objects.create(
            item=tv_item, user=self.user, status=Status.PLANNING.value
        )
        form = TvForm(instance=tv)
        self.assertIn("start_date", form.fields)
        self.assertIn("end_date", form.fields)


class SeasonFormDateTests(TestCase):
    """Test SeasonForm with progress / start_date / end_date."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="test", password="12345"
        )

    def test_season_form_has_fields(self):
        """SeasonForm includes progress, start_date, end_date."""
        tv_item = Item.objects.create(
            media_id="tv-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.TV.value,
            title="Test",
            image="http://example.com/img.jpg",
        )
        tv = TV.objects.create(
            item=tv_item, user=self.user, status=Status.PLANNING.value
        )
        season_item = Item.objects.create(
            media_id="tv-1",
            source=Sources.TMDB.value,
            media_type=MediaTypes.SEASON.value,
            title="Test S1",
            image="http://example.com/img.jpg",
            season_number=1,
        )
        season = Season.objects.create(
            item=season_item,
            related_tv=tv,
            user=self.user,
            status=Status.PLANNING.value,
        )
        form = SeasonForm(instance=season)
        self.assertIn("progress", form.fields)
        self.assertIn("start_date", form.fields)
        self.assertIn("end_date", form.fields)
