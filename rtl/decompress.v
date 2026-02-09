// decompress — Kyber decompression (FIPS 203, Section 4.2.1)
//
// Decompresses a D-bit value back to a 12-bit coefficient in [0, q-1]:
//   Decompress_q(y, d) = round(q * y / 2^d)
//
// Integer form (no division):
//   result = (q * y + 2^(d-1)) >> d
//
// The addition of 2^(d-1) provides rounding (half-up).
//
// D is a compile-time parameter. Valid values: 1, 4, 5, 10, 11.
// Combinational, no clock needed.

module decompress #(
    parameter D = 1
) (
    input  wire [D-1:0]  y,       // [0, 2^D - 1]
    output wire [11:0]   result   // [0, 3328]
);

`include "kyber_pkg.vh"

    localparam PROD_WIDTH = D + 12;   // q * y: 12-bit q × D-bit y
    localparam ROUND_CONST = 1 << (D - 1);

    wire [PROD_WIDTH-1:0] product;
    wire [PROD_WIDTH-1:0] rounded;

    assign product = KYBER_Q * y;
    assign rounded = product + ROUND_CONST;
    assign result  = rounded[D +: 12];   // >> D, take 12 bits

endmodule
