# Warp Core — CRYSTALS-Kyber (ML-KEM) Hardware Accelerator

## Project Overview

Hardware implementation of CRYSTALS-Kyber (FIPS 203 / ML-KEM) targeting Xilinx Artix-7 FPGAs.
All arithmetic operates in Z_q where q=3329.

## Build & Test

```bash
pip install -r requirements-test.txt   # One-time: cocotb, kyber-py
make test                              # Run all testbenches
make test_cond_sub_q                   # Exhaustive conditional subtraction test
make test_barrett_reduce               # Exhaustive 16-bit + structured 24-bit Barrett test
make waves_cond_sub_q                  # Run tests + dump waveforms (FST)
make waves_barrett_reduce              # Run tests + dump waveforms (FST)
```

**Simulator:** Icarus Verilog (`iverilog`). Install via `brew install icarus-verilog` on macOS.

**Waveforms:** `make waves_<module>` dumps FST traces to `tb/<module>/sim_build/<module>.fst`.
Open with GTKWave: `gtkwave tb/cond_sub_q/sim_build/cond_sub_q.fst`

## Directory Layout

```
rtl/               Synthesizable Verilog modules
rtl/kyber_pkg.vh   Shared parameters (Q, N, Barrett constants)
tb/                cocotb testbenches (one directory per module)
tb/common.mk       Shared cocotb Makefile boilerplate
ref/               Python reference implementations (test oracles)
docs/              Architecture diagrams and design notes
```

## Conventions

- **HDL:** Verilog-2001, one module per file
- **Parameters:** Shared constants in `kyber_pkg.vh`, included via `` `include ``
- **Testing:** cocotb + Python oracle (`ref/kyber_math.py`), cross-checked against `a % 3329`
- **Naming:** `snake_case` for signals and modules
- **Bit widths:** Always explicit, documented in module headers

## Key Constants (verified against pq-crystals/kyber C reference)

| Constant | Value | Source |
|----------|-------|--------|
| KYBER_Q | 3329 | FIPS 203, params.h |
| KYBER_N | 256 | FIPS 203, params.h |
| BARRETT_V | 20158 | floor(2^26 / 3329), unsigned hardware adaptation |
| BARRETT_SHIFT | 26 | Standard Barrett parameter |

**Note on BARRETT_V:** The C reference uses 20159 (ceiling) with signed arithmetic. We use 20158 (floor)
for unsigned hardware — guarantees non-negative remainders. Exhaustively verified correct for all
16-bit inputs and sampled up to 27 bits.
