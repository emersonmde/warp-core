// mod_add — Modular addition in Z_q, q=3329
//
// Computes (a + b) mod q for a, b in [0, q-1].
// Sum range: [0, 2q-2] = [0, 6656], fits in 13 bits.
// cond_sub_q reduces [0, 2q-1] → [0, q-1].
//
// Combinational, no clock needed.

module mod_add (
    input  wire [11:0] a,       // [0, 3328]
    input  wire [11:0] b,       // [0, 3328]
    output wire [11:0] result   // [0, 3328]
);

    wire [12:0] sum;

    assign sum = {1'b0, a} + {1'b0, b};

    cond_sub_q u_cond_sub (
        .a      (sum),
        .result (result)
    );

endmodule
