"""
make_samples.py — generate license-free synthetic test images for the hub.

Creates deterministic scenes used to exercise the hub's verification paths
without any external downloads or licensing concerns:

    room_with_person.jpg   a simple room containing a human figure
    empty_room.jpg         the same room with no person
    senior_fall.png        the same room, person lying on the floor
    intruder_cctv.jpg      a dim, grainy take on the room with a figure
                            carrying a bar-like object -- replaces a real
                            watermarked Alamy stock photo that shipped here
                            under an unclear license (2026-08-07 scrub)

The real-photo sample (geniex_demo.jpg) is Qualcomm's public GenieX demo asset
and is shipped alongside these.

Run:
    python hub/apps/security/samples/make_samples.py
"""

import os
import random

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 640, 480


def _room(draw):
    # wall
    draw.rectangle([0, 0, W, H], fill=(214, 210, 196))
    # floor
    draw.rectangle([0, int(H * 0.72), W, H], fill=(150, 120, 90))
    # doorway
    draw.rectangle([40, 90, 170, int(H * 0.72)], fill=(120, 100, 78))
    draw.rectangle([48, 98, 162, int(H * 0.72)], fill=(90, 72, 55))
    # window
    draw.rectangle([430, 110, 580, 240], fill=(160, 200, 230))
    draw.line([505, 110, 505, 240], fill=(80, 80, 80), width=3)
    draw.line([430, 175, 580, 175], fill=(80, 80, 80), width=3)


def _person(draw, cx=320, top=170, holding_bar=False):
    # head
    r = 26
    draw.ellipse([cx - r, top, cx + r, top + 2 * r], fill=(224, 178, 140))
    # body
    body_top = top + 2 * r + 4
    draw.rectangle([cx - 30, body_top, cx + 30, body_top + 110], fill=(40, 90, 160))
    # legs
    leg_top = body_top + 110
    draw.rectangle([cx - 28, leg_top, cx - 6, leg_top + 90], fill=(40, 44, 60))
    draw.rectangle([cx + 6, leg_top, cx + 28, leg_top + 90], fill=(40, 44, 60))
    # arms
    draw.rectangle([cx - 48, body_top + 6, cx - 30, body_top + 80], fill=(40, 90, 160))
    draw.rectangle([cx + 30, body_top + 6, cx + 48, body_top + 80], fill=(40, 90, 160))
    if holding_bar:
        # a long thin object gripped in the lowered right hand -- enough of a
        # silhouette cue for the intrusion scenario without depicting a real
        # weapon in detail
        draw.rectangle([cx + 36, body_top + 60, cx + 42, body_top + 160],
                       fill=(60, 60, 64))


def _fallen_person(draw, cx=320, floor_y=None):
    """Same figure, rotated to lie along the floor -- fall-detection scenario."""
    if floor_y is None:
        floor_y = int(H * 0.72)
    r = 26
    body_len = 110
    leg_len = 90
    y = floor_y - r - 6  # figure rests just above the floor line
    # legs (toward the doorway)
    draw.rectangle([cx - body_len - leg_len, y - 20, cx - body_len, y],
                   fill=(40, 44, 60))
    draw.rectangle([cx - body_len - leg_len, y + 2, cx - body_len, y + 22],
                   fill=(40, 44, 60))
    # body
    draw.rectangle([cx - body_len, y - 22, cx, y + 22], fill=(40, 90, 160))
    # arms
    draw.rectangle([cx - body_len + 10, y - 34, cx - 10, y - 22], fill=(40, 90, 160))
    # head
    draw.ellipse([cx, y - r, cx + 2 * r, y + r], fill=(224, 178, 140))


def _grain(img, amount=18, seed=1):
    """Coarse per-pixel jitter -- cheap stand-in for real CCTV sensor noise,
    enough to read as low-quality footage rather than a clean render."""
    rng = random.Random(seed)
    px = img.load()
    for x in range(0, W, 2):
        for y in range(0, H, 2):
            if rng.random() < 0.35:
                r, g, b = px[x, y]
                d = rng.randint(-amount, amount)
                px[x, y] = (max(0, min(255, r + d)),
                           max(0, min(255, g + d)),
                           max(0, min(255, b + d)))


def main():
    written = []

    # empty room
    img = Image.new("RGB", (W, H))
    _room(ImageDraw.Draw(img))
    p = os.path.join(HERE, "empty_room.jpg")
    img.save(p, quality=88)
    written.append(p)

    # room with person
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    _room(d)
    _person(d)
    p = os.path.join(HERE, "room_with_person.jpg")
    img.save(p, quality=88)
    written.append(p)

    # fall scenario
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    _room(d)
    _fallen_person(d)
    p = os.path.join(HERE, "senior_fall.png")
    img.save(p)
    written.append(p)

    # intrusion scenario: dim + grainy, figure carrying a bar-like object
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    _room(d)
    _person(d, holding_bar=True)
    img = Image.eval(img, lambda v: int(v * 0.45))  # dim, as if underlit/night
    _grain(img)
    p = os.path.join(HERE, "intruder_cctv.jpg")
    img.save(p, quality=80)
    written.append(p)

    for p in written:
        print("wrote:", p)


if __name__ == "__main__":
    main()
