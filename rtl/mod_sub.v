// mod_sub — Modular subtraction in Z_q, q=3329
//
// Computes (a - b) mod q for a, b in [0, q-1].
// Uses 13-bit unsigned subtraction: diff = {1'b0,a} - {1'b0,b}.
// If a < b, bit[12] is set (borrow) and cond_add_q corrects by adding Q.
//
// Combinational, no clock needed.

module mod_sub (
    input  wire [11:0] a,       // [0, 3328]
    input  wire [11:0] b,       // [0, 3328]
    output wire [11:0] result   // [0, 3328]
);

    wire [12:0] diff;

    assign diff = {1'b0, a} - {1'b0, b};

    cond_add_q u_cond_add (
        .a      (diff),
        .result (result)
    );

endmodule
