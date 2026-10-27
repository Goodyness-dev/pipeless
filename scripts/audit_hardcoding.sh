#!/usr/bin/env bash
set -e
echo "=== 1. Checking suspicious literal return patterns in app/ ==="
grep -Rn "return {" app/ run.py || true

echo "=== 2. Checking if output_examples is referenced at runtime ==="
if grep -Rn "output_examples" app/ run.py; then
    echo "FAIL: output_examples referenced in runtime code!"
    exit 1
else
    echo "OK: output_examples is not read by runtime code."
fi

echo "=== 3. Checking hard-coded demo numbers/entities in runtime code ==="
if grep -RniE "0000051143" app/ run.py; then
    echo "FAIL: hard-coded test oracle detected in runtime code!"
    exit 1
else
    echo "OK: no hard-coded test oracle found in app/ or run.py."
fi

echo "=== 4. Scanning for plaintext secrets ==="
if grep -RniE "(api[_-]?key|token|password)\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]" app/ run.py scripts/ caller_demo/; then
    echo "FAIL: possible plaintext secret detected!"
    exit 1
else
    echo "OK: no plaintext secrets found."
fi

echo "=== AUDIT PASSED ==="
