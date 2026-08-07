"""
make_samples.py — generate license-free synthetic test images for the hub.

Creates two deterministic scenes used to exercise the person / no-person
verification paths without any external downloads or licensing concerns:

    room_with_person.jpg   a simple room containing a human figure
    empty_room.jpg         the same room with no person

The real-photo sample (geniex_demo.jpg) is Qualcomm's public GenieX demo asset
and is shipped alongside these.

Run:
    python hub/apps/security/samples/make_samples.py
"""

import os

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


def _person(draw, cx=320, top=170):
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


def main():
    # empty room
    img = Image.new("RGB", (W, H))
    _room(ImageDraw.Draw(img))
    p1 = os.path.join(HERE, "empty_room.jpg")
    img.save(p1, quality=88)

    # room with person
    img2 = Image.new("RGB", (W, H))
    d2 = ImageDraw.Draw(img2)
    _room(d2)
    _person(d2)
    p2 = os.path.join(HERE, "room_with_person.jpg")
    img2.save(p2, quality=88)

    print("wrote:", p1)
    print("wrote:", p2)


if __name__ == "__main__":
    main()
