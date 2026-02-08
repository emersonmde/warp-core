// cond_add_q — Conditional addition of q=3329
//
// Corrects underflow after unsigned subtraction.
// Input: 13-bit result of {1'b0,a} - {1'b0,b} where a,b in [0, q-1].
//   - If a >= b: result = a - b (no borrow, bit[12] = 0)
//   - If a < b:  result wraps, bit[12] = 1, a[11:0] = (a - b + 4096)
//     Adding Q: (a - b + 4096 + 3329) mod 4096 = (a - b + 3329) mod 4096
//     = (a - b) mod q since result is in [1, q-1].
//
// Mirror of cond_sub_q for subtraction paths.
// Combinational, no clock needed.

module cond_add_q (
    input  wire [12:0] a,       // 13-bit unsigned subtraction result; bit[12] = borrow
    output wire [11:0] result   // [0, 3328]
);

`include "kyber_pkg.vh"

    wire [12:0] sum;

    assign sum    = {1'b0, a[11:0]} + {1'b0, KYBER_Q[11:0]};
    assign result = a[12] ? sum[11:0] : a[11:0];

endmodule
