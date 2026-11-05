#!/usr/bin/env python3
"""Source hooks must patch clean copies and notice changes without patch drift."""
from pathlib import Path
import subprocess
import tempfile

repo = Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    origin, obj, patches = [root / name for name in ['origin', 'obj', 'patches/demo']]
    for path in [origin, obj, patches]:
        path.mkdir(parents=True)
    (origin / 'value').write_text('before\n')
    (patches / 'one.patch').write_text('--- a/value\n+++ b/value\n@@ -1 +1 @@\n-before\n+after\n')
    makefile = root / 'Makefile'
    makefile.write_text(f'SRC := {root}\nOBJ := {obj}\ninclude {repo}/make/utility.mk\ninclude {repo}/make/rules-source.mk\n$(eval $(call rules-source,demo,{origin}))\n')
    def run():
        subprocess.run(['make', '-s', '-f', str(makefile), 'demo-source'], check=True)
    run()
    assert (obj / 'src-demo/value').read_text() == 'after\n'
    stamp = obj / '.demo-source'
    first = stamp.stat().st_mtime_ns
    cache = obj / 'src-demo/__pycache__'
    cache.mkdir()
    (cache / 'generated.pyc').write_bytes(b'generated cache')
    (obj / 'src-demo/legacy.pyc').write_bytes(b'generated cache')
    run()
    assert stamp.stat().st_mtime_ns == first, 'patch or Python cache caused an unnecessary source rebuild'
    (origin / 'extra').write_text('new\n')
    run()
    assert (obj / 'src-demo/extra').read_text() == 'new\n'
    assert (obj / 'src-demo/value').read_text() == 'after\n'
    (patches / 'one.patch').write_text('--- a/value\n+++ b/value\n@@ -1 +1 @@\n-before\n+updated\n')
    run()
    assert (obj / 'src-demo/value').read_text() == 'updated\n'
print('PASS: source patch application and incremental rebuilds')
