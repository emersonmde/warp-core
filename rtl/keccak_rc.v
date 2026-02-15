/*
 * Copyright (c) 2026 Matthew Emerson
 * SPDX-License-Identifier: Apache-2.0
 *
 * Keccak-f[1600] round constant lookup — combinational
 * 24 round constants from FIPS 202 / keccak.team
 *
 * Source: vilya project (github.com/emersonmde/vilya)
 */

`default_nettype none

module keccak_rc (
    input  wire [4:0]  round_num,
    output reg  [63:0] rc
);

    always @(*) begin
        case (round_num)
            5'd 0: rc = 64'h0000000000000001;
            5'd 1: rc = 64'h0000000000008082;
            5'd 2: rc = 64'h800000000000808A;
            5'd 3: rc = 64'h8000000080008000;
            5'd 4: rc = 64'h000000000000808B;
            5'd 5: rc = 64'h0000000080000001;
            5'd 6: rc = 64'h8000000080008081;
            5'd 7: rc = 64'h8000000000008009;
            5'd 8: rc = 64'h000000000000008A;
            5'd 9: rc = 64'h0000000000000088;
            5'd10: rc = 64'h0000000080008009;
            5'd11: rc = 64'h000000008000000A;
            5'd12: rc = 64'h000000008000808B;
            5'd13: rc = 64'h800000000000008B;
            5'd14: rc = 64'h8000000000008089;
            5'd15: rc = 64'h8000000000008003;
            5'd16: rc = 64'h8000000000008002;
            5'd17: rc = 64'h8000000000000080;
            5'd18: rc = 64'h000000000000800A;
            5'd19: rc = 64'h800000008000000A;
            5'd20: rc = 64'h8000000080008081;
            5'd21: rc = 64'h8000000000008080;
            5'd22: rc = 64'h0000000080000001;
            5'd23: rc = 64'h8000000080008008;
            default: rc = 64'h0000000000000000;
        endcase
    end

endmodule
