// basemul_unit — Single 2x2 basemul for CRYSTALS-Kyber NTT domain
//
// Computes (a0 + a1*X) * (b0 + b1*X) mod (X^2 - zeta) in Z_q:
//   c0 = (a0*b0 + a1*b1*zeta) mod q
//   c1 = (a0*b1 + a1*b0)      mod q
//
// Optimized Barrett reduction usage (3 instead of 5):
//   t1   = barrett_reduce(a1*b1)              24-bit → 12-bit
//   acc0 = (a0*b0) + (t1*zeta)               25-bit, max 22,151,168
//   c0   = barrett_reduce(acc0)               25-bit → 12-bit
//   acc1 = (a0*b1) + (a1*b0)                 25-bit, max 22,151,168
//   c1   = barrett_reduce(acc1)               25-bit → 12-bit
//
// All inputs/outputs in [0, q-1] = [0, 3328].
// Combinational, no clock needed.

module basemul_unit (
    input  wire [11:0] a0,       // [0, 3328]
    input  wire [11:0] a1,       // [0, 3328]
    input  wire [11:0] b0,       // [0, 3328]
    input  wire [11:0] b1,       // [0, 3328]
    input  wire [11:0] zeta,     // [0, 3328] — gamma for this pair
    output wire [11:0] c0,       // [0, 3328]
    output wire [11:0] c1        // [0, 3328]
);

`include "kyber_pkg.vh"

    // ─── c0 path: a0*b0 + barrett(a1*b1)*zeta ────────────────

    // Step 1: reduce a1*b1 (24-bit product → 12-bit)
    wire [23:0] a1b1 = a1 * b1;
    wire [11:0] t1;

    barrett_reduce #(
        .INPUT_WIDTH (24)
    ) u_barrett_t1 (
        .a      (a1b1),
        .result (t1)
    );

    // Step 2: accumulate a0*b0 + t1*zeta (25-bit max)
    // Max: 3328*3328 + 3328*3328 = 2 * 11,075,584 = 22,151,168 < 77,517,490
    wire [23:0] a0b0   = a0 * b0;
    wire [23:0] t1zeta = t1 * zeta;
    wire [24:0] acc_c0 = {1'b0, a0b0} + {1'b0, t1zeta};

    barrett_reduce #(
        .INPUT_WIDTH (25)
    ) u_barrett_c0 (
        .a      (acc_c0),
        .result (c0)
    );

    // ─── c1 path: a0*b1 + a1*b0 ──────────────────────────────

    wire [23:0] a0b1   = a0 * b1;
    wire [23:0] a1b0   = a1 * b0;
    wire [24:0] acc_c1 = {1'b0, a0b1} + {1'b0, a1b0};

    barrett_reduce #(
        .INPUT_WIDTH (25)
    ) u_barrett_c1 (
        .a      (acc_c1),
        .result (c1)
    );

endmodule
