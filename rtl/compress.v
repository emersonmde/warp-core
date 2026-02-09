// compress — Kyber compression (FIPS 203, Section 4.2.1)
//
// Compresses a 12-bit coefficient to a D-bit value:
//   Compress_q(x, d) = round(2^d * x / q) mod 2^d
//
// Integer form:
//   numerator = (x << d) + 1664        (1664 = (q-1)/2 for rounding)
//   result    = floor(numerator / q) mod 2^d
//
// The division by q reuses the Barrett constant (V=20158, shift=26),
// extracting the quotient instead of the remainder:
//   product    = numerator * V
//   t          = product >> 26          (quotient estimate, may undercount by 1)
//   r          = numerator - t * q      (remainder, 13-bit low-bits trick)
//   correction = (r >= q) ? 1 : 0      (fix underestimate)
//   result     = (t + correction) mod 2^d
//
// D is a compile-time parameter. Valid values: 1, 4, 5, 10, 11.
// Combinational, no clock needed.

module compress #(
    parameter D = 1
) (
    input  wire [11:0]  x,       // [0, 3328]
    output wire [D-1:0] result   // [0, 2^D - 1]
);

`include "kyber_pkg.vh"

    // Step 1: numerator = (x << D) + HALF_Q
    // Width: D+12 bits (max value at D=11: 3328*2048 + 1664 = 6,817,408, fits 23 bits)
    localparam NUM_WIDTH = D + 12;
    wire [NUM_WIDTH-1:0] numerator;
    assign numerator = ({x, {D{1'b0}}}) + HALF_Q;

    // Step 2: Barrett quotient estimate
    // product = numerator * V (NUM_WIDTH + 15 bits)
    // t = product >> 26
    localparam PROD_WIDTH = NUM_WIDTH + 15;
    localparam QUOT_WIDTH = PROD_WIDTH - BARRETT_SHIFT;

    wire [PROD_WIDTH-1:0] product;
    wire [QUOT_WIDTH-1:0] t;

    assign product = numerator * BARRETT_V;
    assign t       = product[PROD_WIDTH-1:BARRETT_SHIFT];

    // Step 3: remainder check (13-bit low-bits trick, same as barrett_reduce)
    // r = numerator[12:0] - (t * Q)[12:0]
    localparam TQ_WIDTH = QUOT_WIDTH + 12;
    wire [TQ_WIDTH-1:0] tq;
    wire [12:0]         r;

    assign tq = t * KYBER_Q;
    assign r  = numerator[12:0] - tq[12:0];

    // Step 4: correction — inverted borrow means r >= Q
    wire [12:0] r_minus_q;
    wire        correction;

    assign r_minus_q  = r - {1'b0, KYBER_Q[11:0]};
    assign correction = ~r_minus_q[12];   // no borrow → r >= q → need +1

    // Step 5: final result = (t + correction) mod 2^D
    assign result = t[D-1:0] + correction;

endmodule
