// kyber_pkg.vh — Shared parameters for CRYSTALS-Kyber hardware
//
// Constants verified against pq-crystals/kyber C reference (params.h, reduce.h)
// and FIPS 203 (ML-KEM) specification.

localparam KYBER_Q       = 13'd3329;   // Prime modulus (FIPS 203)
localparam KYBER_N       = 9'd256;     // Polynomial degree

localparam COEFF_WIDTH   = 12;         // ceil(log2(3329)) = 12 bits for [0, 3328]

// Barrett reduction: floor(a / q) ≈ (a * V) >> SHIFT
// V = floor(2^26 / 3329) = 20158
// Using floor (not ceiling 20159) for unsigned hardware — guarantees r >= 0.
// Safe for inputs up to 77,517,490 (~27 bits).
localparam BARRETT_V     = 15'd20158;  // floor(2^26 / 3329)
localparam BARRETT_SHIFT = 26;

// Inverse NTT scaling factor: 128^-1 mod 3329 = 3303
// After 7 INTT layers, multiply all 256 coefficients by this.
// Source: pow(128, -1, 3329) verified in ref/verify_ntt.py
localparam KYBER_N_INV   = 12'd3303;
