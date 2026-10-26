"""Build sample-tagging and stockpile analysis templates from editable sources."""
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
for name, title in (('sample-image-tags', 'styler-image-tags_Qwen Image Tags'),
                    ('stockpile-image-metadata', 'stockpile-image-metadata_Stockpile Image Metadata')):
    with tempfile.TemporaryDirectory() as directory:
        compiled = Path(directory) / 'analysis.json'
        subprocess.run(['comfy', 'workflow', 'compose',
                        str(ROOT / f'workflows/styler/blueprints/{name}.yaml'),
                        '--lib', str(ROOT / 'workflows/styler/fragments'), '--out', str(compiled)], check=True)
        graph = {key: value for key, value in json.loads(compiled.read_text()).items() if 'class_type' in value}
        target = ROOT / f'templates/api.i2json.{title}.json'
        target.write_text(json.dumps(graph, indent=2) + '\n')
        print(target)
