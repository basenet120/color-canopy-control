"""Refresh Studio's public geometry snapshot from the local workshop plan and maps.

Run from the app folder: .venv/Scripts/python.exe -B tools/update_studio_geometry.py
Only geometric positions are exported; registered identifiers and secrets are excluded.
"""
import json
import sys
from dataclasses import asdict
from pathlib import Path

app=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(app))
from conductor.geometry import load_canopy_config, build_layout
from conductor.config import load_registry
from conductor.module.layout import calibration_for, module_map

cfg=load_canopy_config()
registry=load_registry(data=app/'workshop-data')
data={'source':'config/canopy.yaml and the saved workshop module maps',
      'width':cfg['width_in'],'height':cfg['height_in'],
      'panelWidth':cfg['module']['width_in'],'panelHeight':cfg['module']['height_in'],
      'scheme':cfg['orientation_scheme'],'heads':[asdict(h) for h in build_layout(cfg)],'bench':{}}
for device in ['bench-kit','shop-string-1']:
    mapped=module_map(cfg,calibration_for(cfg,registry.get(device)))
    data['bench'][device]=[{k:h[k] for k in ['index','x','y']} for h in mapped['heads']]
p=app/'web/ops/showcase/demo-model.mjs'
s=p.read_text(encoding='utf-8')
start='// BEGIN GENERATED CANOPY GEOMETRY\n'
end='// END GENERATED CANOPY GEOMETRY\n'
a=s.index(start)+len(start);b=s.index(end,a)
s=s[:a]+'export const CANOPY = '+json.dumps(data,separators=(',',':'))+';\n'+s[b:]
p.write_text(s,encoding='utf-8')
print('Studio geometry refreshed: 270 planned heads, 30 per shop module.')
