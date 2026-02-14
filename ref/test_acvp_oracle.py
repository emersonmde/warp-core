#!/usr/bin/env python3
"""Validate the FIPS 203 oracle (kyber_acvp.py) against NIST ACVP test vectors.

Downloads ML-KEM test vectors from the usnistgov/ACVP-Server GitHub repository,
caches them locally in ref/acvp_vectors/, and runs all ML-KEM-768 test cases
through keygen_full, encaps_full, and decaps_full.

This is the gating step: if the oracle doesn't match ACVP vectors, hardware
tests against these vectors are meaningless.

Usage:
    python ref/test_acvp_oracle.py
"""

import json
import os
import sys
import urllib.request

# Add ref/ to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kyber_acvp import keygen_full, encaps_full, decaps_full

# ═══════════════════════════════════════════════════════════════════
# ACVP Vector URLs (usnistgov/ACVP-Server on GitHub)
# ═══════════════════════════════════════════════════════════════════

BASE_URL = "https://raw.githubusercontent.com/usnistgov/ACVP-Server/master/gen-val/json-files"

VECTOR_URLS = {
    "keygen_prompt": f"{BASE_URL}/ML-KEM-keyGen-FIPS203/prompt.json",
    "keygen_results": f"{BASE_URL}/ML-KEM-keyGen-FIPS203/expectedResults.json",
    "encapdecap_prompt": f"{BASE_URL}/ML-KEM-encapDecap-FIPS203/prompt.json",
    "encapdecap_results": f"{BASE_URL}/ML-KEM-encapDecap-FIPS203/expectedResults.json",
}

CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "acvp_vectors")

PARAM_SET = "ML-KEM-768"


# ═══════════════════════════════════════════════════════════════════
# Download / Cache
# ═══════════════════════════════════════════════════════════════════

def fetch_json(name):
    """Download and cache an ACVP JSON file. Returns parsed JSON."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"{name}.json")

    if os.path.exists(cache_path):
        with open(cache_path, 'r') as f:
            return json.load(f)

    url = VECTOR_URLS[name]
    print(f"  Downloading {name} from {url} ...")
    req = urllib.request.Request(url, headers={"User-Agent": "warp-core-acvp/1.0"})
    with urllib.request.urlopen(req) as resp:
        data = resp.read()

    with open(cache_path, 'wb') as f:
        f.write(data)

    return json.loads(data)


def get_test_groups(prompt_json, results_json, param_set):
    """Match prompt and results test groups by tgId, filtering by parameterSet."""
    prompt_groups = {g["tgId"]: g for g in prompt_json["testGroups"]
                     if g.get("parameterSet") == param_set}
    results_groups = {g["tgId"]: g for g in results_json["testGroups"]
                      if g["tgId"] in prompt_groups}

    merged = []
    for tg_id in sorted(prompt_groups.keys()):
        pg = prompt_groups[tg_id]
        rg = results_groups[tg_id]
        # Build tcId -> results map
        results_map = {tc["tcId"]: tc for tc in rg["tests"]}
        merged.append((pg, results_map))
    return merged


# ═══════════════════════════════════════════════════════════════════
# Test Runners
# ═══════════════════════════════════════════════════════════════════

def test_keygen():
    """Validate keygen_full against all ML-KEM-768 keyGen ACVP vectors."""
    print("\n=== ML-KEM-768 KeyGen ===")
    prompt = fetch_json("keygen_prompt")
    results = fetch_json("keygen_results")

    groups = get_test_groups(prompt, results, PARAM_SET)
    total = 0
    passed = 0

    for pg, results_map in groups:
        for tc in pg["tests"]:
            tc_id = tc["tcId"]
            d = bytes.fromhex(tc["d"])
            z = bytes.fromhex(tc["z"])

            exp = results_map[tc_id]
            exp_ek = bytes.fromhex(exp["ek"])
            exp_dk = bytes.fromhex(exp["dk"])

            ek, dk = keygen_full(d, z)
            total += 1

            if ek == exp_ek and dk == exp_dk:
                passed += 1
            else:
                print(f"  FAIL tcId={tc_id}:")
                if ek != exp_ek:
                    print(f"    ek mismatch: got {len(ek)} bytes, expected {len(exp_ek)} bytes")
                    # Find first differing byte
                    for i in range(min(len(ek), len(exp_ek))):
                        if ek[i] != exp_ek[i]:
                            print(f"    first diff at byte {i}: got 0x{ek[i]:02x}, expected 0x{exp_ek[i]:02x}")
                            break
                if dk != exp_dk:
                    print(f"    dk mismatch: got {len(dk)} bytes, expected {len(exp_dk)} bytes")

    print(f"  KeyGen: {passed}/{total} passed")
    return passed == total


def test_encaps():
    """Validate encaps_full against all ML-KEM-768 encapsulation ACVP vectors."""
    print("\n=== ML-KEM-768 Encapsulation ===")
    prompt = fetch_json("encapdecap_prompt")
    results = fetch_json("encapdecap_results")

    groups = get_test_groups(prompt, results, PARAM_SET)
    total = 0
    passed = 0

    for pg, results_map in groups:
        if pg.get("function") != "encapsulation":
            continue

        for tc in pg["tests"]:
            tc_id = tc["tcId"]
            ek = bytes.fromhex(tc["ek"])
            m = bytes.fromhex(tc["m"])

            exp = results_map[tc_id]
            exp_c = bytes.fromhex(exp["c"])
            exp_k = bytes.fromhex(exp["k"])

            K_val, c = encaps_full(ek, m)
            total += 1

            if c == exp_c and K_val == exp_k:
                passed += 1
            else:
                print(f"  FAIL tcId={tc_id}:")
                if c != exp_c:
                    print(f"    c mismatch ({len(c)} vs {len(exp_c)} bytes)")
                if K_val != exp_k:
                    print(f"    K mismatch")

    print(f"  Encapsulation: {passed}/{total} passed")
    return passed == total


def test_decaps():
    """Validate decaps_full against all ML-KEM-768 decapsulation ACVP vectors."""
    print("\n=== ML-KEM-768 Decapsulation ===")
    prompt = fetch_json("encapdecap_prompt")
    results = fetch_json("encapdecap_results")

    groups = get_test_groups(prompt, results, PARAM_SET)
    total = 0
    passed = 0

    for pg, results_map in groups:
        if pg.get("function") != "decapsulation":
            continue

        for tc in pg["tests"]:
            tc_id = tc["tcId"]
            dk = bytes.fromhex(tc["dk"])
            c = bytes.fromhex(tc["c"])

            exp = results_map[tc_id]
            exp_k = bytes.fromhex(exp["k"])

            K_val = decaps_full(dk, c)
            total += 1

            if K_val == exp_k:
                passed += 1
            else:
                print(f"  FAIL tcId={tc_id}: K mismatch")

    print(f"  Decapsulation: {passed}/{total} passed")
    return passed == total


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    print("NIST ACVP Oracle Validation for ML-KEM-768")
    print("=" * 50)

    all_ok = True
    all_ok &= test_keygen()
    all_ok &= test_encaps()
    all_ok &= test_decaps()

    print("\n" + "=" * 50)
    if all_ok:
        print("ALL TESTS PASSED -- Oracle matches ACVP vectors")
        return 0
    else:
        print("SOME TESTS FAILED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
