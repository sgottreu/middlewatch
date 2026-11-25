"""release.py: youtube_id parsing, approve/publish gates."""
import pytest

from story_pipeline import release
from story_pipeline.release import approve_video, publish, youtube_id

VID = "dQw4w9WgXcQ"


@pytest.mark.parametrize("url", [
    VID,
    f"  {VID} ",
    f"https://www.youtube.com/watch?v={VID}",
    f"https://youtube.com/watch?v={VID}&t=10s",
    f"youtube.com/watch?v={VID}",
    f"https://m.youtube.com/watch?v={VID}",
    f"https://youtu.be/{VID}",
    f"https://youtu.be/{VID}?si=abc",
    f"https://www.youtube.com/shorts/{VID}",
    f"https://www.youtube.com/live/{VID}",
    f"https://www.youtube.com/embed/{VID}",
    f"https://www.youtube.com/v/{VID}",
])
def test_youtube_id_accepts(url):
    assert youtube_id(url) == VID


@pytest.mark.parametrize("url", [
    "", None, "https://example.com/watch?v=" + VID, "https://www.youtube.com/watch",
    "https://www.youtube.com/watch?v=short", "https://www.youtube.com/channel/" + VID,
    "https://youtu.be/", "not a url", "https://www.youtube.com/playlist?list=" + VID,
])
def test_youtube_id_rejects(url):
    assert youtube_id(url) is None


def test_approve_requires_direct_done(mini):
    with pytest.raises(ValueError, match="no rendered video"):
        approve_video(mini)


def test_approve_and_undo(mini):
    mini.set_stage("direct", "done")
    assert approve_video(mini) == "approved"
    assert mini.manifest()["stages"]["approve"] == "approved"
    assert approve_video(mini, undo=True) == "awaiting_review"


def test_undo_refused_when_published(mini):
    mini.set_stage("direct", "done")
    approve_video(mini)
    publish(mini, VID)
    with pytest.raises(ValueError, match="already published"):
        approve_video(mini, undo=True)


def test_publish_needs_approval(mini):
    mini.set_stage("direct", "done")        # derived approve = awaiting_review
    with pytest.raises(ValueError, match="approve the video"):
        publish(mini, VID)
    assert mini.stage_status("publish") == "pending"


def test_publish_force_skips_approval(mini):
    info = publish(mini, VID, force=True)
    assert info["video_id"] == VID


def test_publish_records_info(mini):
    mini.set_stage("direct", "done")
    approve_video(mini)
    info = publish(mini, f"https://www.youtube.com/watch?v={VID}", when="2026-03-04")
    assert info == {"url": f"https://youtu.be/{VID}", "video_id": VID, "published": "2026-03-04"}
    assert mini.stage_status("publish") == "done"
    assert mini.publish_info() == info


def test_publish_defaults_to_today(mini, monkeypatch):
    class D(release.date):
        @classmethod
        def today(cls):
            return cls(2026, 1, 2)
    monkeypatch.setattr(release, "date", D)
    mini.set_stage("approve", "approved")
    assert publish(mini, VID)["published"] == "2026-01-02"


def test_publish_bad_url_and_bad_date_leave_manifest_alone(mini):
    mini.set_stage("approve", "approved")
    with pytest.raises(ValueError, match="not a YouTube"):
        publish(mini, "https://example.com/x")
    with pytest.raises(ValueError):
        publish(mini, VID, when="2026-13-45")
    assert mini.stage_status("publish") == "pending"


def test_publish_undo(mini):
    mini.set_stage("approve", "approved")
    publish(mini, VID)
    assert publish(mini, "", undo=True) == {}
    assert mini.stage_status("publish") == "pending"
    assert mini.publish_info() == {}
    publish(mini, "", undo=True)             # idempotent
