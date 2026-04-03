#!/usr/bin/env python3
"""
One-time remap for base.cave: L (loose soil) → M (machine metal).
Run after exporting base.cave from the editor:

    python assets/maptiles/remap_base.py
"""
import pathlib

path = pathlib.Path(__file__).parent / "base.cave"
text = path.read_text()

lines = text.splitlines()
out = []
for line in lines:
    if line.startswith("#") or not line:
        out.append(line)
    else:
        out.append(line.replace("L", "M"))

remapped = "\n".join(out) + "\n"
path.write_text(remapped)

data_lines = [l for l in out if not l.startswith("#") and l]
m_count = sum(l.count("M") for l in data_lines)
l_count = sum(l.count("L") for l in data_lines)
print(f"Done. M cells: {m_count}  L remaining: {l_count}")
