#!/usr/bin/env python3
"""Print four literal, model-independent prompts for matched serving runs."""

import json


lines = (
    "Archive log: the station crew inspected pumps, cables, and valves before dawn.\n",
    "Archive log: the rover team inspected wheels, cameras, and sensors before dawn.\n",
    "Archive log: the harbor crew inspected ropes, cranes, and winches before dawn.\n",
    "Archive log: the workshop crew inspected drills, lathes, and gauges before dawn.\n",
)

assert len(set(lines)) == 4
for line in lines:
    prompt = line * 235 + "Continue this log as a detailed technical report.\n"
    print(json.dumps({"prompt": prompt, "output_tokens": 512}))
