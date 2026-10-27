"""Stage an explicit source allowlist and reject private credential values."""
import hashlib
import json
import re
from pathlib import Path
import shutil
import zipfile
from dotenv import dotenv_values


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / 'submission'
    stage = output / 'source'
    stage.mkdir(parents=True, exist_ok=True)
    selected = []
    for folder in ['app', 'scripts', 'tests', 'caller_demo', 'input_examples']:
        selected += [p for p in (root / folder).rglob('*') if p.is_file()
                     and '__pycache__' not in p.parts and p.suffix in {'.py', '.html', '.json', '.md', '.sql'}]
    selected += [root / p for p in ['README.md', 'requirements.txt', 'Dockerfile', 'run.py', 'pytest.ini', '.env.example', '.gitignore', '.dockerignore']]
    secret_values = set()
    for env in [root / '.env', root / 'zetaris-platform' / '.env']:
        if not env.exists():
            continue
        for key, value in dotenv_values(env).items():
            if value and len(value) >= 16 and any(part in key.upper() for part in ['TOKEN', 'PASSWORD', 'SECRET', 'API_KEY']):
                if not value.lower().startswith(('your-', 'choose-', 'replace-', 'changeme')):
                    secret_values.add(value.encode())
    manifest = []
    for source in selected:
        content = source.read_bytes()
        if any(value in content for value in secret_values) or re.search(rb'(?:nvapi-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9_-]{24,}|ak[0-9a-z]{45,})', content):
            raise RuntimeError(f'Private credential value found in selected file: {source.relative_to(root)}')
        target = stage / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        manifest.append({'path': str(source.relative_to(root)), 'sha256': hashlib.sha256(content).hexdigest()})
    disclosure = output / 'BUILD_DISCLOSURE.md'
    if disclosure.exists():
        content = disclosure.read_bytes()
        (stage / disclosure.name).write_bytes(content)
        manifest.append({'path': disclosure.name, 'sha256': hashlib.sha256(content).hexdigest()})
    (output / 'source-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    archive = output / 'pipeless-source.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for item in manifest:
            path = Path(item['path'])
            bundle.write(stage / path, arcname=path.as_posix())
    print(json.dumps({'files': len(manifest), 'archive': str(archive), 'credential_scan': 'passed'}))


if __name__ == '__main__':
    main()
