"""Tests for the LED-matrix icon cache.

The load-bearing property: icon rendering NEVER touches the VLM. The VLM is a
single serialized instance shared with posture investigations, so anything
that queries it for decoration can delay emergency reasoning.
"""

import os
import sys

HUB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HUB_DIR)

from framework import icons


class ExplodingVLM:
    """Any use at all is a failure."""

    def is_available(self):
        raise AssertionError("icons must not consult the VLM")

    def structured_query(self, *a, **kw):
        raise AssertionError("icons must not query the VLM")


def test_module_exposes_no_vlm_entry_points():
    # start_boot_warming fired dozens of VLM queries right after a restart --
    # exactly when an investigation is most likely to be waiting on the model.
    assert not hasattr(icons, "start_boot_warming")
    assert not hasattr(icons, "_synthesize_icon")


def test_get_or_generate_icon_takes_no_vlm_argument():
    # A caller cannot reintroduce the dependency by passing one in.
    import inspect

    params = list(inspect.signature(icons.get_or_generate_icon).parameters)
    assert params == ["label"]


def test_render_icon_shape_is_12x8():
    grid = icons.render_icon("skateboard")
    assert len(grid) == 8
    assert all(len(row) == 12 for row in grid)
    assert all(val in (0, 1) for row in grid for val in row)


def test_render_icon_is_deterministic():
    # Stability is why a TTL refresh is now a no-op instead of a fresh guess.
    assert icons.render_icon("potted plant") == icons.render_icon("potted plant")


def test_distinct_labels_get_distinct_silhouettes():
    # The generator used to branch on hash % 2, giving only TWO possible
    # bitmaps for every label in existence -- 'dog' and 'skateboard' lit
    # identical pixels, which makes the icon useless as a signal.
    labels = ["dog", "bird", "skateboard", "potted plant", "chair", "laptop",
              "cup", "car", "bench", "clock", "tv", "sink"]
    rendered = {tuple(map(tuple, icons.render_icon(x))) for x in labels}
    assert len(rendered) == len(labels)


def test_border_is_blank_so_the_silhouette_is_centered():
    grid = icons.render_icon("dog")
    assert grid[0] == [0] * 12
    assert grid[7] == [0] * 12
    assert all(row[0] == 0 and row[11] == 0 for row in grid)


def test_cache_miss_renders_without_a_vlm(tmp_path, monkeypatch):
    monkeypatch.setattr(icons, "HUB_CACHE_FILE", str(tmp_path / "icons.json"))
    monkeypatch.setattr(icons, "_hub_icon_cache", {})

    entry = icons.get_or_generate_icon("skateboard")
    assert entry["bitmap"] == icons.render_icon("skateboard")
    assert entry["permanent"] is False
    assert entry["updated_at"] > 0

    # Second call is a cache hit and returns the same bitmap.
    assert icons.get_or_generate_icon("skateboard")["bitmap"] == entry["bitmap"]


def test_expired_entry_refreshes_without_a_vlm(tmp_path, monkeypatch):
    monkeypatch.setattr(icons, "HUB_CACHE_FILE", str(tmp_path / "icons.json"))
    monkeypatch.setattr(icons, "_hub_icon_cache", {
        "bird": {"bitmap": [[0] * 12] * 8, "updated_at": 0.0,
                 "last_requested_at": 0.0, "permanent": False},
    })
    # updated_at of 0 is well past the 30-minute TTL.
    entry = icons.get_or_generate_icon("bird")
    assert entry["bitmap"] == icons.render_icon("bird")
    assert entry["updated_at"] > 0


def test_permanent_control_states_are_served_from_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(icons, "HUB_CACHE_FILE", str(tmp_path / "icons.json"))
    monkeypatch.setattr(icons, "_hub_icon_cache", {})
    icons.load_cache()

    assert icons.get_or_generate_icon("clear")["bitmap"] == [[0] * 12 for _ in range(8)]
    assert icons.get_or_generate_icon("green")["bitmap"] == [[1] * 12 for _ in range(8)]


def test_blank_label_falls_back_to_clear(tmp_path, monkeypatch):
    monkeypatch.setattr(icons, "HUB_CACHE_FILE", str(tmp_path / "icons.json"))
    monkeypatch.setattr(icons, "_hub_icon_cache", {})
    icons.load_cache()
    assert icons.get_or_generate_icon("  ")["bitmap"] == [[0] * 12 for _ in range(8)]


def test_edge_icon_route_never_reaches_the_vlm(tmp_path, monkeypatch):
    # End-to-end through the Flask route with a VLM that fails on contact.
    from framework import server, transport

    monkeypatch.setattr(transport, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(icons, "HUB_CACHE_FILE", str(tmp_path / "icons.json"))
    monkeypatch.setattr(icons, "_hub_icon_cache", {})

    app = server.create_app(policy=None, vlm=ExplodingVLM(), mqtt=None,
                            sms=None, static_dir=str(tmp_path),
                            face_id=None, pose=None)
    client = app.test_client()

    resp = client.get("/edge/icon?label=skateboard")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["bitmap"] == icons.render_icon("skateboard")

    # POST still works; the uploaded frame is ignored rather than sent to a model.
    resp = client.post("/edge/icon?label=bird", data=b"\xff\xd8ignored",
                       content_type="image/jpeg")
    assert resp.status_code == 200
    assert resp.get_json()["bitmap"] == icons.render_icon("bird")


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-v"]))
