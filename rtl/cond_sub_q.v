// cond_sub_q — Conditional subtraction mod q=3329
//
// Reduces a value in [0, 2q-1] to [0, q-1].
// Used after additions and as the final step of Barrett reduction.
//
// Method: subtract-and-select
//   1. Compute diff = a - Q (13-bit unsigned subtraction)
//   2. If a < Q, the subtraction underflows and diff[12] = 1 (borrow)
//   3. Select: borrow ? a : diff
//
// Combinational, no clock needed.

module cond_sub_q (
    input  wire [12:0] a,       // [0, 6657] (2*3329 - 1)
    output wire [11:0] result   // [0, 3328]
);

`include "kyber_pkg.vh"

    wire [12:0] diff;

    assign diff   = a - {1'b0, KYBER_Q[11:0]};
    assign result = diff[12] ? a[11:0] : diff[11:0];

endmodule
