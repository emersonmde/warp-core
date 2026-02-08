#!/usr/bin/env python3
"""Standalone verification of cond_add_q oracle against ground truth.

This script does NOT trust the cond_add_q oracle. It independently verifies
that the oracle's output matches (a - b) % q for every valid (a, b) pair
in [0, q-1] x [0, q-1]. That's 3329^2 = 11,082,241 checks.

It also verifies the mod_sub and ntt_butterfly oracles against plain
Python arithmetic to close the self-referential loop.

Run: python3 ref/verify_cond_add_q.py
"""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from kyber_math import (
    KYBER_Q, cond_add_q, mod_sub, ntt_butterfly, barrett_reduce, mod_add
)


def verify_cond_add_q_exhaustive():
    """Verify cond_add_q against (a-b) % q for ALL valid (a, b) pairs.

    Valid inputs to cond_add_q are 13-bit results of {1'b0,a} - {1'b0,b}
    where a, b in [0, q-1]. This covers:
      - a >= b: diff in [0, 3328], bit[12] = 0
      - a < b:  diff in [4864, 8191], bit[12] = 1
    """
    q = KYBER_Q
    errors = 0
    checked = 0

    for a in range(q):
        for b in range(q):
            # Simulate 13-bit unsigned subtraction (same as hardware)
            if a >= b:
                diff = a - b
            else:
                diff = (1 << 13) + a - b  # wraps with borrow

            oracle_result = cond_add_q(diff)
            ground_truth = (a - b) % q

            if oracle_result != ground_truth:
                print(f"MISMATCH: a={a}, b={b}, diff=0x{diff:04x}, "
                      f"oracle={oracle_result}, truth={ground_truth}")
                errors += 1
                if errors >= 20:
                    print("... stopping after 20 errors")
                    return errors
            checked += 1

        # Progress every 500 rows
        if a % 500 == 0:
            print(f"  cond_add_q: checked a=0..{a} ({checked:,} pairs so far)")

    print(f"cond_add_q: {checked:,} pairs checked, {errors} errors")
    return errors


def verify_mod_sub_exhaustive():
    """Verify mod_sub oracle against (a-b) % q for all (a, b) pairs."""
    q = KYBER_Q
    errors = 0
    checked = 0

    for a in range(q):
        for b in range(q):
            oracle_result = mod_sub(a, b)
            ground_truth = (a - b) % q

            if oracle_result != ground_truth:
                print(f"MISMATCH mod_sub: a={a}, b={b}, "
                      f"oracle={oracle_result}, truth={ground_truth}")
                errors += 1
                if errors >= 20:
                    print("... stopping after 20 errors")
                    return errors
            checked += 1

        if a % 500 == 0:
            print(f"  mod_sub: checked a=0..{a} ({checked:,} pairs so far)")

    print(f"mod_sub: {checked:,} pairs checked, {errors} errors")
    return errors


def verify_ntt_butterfly_sampled():
    """Verify ntt_butterfly oracle against plain arithmetic.

    Exhaustive over all triples is infeasible (3329^3 ≈ 37B), so we:
    1. Check all (even, odd) pairs with zeta=1 (3329^2 checks)
    2. Check 1M random triples
    """
    import random
    q = KYBER_Q
    errors = 0

    # Part 1: zeta=1, all (even, odd) pairs
    # t = (1 * odd) % q = odd
    # even_out = (even + odd) % q
    # odd_out = (even - odd) % q
    checked = 0
    print("  ntt_butterfly zeta=1: checking all (even, odd) pairs...")
    for even in range(q):
        for odd in range(q):
            e_out, o_out = ntt_butterfly(even, odd, 1)
            exp_even = (even + odd) % q
            exp_odd = (even - odd) % q

            if e_out != exp_even or o_out != exp_odd:
                print(f"MISMATCH butterfly: even={even}, odd={odd}, zeta=1, "
                      f"got=({e_out},{o_out}), expected=({exp_even},{exp_odd})")
                errors += 1
                if errors >= 20:
                    return errors
            checked += 1

        if even % 500 == 0:
            print(f"    checked even=0..{even} ({checked:,} pairs)")

    print(f"  ntt_butterfly zeta=1: {checked:,} pairs, {errors} errors")

    # Part 2: 1M random triples against plain Python
    rng = random.Random(99)
    n_samples = 1_000_000
    print(f"  ntt_butterfly random: checking {n_samples:,} triples...")
    for i in range(n_samples):
        even = rng.randint(0, q - 1)
        odd = rng.randint(0, q - 1)
        zeta = rng.randint(0, q - 1)

        e_out, o_out = ntt_butterfly(even, odd, zeta)
        t = (zeta * odd) % q
        exp_even = (even + t) % q
        exp_odd = (even - t) % q

        if e_out != exp_even or o_out != exp_odd:
            print(f"MISMATCH butterfly: even={even}, odd={odd}, zeta={zeta}, "
                  f"got=({e_out},{o_out}), expected=({exp_even},{exp_odd})")
            errors += 1
            if errors >= 20:
                return errors

    print(f"  ntt_butterfly random: {n_samples:,} triples, {errors} errors")
    return errors


def main():
    total_errors = 0

    print("=" * 60)
    print("Independent verification of Milestone 2 oracle functions")
    print("Ground truth: plain Python (a - b) % q and (a * b) % q")
    print("=" * 60)

    print("\n--- 1. cond_add_q: all 3329^2 valid (a,b) pairs ---")
    total_errors += verify_cond_add_q_exhaustive()

    print("\n--- 2. mod_sub: all 3329^2 valid (a,b) pairs ---")
    total_errors += verify_mod_sub_exhaustive()

    print("\n--- 3. ntt_butterfly: zeta=1 exhaustive + 1M random ---")
    total_errors += verify_ntt_butterfly_sampled()

    print("\n" + "=" * 60)
    if total_errors == 0:
        print("ALL CHECKS PASSED — oracles match ground truth")
    else:
        print(f"FAILED — {total_errors} total errors")
    print("=" * 60)

    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
