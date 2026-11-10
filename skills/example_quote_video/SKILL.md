---
name: example_quote_video
description: Makes a 9:16 quote card image (big centred quote, optional author) on a mood-coloured gradient
when_to_use: When the user wants a single powerful quote / one-liner as a visual, or a scene that is just a quote card
inputs: quote, author, mood
outputs: image
---

# Quote card skill

A tiny example of a *script skill*. The agent runs `script.py` with a clean environment.

## Contract
* Input: JSON in the `SKILL_ARGS` environment variable - `quote`, `author` (optional), `mood`
  (`motivational` | `sad` | `calm` | `energetic` | `dramatic`).
* Output folder: `SKILL_OUTPUT_DIR` (the script may only write here).
* Output: print ONE JSON line on stdout, e.g. `{"ok": true, "kind": "image", "path": "quote.jpg"}`.

## How the planner should use it
Give the scene a `"skill": {"name": "example_quote_video", "args": {"quote": "...", "author": "...",
"mood": "..."}}`. Keep the quote under 120 characters.

## Safety
No network, no secrets, writes only inside `SKILL_OUTPUT_DIR`.
