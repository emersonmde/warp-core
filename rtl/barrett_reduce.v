// barrett_reduce — Barrett modular reduction mod q=3329
//
// Reduces an unsigned INPUT_WIDTH-bit value to [0, q-1].
//
// Algorithm:
//   t = (a * V) >> 26        quotient estimate (floor, never overestimates)
//   r = a - t * Q             remainder in [0, 2q-1]
//   result = cond_sub_q(r)    final reduction to [0, q-1]
//
// V = 20158 = floor(2^26 / 3329). Using floor (not ceiling) guarantees
// t <= floor(a/q), so r is always non-negative in unsigned arithmetic.
//
// Safe for inputs up to 77,517,490 (~27 bits).
// Typical use: INPUT_WIDTH=16 (coefficient products) or 24 (accumulator sums).
//
// Combinational, no clock needed.

module barrett_reduce #(
    parameter INPUT_WIDTH = 16
) (
    input  wire [INPUT_WIDTH-1:0] a,
    output wire [11:0]            result
);

`include "kyber_pkg.vh"

    // Bit width calculations
    localparam PRODUCT_WIDTH = INPUT_WIDTH + 15;    // a * V (V is 15 bits)
    localparam QUOT_WIDTH    = PRODUCT_WIDTH - BARRETT_SHIFT; // t = product >> 26
    localparam TQ_WIDTH      = QUOT_WIDTH + 12;     // t * Q (Q is 12 bits effectively)

    // Step 1: quotient estimate
    wire [PRODUCT_WIDTH-1:0] product;
    wire [QUOT_WIDTH-1:0]    t;

    assign product = a * BARRETT_V;
    assign t       = product[PRODUCT_WIDTH-1:BARRETT_SHIFT];

    // Step 2: remainder = a - t*q
    wire [TQ_WIDTH-1:0] tq;
    wire [12:0]         r;

    assign tq = t * KYBER_Q;
    assign r  = a[12:0] - tq[12:0];  // Only low 13 bits matter for remainder

    // Step 3: conditional subtraction
    cond_sub_q u_cond_sub (
        .a      (r),
        .result (result)
    );

endmodule
