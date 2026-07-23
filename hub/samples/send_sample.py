"""
send_sample.py — post a bundled sample image to a running hub, for quick
end-to-end testing (especially on the Snapdragon laptop where reasoning is live).

Usage:
    python hub/samples/send_sample.py                       # room_with_person -> /edge/event
    python hub/samples/send_sample.py empty_room            # by sample name
    python hub/samples/send_sample.py geniex_demo.jpg reason  # -> /user/reason
    python hub/samples/send_sample.py room_with_person event http://HUB_IP:8000

Args: [sample] [mode: event|reason] [base_url]
Requires: pip install requests   (or use the curl commands in hub/README.md)
"""

import os
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    sample = sys.argv[1] if len(sys.argv) > 1 else "room_with_person"
    mode = sys.argv[2] if len(sys.argv) > 2 else "event"
    base = sys.argv[3] if len(sys.argv) > 3 else "http://127.0.0.1:8000"

    if not sample.endswith((".jpg", ".jpeg", ".png")):
        sample += ".jpg"
    path = os.path.join(HERE, sample)
    if not os.path.exists(path):
        print(f"[!] sample not found: {path}")
        print("    available:", [f for f in os.listdir(HERE) if f.endswith(".jpg")])
        sys.exit(1)

    if mode == "reason":
        url = f"{base}/user/reason"
        files = {"image": open(path, "rb")}
        data = {"prompt": "Describe the scene. Is there a person?"}
    else:
        url = f"{base}/edge/event"
        files = {"image": open(path, "rb")}
        data = {"event": '{"device_id":"sample-sender","event_id":"%s","edge_confidence":0.85}' % sample}

    print(f"POST {url}  <-  {sample}")
    r = requests.post(url, files=files, data=data, timeout=120)
    print("HTTP", r.status_code)
    print(r.text)


if __name__ == "__main__":
    main()
