#!/usr/bin/env bash
# Focused CPU execution plus native test compilation; never execute GPU cases.
set -euo pipefail
: "${SOURCE_ROOT:?}" "${BUILD_ROOT:?}"
[[ ${CLEANUP_TESTS:-0} == 1 ]]
jobs=${JOBS:-3}
[[ $jobs =~ ^[1-3]$ ]]
export CARGO_BUILD_JOBS=$jobs CMAKE_BUILD_PARALLEL_LEVEL=$jobs
export ZLUDA_REAL_CMAKE
ZLUDA_REAL_CMAKE=$(command -v cmake)
export CMAKE=$SOURCE_ROOT/packaging/zluda-overlay/native-cmake.py
cd "$SOURCE_ROOT/zluda"
mkdir -p "$BUILD_ROOT/native/cleanup-tests"
shipping_before=$(sha256sum target/release/libnvcuda.so | cut -d' ' -f1)
# This compiles every retained CUDA comparison, without opening a GPU or CUDA driver.
cargo test --release --locked --offline -p zluda -j"$jobs" --no-run \
    2>&1 | tee "$BUILD_ROOT/native/cleanup-tests/native-compile.log"
run_reclaim_cpu_tests() {
    local case
    for case in texref_reclaim_cached_shown_and_shadow texref_reclaim_failure_keeps_ownership texref_reclaim_shared_budget_and_shown; do
        cargo test --release --locked --offline -p zluda -j"$jobs" "r#impl::tex::tests::$case" \
            -- --exact --test-threads=1 || return
    done
}
run_reclaim_cpu_tests 2>&1 | tee "$BUILD_ROOT/native/cleanup-tests/reclaim-cpu.log"
# Restore the full already-public synthetic tests only in this ephemeral CI
# checkout. No game/NVIDIA corpus or oracle bytes are supplied to the runner.
restore_tests() {
    # Read exact tracked bytes without checkout hooks; git-lfs is not in the SDK.
    python3 - <<'PYRESTORE'
from pathlib import Path
import subprocess
for name in ['tex', 'driver', 'surf']:
    path = Path('zluda/src/impl') / (name + '.rs')
    path.write_bytes(subprocess.check_output(['git', 'show', 'HEAD:' + path.as_posix()]))
PYRESTORE
}
trap restore_tests EXIT
python3 - <<'PY'
import hashlib, json, os
from pathlib import Path
import subprocess
origin = '854720b624f2a61a447d9c3b69a3f986470a0149'
records = []
for name in ['tex', 'driver', 'surf']:
    path = Path('zluda/src/impl') / (name + '.rs')
    before = path.read_bytes()
    original = subprocess.check_output(['git', 'show', origin + ':' + path.as_posix()])
    assert before.split(b'#[cfg(test)]', 1)[0] == original.split(b'#[cfg(test)]', 1)[0]
    records.append({'path': str(path), 'compact_sha256': hashlib.sha256(before).hexdigest(),
                    'original_sha256': hashlib.sha256(original).hexdigest(), 'production_identical': True})
    path.write_bytes(original)
out = Path(os.environ['BUILD_ROOT']) / 'native/cleanup-tests/PRIVATE-OVERLAY.json'
out.write_text(json.dumps({'origin': origin, 'files': records}, indent=2) + '\n')
PY
cargo test --release --locked --offline -p zluda -j"$jobs" --no-run \
    2>&1 | tee "$BUILD_ROOT/native/cleanup-tests/original-native-compile.log"
run_reclaim_cpu_tests 2>&1 | tee "$BUILD_ROOT/native/cleanup-tests/original-reclaim-cpu.log"
restore_tests
trap - EXIT
cargo build --release --locked --offline -p zluda -j"$jobs" \
    2>&1 | tee "$BUILD_ROOT/native/cleanup-tests/shipping-restore.log"
shipping_after=$(sha256sum target/release/libnvcuda.so | cut -d' ' -f1)
[[ $shipping_before == "$shipping_after" ]] || { echo 'Test replay changed shipping library bytes' >&2; exit 1; }
printf '%s\n' "$shipping_after" > "$BUILD_ROOT/native/cleanup-tests/SHIPPING-UNCHANGED.sha256"
# LLVM/PTX production inputs are unchanged; their prior qualification is retained.
