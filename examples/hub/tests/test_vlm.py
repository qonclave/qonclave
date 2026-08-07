#!/usr/bin/env python3
"""
test_vlm.py — GenieX vision-language smoke test (Qwen2.5-VL-7B on Snapdragon X Elite).

Feeds an image + prompt to the VLM and prints its reasoning. This is the hub-side
"second-stage reasoning" step for Qonclave: once the edge device escalates a frame,
the hub describes the scene and can generate alert text.

Run inside the geniex-env built by hub/setup_hub.ps1, from the repo root:

    .\\hub\\geniex-env\\Scripts\\Activate.ps1
    # grab a sample image the first time (image paths are relative to the
    # directory you run from, not to this script):
    curl -o demo.jpg https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-geniex/demo.jpg
    python hub\\tests\\test_vlm.py           # uses demo.jpg, default prompt
    python hub\\tests\\test_vlm.py my_frame.jpg "Is a person present? What are they doing?"

Model bundle: ai-hub-models/Qwen2.5-VL-7B-Instruct  (Qualcomm AI Hub VLM bundle,
runs on the Hexagon NPU via the qairt runtime; the largest VLM AI Hub lists for
first-gen Snapdragon X Elite).
"""

import os
import platform
import sys

MODEL_ID = "ai-hub-models/Qwen2.5-VL-7B-Instruct"
DEFAULT_IMAGE = "demo.jpg"
DEFAULT_PROMPT = "Describe the image. Is there a person? If so, what are they doing?"


def main():
    # Args: [image_path] [prompt...]
    image_arg = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IMAGE
    prompt_text = " ".join(sys.argv[2:]) if len(sys.argv) > 2 else DEFAULT_PROMPT

    print(f"platform.machine() = {platform.machine()}")

    if not os.path.exists(image_arg):
        print(f"[!] Image not found: {image_arg}")
        print("    Download the sample image first:")
        print("    curl -o demo.jpg https://qaihub-public-assets.s3.us-west-2.amazonaws.com/qai-hub-geniex/demo.jpg")
        sys.exit(1)

    image_path = os.path.abspath(image_arg)
    print(f"image  = {image_path}")
    print(f"prompt = {prompt_text}\n")

    from geniex import AutoModelForCausalLM

    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        device_map="qairt",   # pre-compiled VLM bundle runs on Hexagon NPU via qairt
    )

    messages = [{
        "role": "user",
        "content": [
            {"type": "image", "image": image_path},
            {"type": "text", "text": prompt_text},
        ],
    }]
    prompt = model.tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True,
    )

    print("--- streaming ---")
    streamer = model.generate(
        prompt, images=[image_path], max_new_tokens=256, stream=True,
    )
    for chunk in streamer:
        print(chunk, end="", flush=True)
    print()

    model.close()


if __name__ == "__main__":
    try:
        main()
    except ImportError as e:
        print(f"\n[!] Could not import geniex: {e}")
        print("    Activate the venv and install it:  pip install -U geniex")
        sys.exit(1)
    except Exception as e:
        print(f"\n[!] VLM test failed: {type(e).__name__}: {e}")
        sys.exit(1)
