# Sample images

Bundled test images so the hub can be exercised end-to-end (especially on the
Snapdragon laptop where VLM reasoning is live) without hunting for a file.

| File | Contents | Expected hub verdict (VLM live) |
|------|----------|---------------------------------|
| `room_with_person.jpg` | A room with a human figure | `hub_verified: true` |
| `empty_room.jpg` | The same room, no person | `hub_verified: false` |
| `geniex_demo.jpg` | Qualcomm's public GenieX demo photo | depends on scene |

`room_with_person.jpg` and `empty_room.jpg` are synthetic and license-free —
regenerate them with `python make_samples.py`. `geniex_demo.jpg` is Qualcomm's
public GenieX sample asset.

## Quick test against a running hub

Using the helper (needs `pip install requests`):
```bash
python hub/samples/send_sample.py room_with_person          # -> /edge/event
python hub/samples/send_sample.py empty_room                # -> /edge/event
python hub/samples/send_sample.py geniex_demo reason        # -> /user/reason
python hub/samples/send_sample.py room_with_person event http://HUB_IP:8000
```

Or plain curl:
```bash
curl -F "image=@hub/samples/room_with_person.jpg" \
     -F 'event={"device_id":"unoq-01","event_id":"evt-1","edge_confidence":0.85}' \
     http://127.0.0.1:8000/edge/event
```

On a non-Snapdragon machine every event returns `hub_verified: false`
("reasoning unavailable") — that's expected; the plumbing still works. On the
Snapdragon laptop with GenieX installed, `room_with_person.jpg` should verify.
