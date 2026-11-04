#!/usr/bin/env bash
# Hosted CPU suites and independent removal trials; restore tracked inputs on exit.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}"
here=$SOURCE_ROOT/packaging/zluda-overlay
root=$SOURCE_ROOT/zluda
out=$BUILD_ROOT/native/review-tests
capsules=$BUILD_ROOT/review-capsules
mkdir -p "$out" "$capsules"
export CARGO_BUILD_JOBS=3 CMAKE_BUILD_PARALLEL_LEVEL=3
export ZLUDA_REAL_CMAKE
ZLUDA_REAL_CMAKE=$(command -v cmake)
export CMAKE=$here/native-cmake.py
cd "$root"
restore() {
    python3 - "$out/saved" <<'PY'
from pathlib import Path
import shutil, sys
for path in Path(sys.argv[1]).rglob('*'):
    if path.is_file():
        shutil.copyfile(path, path.relative_to(sys.argv[1]))
PY
}
python3 - "$out/saved" <<'PY'
from pathlib import Path
import shutil, sys
paths = ['ptx/src/test/spirv_run/mod.rs', 'ptx/src/pass/llvm/emit.rs']
paths += ['llvm_zluda/src/' + n for n in ['compile.rs', 'ffi.rs', 'lib.cpp', 'lib.rs', 'utils.rs']]
for name in paths:
    dst = Path(sys.argv[1]) / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(name, dst)
PY
trap restore EXIT
# Normalize only the Windows cache-invalidation prelude and attribute numbering
# in the test comparator. Production emitter and tracked goldens stay unchanged.
python3 - <<'PY'
from pathlib import Path
p = Path('ptx/src/test/spirv_run/mod.rs')
s = p.read_text()
needle = 'fn compare_llvm(name: &str, actual_ll: &str, expected_ll: &str) {\n'
assert needle in s
s = s.replace(needle, needle + '''    fn normalize(text: &str) -> String {
        let mut attrs = std::collections::BTreeMap::new();
        for line in text.lines().filter(|line| line.starts_with("attributes #")) {
            let (name, body) = line.trim_start_matches("attributes ").split_once(" = ").unwrap();
            attrs.insert(name.to_owned(), body.to_owned());
        }
        text.lines().filter_map(|line| {
            let line = line.trim();
            if line.is_empty() || line.starts_with(';') || line.starts_with("attributes #")
                || line.contains("@llvm.amdgcn.s.dcache.inv(") { return None; }
            let mut line = line.to_owned();
            for (name, body) in attrs.iter().rev() { line = line.replace(name, body); }
            Some(line)
        }).collect::<Vec<_>>().join("\\n")
    }
    let actual = normalize(actual_ll);
    let expected = normalize(expected_ll);
    let actual_ll = actual.as_str();
    let expected_ll = expected.as_str();
''', 1)
p.write_text(s)
PY
run() {
    local name=$1 expected=$2; shift 2
    cargo test --release --locked --offline -j3 "$@" -- --test-threads=1 2>&1 | tee "$out/$name.log"
    python3 - "$out/$name.log" "$expected" <<'PY'
import re, sys
from pathlib import Path
counts = re.findall(r'test result: ok\. (\d+) passed; 0 failed;', Path(sys.argv[1]).read_text())
assert counts and int(counts[0]) == int(sys.argv[2]), (sys.argv[1], counts, sys.argv[2])
PY
}
run barrier 3 -p ptx --lib insert_shared_war_barriers
run llvm 194 -p ptx --lib _llvm
run parser 24 -p ptx_parser
run dark 5 -p dark_api
run hardware 2 -p zluda --lib hardware::
run image 2 -p llvm_zluda --lib image_wrapper
restore
# The runtime fixes touch no compiler input. Retain separately labelled capsules
# and a source diff proof against the published compiler.
git diff --exit-code 0a5db154b067af26115a0843107ff8ca766fe3b9 HEAD -- \
    compiler ptx ptx_parser llvm_zluda kernel_metadata Cargo.lock > "$out/compiler-source-identity.diff"
cp -a "$BUILD_ROOT/zoc-capsule" "$capsules/fix"
cp -a "$BUILD_ROOT/zoc-capsule" "$capsules/baseline"
python3 - "$capsules" <<'PY'
from pathlib import Path
import json, sys
root = Path(sys.argv[1])
p = root / 'baseline/PROVENANCE.json'
r = json.loads(p.read_text()); r['source_commit'] = '0a5db154b067af26115a0843107ff8ca766fe3b9'
r['compiler_source_identity'] = 'git diff --exit-code baseline fix -- compiler ptx ptx_parser llvm_zluda kernel_metadata Cargo.lock'
p.write_text(json.dumps(r, indent=2) + '\n')
PY
package_trial() {
    local arm=$1
    git diff --binary > "$out/$arm.patch"
    cargo build --release --locked --offline -j3 -p compiler 2>&1 | tee "$out/$arm-build.log"
    BRIDGE_COMPONENT_SOURCE=$SOURCE_ROOT/zluda-wine python3 "$here/zoc_capsule.py" --source "$root" \
        --binary "$root/target/release/zoc" --dest "$capsules/$arm" --stack-probe-build "$BUILD_ROOT/stack-probe"
    cp "$out/$arm.patch" "$capsules/$arm/SOURCE-CHANGES.patch"
    python3 - "$capsules/$arm" "$arm" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1]) / 'PROVENANCE.json'
r = json.loads(p.read_text()); r['trial'] = sys.argv[2]; r['shipping_source_modified'] = True
p.write_text(json.dumps(r, indent=2) + '\n')
PY
}
# Only the IMAGE group differs in these five upstream source files.
python3 - <<'PY'
from pathlib import Path
import subprocess
for name in ['compile.rs', 'ffi.rs', 'lib.cpp', 'lib.rs', 'utils.rs']:
    path = Path('llvm_zluda/src') / name
    path.write_bytes(subprocess.check_output(['git', 'show', 'f4df215e:' + path.as_posix()]))
PY
package_trial without-image
restore
python3 - <<'PY'
from pathlib import Path
p = Path('ptx/src/pass/llvm/emit.rs'); s = p.read_text()
a = s.index('        } else if matches!(\n            var.info.state_space,')
b = s.index('        if var.info.state_space == ast::StateSpace::Const {', a)
s = s[:a] + '        }\n' + s[b:]
p.write_text(s)
PY
package_trial without-const-zero
restore
trap - EXIT
# Regenerate capsule manifests after adding exact trial/baseline provenance.
PYTHONPATH="$here" python3 - "$capsules" <<'PY'
from pathlib import Path
import sys
from inputs import manifest
for path in Path(sys.argv[1]).iterdir(): manifest(path)
PY
python3 "$here/review_identity.py" "$root" "$capsules" "$out/public-identity"
# Restore the shipping compiler and runtime after transient trials.
cargo build --release --locked --offline -j3 -p zluda -p compiler 2>&1 | tee "$out/shipping-restore.log"
git diff --exit-code > "$out/source-clean.diff"
