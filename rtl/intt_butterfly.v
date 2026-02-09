// intt_butterfly — Gentleman-Sande inverse NTT butterfly for CRYSTALS-Kyber
//
// Computes:
//   even_out = (even + odd) mod q       via mod_add
//   diff     = (odd - even) mod q       via mod_sub
//   odd_out  = (zeta * diff) mod q      via Barrett reduction
//
// Note: the difference is (odd - even), matching the pq-crystals reference
// where the INTT loop computes zeta * (f[j+len] - f[j]).
//
// All inputs/outputs in [0, q-1] = [0, 3328].
// Product zeta*diff is at most 3328^2 = 11,075,584 (24 bits),
// well within Barrett's safe range of 77,517,490.
//
// Combinational, no clock needed.

module intt_butterfly (
    input  wire [11:0] even,        // [0, 3328]
    input  wire [11:0] odd,         // [0, 3328]
    input  wire [11:0] zeta,        // [0, 3328] — twiddle factor
    output wire [11:0] even_out,    // [0, 3328]
    output wire [11:0] odd_out      // [0, 3328]
);

`include "kyber_pkg.vh"

    localparam PRODUCT_WIDTH = 2 * COEFF_WIDTH;  // 24

    wire [11:0] diff;
    wire [PRODUCT_WIDTH-1:0] product;

    // Step 1: add/sub
    mod_add u_add (
        .a      (even),
        .b      (odd),
        .result (even_out)
    );

    mod_sub u_sub (
        .a      (odd),
        .b      (even),
        .result (diff)
    );

    // Step 2: multiply and reduce
    assign product = zeta * diff;

    barrett_reduce #(
        .INPUT_WIDTH (PRODUCT_WIDTH)
    ) u_barrett (
        .a      (product),
        .result (odd_out)
    );

endmodule
