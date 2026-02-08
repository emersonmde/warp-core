# Warp Core

CRYSTALS-Kyber (ML-KEM / FIPS 203) hardware accelerator targeting Xilinx Artix-7 FPGAs.

All arithmetic operates in Z_q where q = 3329.

## Status

**Milestone 1 (Modular Arithmetic) — complete.**

| Module | Description | Verification |
|--------|-------------|--------------|
| `cond_sub_q` | Conditional subtraction, [0, 2q-1] → [0, q-1] | Exhaustive |
| `barrett_reduce` | Barrett reduction mod 3329, parameterized width | Exhaustive (16-bit) + sampled (27-bit) |

See [docs/architecture.md](docs/architecture.md) for the full roadmap and block diagrams.

## Prerequisites

- [Icarus Verilog](http://iverilog.icarus.com/) 12.0+ — `brew install icarus-verilog` on macOS
- Python 3.9+
- [cocotb](https://www.cocotb.org/) 2.x — installed via pip

```bash
pip install -r requirements-test.txt
```

## Build & Test

```bash
make test                    # Run all testbenches
make test_cond_sub_q         # Run one module's tests
make test_barrett_reduce
```

## Waveforms

```bash
make waves_cond_sub_q        # Run tests + dump FST waveforms
make waves_barrett_reduce
```

FST files are written to `tb/<module>/sim_build/<module>.fst` and can be opened with [GTKWave](http://gtkwave.sourceforge.net/) or [Surfer](https://surfer-project.org/).

## Directory Layout

```
rtl/               Synthesizable Verilog modules
rtl/kyber_pkg.vh   Shared parameters (Q, N, Barrett constants)
tb/                cocotb testbenches (one directory per module)
tb/common.mk       Shared cocotb Makefile boilerplate
ref/               Python reference implementations (test oracles)
docs/              Architecture diagrams and design notes
```

## Design Notes

**Barrett constant V = 20158** (not 20159 from the C reference). The C implementation uses `ceil(2^26 / 3329) = 20159` with signed arithmetic. Hardware uses `floor(2^26 / 3329) = 20158` to guarantee non-negative remainders in unsigned datapath. Exhaustively verified correct for all 16-bit inputs.

See [CLAUDE.md](CLAUDE.md) for full conventions and constant provenance.

## License

Apache-2.0. See [LICENSE](LICENSE).
