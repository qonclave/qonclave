# Sample images

Bundled test images so the hub can be exercised end-to-end (especially on the
Snapdragon laptop where VLM reasoning is live) without hunting for a file.

| File | Contents | Expected hub verdict (VLM live) |
|------|----------|---------------------------------|
| `room_with_person.jpg` | A room with a human figure | `hub_verified: true` |
| `empty_room.jpg` | The same room, no person | `hub_verified: false` |
| `geniex_demo.jpg` | Qualcomm's public GenieX demo photo | depends on scene |
| `intruder_cctv.jpg` | Stock CCTV-style photo of an intruder with a crowbar | `hub_verified: true` (security/intrusion scenario) |
| `senior_fall.png` | Photo of a fall scenario | `hub_verified: true` (fall/elderly-care scenario) |

`room_with_person.jpg` and `empty_room.jpg` are synthetic and license-free —
regenerate them with `python make_samples.py`. `geniex_demo.jpg` is Qualcomm's
public GenieX sample asset.

`intruder_cctv.jpg` is a stock photo from Alamy (comp/watermarked preview
resolution) — fine for local hackathon demo/testing, but **do not** redistribute
or ship it in any public release without checking Alamy's licensing terms.

## Quick test against a running hub

Using the helper (needs `pip install requests`):
```bash
python hub/apps/security/samples/send_sample.py room_with_person          # -> /edge/event
python hub/apps/security/samples/send_sample.py empty_room                # -> /edge/event
python hub/apps/security/samples/send_sample.py geniex_demo reason        # -> /user/reason
python hub/apps/security/samples/send_sample.py room_with_person event http://HUB_IP:8000
```

Or plain curl:
```bash
curl -F "image=@hub/apps/security/samples/room_with_person.jpg" \
     -F 'event={"device_id":"unoq-01","event_id":"evt-1","edge_confidence":0.85}' \
     http://127.0.0.1:8000/edge/event
```

On a non-Snapdragon machine every event returns `hub_verified: false`
("reasoning unavailable") — that's expected; the plumbing still works. On the
Snapdragon laptop with GenieX installed, `room_with_person.jpg` should verify.
