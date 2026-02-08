// ntt_butterfly — Cooley-Tukey NTT butterfly for CRYSTALS-Kyber
//
// Computes:
//   t        = (zeta * odd) mod q      via Barrett reduction
//   even_out = (even + t) mod q        via mod_add
//   odd_out  = (even - t) mod q        via mod_sub
//
// All inputs/outputs in [0, q-1] = [0, 3328].
// Product zeta*odd is at most 3328^2 = 11,075,584 (24 bits),
// well within Barrett's safe range of 77,517,490.
//
// Combinational, no clock needed.

module ntt_butterfly (
    input  wire [11:0] even,        // [0, 3328]
    input  wire [11:0] odd,         // [0, 3328]
    input  wire [11:0] zeta,        // [0, 3328] — twiddle factor
    output wire [11:0] even_out,    // [0, 3328]
    output wire [11:0] odd_out      // [0, 3328]
);

`include "kyber_pkg.vh"

    localparam PRODUCT_WIDTH = 2 * COEFF_WIDTH;  // 24

    wire [PRODUCT_WIDTH-1:0] product;
    wire [11:0] t;

    // Step 1: multiply and reduce
    assign product = zeta * odd;

    barrett_reduce #(
        .INPUT_WIDTH (PRODUCT_WIDTH)
    ) u_barrett (
        .a      (product),
        .result (t)
    );

    // Step 2: butterfly add/sub
    mod_add u_add (
        .a      (even),
        .b      (t),
        .result (even_out)
    );

    mod_sub u_sub (
        .a      (even),
        .b      (t),
        .result (odd_out)
    );

endmodule
