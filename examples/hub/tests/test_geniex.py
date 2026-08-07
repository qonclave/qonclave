#!/usr/bin/env python3
"""
test_geniex.py — minimal GenieX smoke test.

Runs a tiny Qwen3 GGUF model through GenieX to confirm the SDK works on this
Snapdragon X box (one-shot + streaming generation). Run inside the geniex-env
built by hub/setup_hub.ps1, from the repo root:

    .\\hub\\geniex-env\\Scripts\\Activate.ps1     # Windows ARM64
    python hub\\tests\\test_geniex.py

If GenieX / the model isn't available it prints a clear error instead of a
raw traceback.
"""

import platform
import sys


def main():
    # GenieX only ships ARM64 wheels; bail early with a clear message otherwise.
    machine = platform.machine()
    print(f"platform.machine() = {machine}")
    if "ARM64" not in machine.upper() and "AARCH64" not in machine.upper():
        print("[!] This is not an ARM64 interpreter. GenieX will not run here.")
        # Continue anyway so the import error is visible if the user insists.

    from geniex import AutoModelForCausalLM

    model = AutoModelForCausalLM.from_pretrained(
        "Qwen/Qwen3-0.6B-GGUF",     # HF repo id of a GGUF model, or a local .gguf path
        device_map="auto",          # "auto" | "cpu" | "gpu" | "npu" | "hybrid"
                                    # | "<runtime>" | "<runtime>:<compute-unit>"
                                    # auto -> npu for both llama_cpp and qairt
    )

    messages = [{"role": "user", "content": "What is 2+2?"}]
    prompt = model.tokenizer.apply_chat_template(
        messages, add_generation_prompt=True,
    )

    # One-shot
    print("\n--- one-shot ---")
    output = model.generate(prompt, max_new_tokens=256)
    print(output.text)
    print(f"[{output.profile.generated_tokens} tok, "
          f"{output.profile.decode_speed:.1f} tok/s, stop={output.profile.stop_reason}]")

    # Streaming
    print("\n--- streaming ---")
    streamer = model.generate(prompt, max_new_tokens=256, stream=True)
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
        print(f"\n[!] GenieX test failed: {type(e).__name__}: {e}")
        sys.exit(1)
