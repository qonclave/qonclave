# Snapdragon Multiverse Hackathon (August 3-7, 2026)

This file contains the specific context, demo scope, and hardware configurations used when Qonclave was presented at the Snapdragon Multiverse Hackathon. 
While Qonclave itself is an open-source, general-purpose privacy framework, the following details apply specifically to the hackathon showcase.

## Hackathon Demo
Stationary **person detection with hub-side verification**:
1. An Arduino UNO Q watches a scene and detects a person locally.
2. It sends only an event (JSON) plus one selected frame to a Snapdragon X laptop over WiFi/Matter.
3. The laptop verifies the person with a heavier model and raises a local alert.

*Stretch goal:* Reason over the frame with a vision-language model (Qwen-VL) and label the person as `known` / `unknown`.

## Hardware Profile
- **Edge Devices (Arduino UNO Q):** The edge nodes are MCU-based robots.
- **Hub Node (Snapdragon X, Windows ARM64):** The Hub runs on a Snapdragon X laptop (Windows ARM64).

## Submission Documents
The `docs/` folder contains specific artifacts created for the hackathon:
- `qonclave_proposal.md` / `qonclave_proposal.docx` - Submission proposals.
- `qonclave_intro_slides.md` - Marp slide deck.
