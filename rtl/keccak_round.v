/*
 * Copyright (c) 2026 Matthew Emerson
 * SPDX-License-Identifier: Apache-2.0
 *
 * Keccak-f[1600] single round — purely combinational
 * Implements theta, rho, pi, chi, iota steps
 *
 * State layout (FIPS 202 Section 3.1.2):
 *   Lane A[x][y] = state[(5*y + x)*64 +: 64]
 *   x in 0..4, y in 0..4, little-endian byte order within each lane
 *
 * Source: vilya project (github.com/emersonmde/vilya)
 */

`default_nettype none

module keccak_round (
    input  wire [1599:0] state_in,
    input  wire [4:0]    round_num,
    output wire [1599:0] state_out
);

    // Round constant
    wire [63:0] rc;
    keccak_rc rc_inst (
        .round_num (round_num),
        .rc        (rc)
    );

    // =========================================================================
    // THETA
    // C[x] = A[x,0] ^ A[x,1] ^ A[x,2] ^ A[x,3] ^ A[x,4]
    // D[x] = C[(x-1) mod 5] ^ ROT(C[(x+1) mod 5], 1)
    // A'[x,y] = A[x,y] ^ D[x]
    // =========================================================================

    wire [63:0] C [0:4];
    wire [63:0] D [0:4];
    wire [63:0] theta_out [0:24];

    genvar gx, gy;

    generate
        for (gx = 0; gx < 5; gx = gx + 1) begin : gen_c
            assign C[gx] = state_in[(5*0+gx)*64 +: 64]
                          ^ state_in[(5*1+gx)*64 +: 64]
                          ^ state_in[(5*2+gx)*64 +: 64]
                          ^ state_in[(5*3+gx)*64 +: 64]
                          ^ state_in[(5*4+gx)*64 +: 64];
        end
    endgenerate

    // D[x] = C[(x+4) mod 5] ^ ROT(C[(x+1) mod 5], 1)
    generate
        for (gx = 0; gx < 5; gx = gx + 1) begin : gen_d
            assign D[gx] = C[(gx + 4) % 5]
                          ^ {C[(gx + 1) % 5][62:0], C[(gx + 1) % 5][63]};
        end
    endgenerate

    generate
        for (gy = 0; gy < 5; gy = gy + 1) begin : gen_theta_y
            for (gx = 0; gx < 5; gx = gx + 1) begin : gen_theta_x
                assign theta_out[5*gy+gx] = state_in[(5*gy+gx)*64 +: 64] ^ D[gx];
            end
        end
    endgenerate

    // =========================================================================
    // RHO — rotate each lane by r[x][y] positions (left rotation)
    //
    // Rotation offsets r[x][y] (from FIPS 202 Table 2):
    //       y=0  y=1  y=2  y=3  y=4
    // x=0:   0   36    3   41   18
    // x=1:   1   44   10   45    2
    // x=2:  62    6   43   15   61
    // x=3:  28   55   25   21   56
    // x=4:  27   20   39    8   14
    //
    // Lane index = 5*y + x
    // =========================================================================

    wire [63:0] rho_out [0:24];

    // Lane  0 (x=0,y=0): rotate  0
    assign rho_out[ 0] = theta_out[ 0];
    // Lane  1 (x=1,y=0): rotate  1
    assign rho_out[ 1] = {theta_out[ 1][62:0], theta_out[ 1][63]};
    // Lane  2 (x=2,y=0): rotate 62
    assign rho_out[ 2] = {theta_out[ 2][ 1:0], theta_out[ 2][63: 2]};
    // Lane  3 (x=3,y=0): rotate 28
    assign rho_out[ 3] = {theta_out[ 3][35:0], theta_out[ 3][63:36]};
    // Lane  4 (x=4,y=0): rotate 27
    assign rho_out[ 4] = {theta_out[ 4][36:0], theta_out[ 4][63:37]};
    // Lane  5 (x=0,y=1): rotate 36
    assign rho_out[ 5] = {theta_out[ 5][27:0], theta_out[ 5][63:28]};
    // Lane  6 (x=1,y=1): rotate 44
    assign rho_out[ 6] = {theta_out[ 6][19:0], theta_out[ 6][63:20]};
    // Lane  7 (x=2,y=1): rotate  6
    assign rho_out[ 7] = {theta_out[ 7][57:0], theta_out[ 7][63:58]};
    // Lane  8 (x=3,y=1): rotate 55
    assign rho_out[ 8] = {theta_out[ 8][ 8:0], theta_out[ 8][63: 9]};
    // Lane  9 (x=4,y=1): rotate 20
    assign rho_out[ 9] = {theta_out[ 9][43:0], theta_out[ 9][63:44]};
    // Lane 10 (x=0,y=2): rotate  3
    assign rho_out[10] = {theta_out[10][60:0], theta_out[10][63:61]};
    // Lane 11 (x=1,y=2): rotate 10
    assign rho_out[11] = {theta_out[11][53:0], theta_out[11][63:54]};
    // Lane 12 (x=2,y=2): rotate 43
    assign rho_out[12] = {theta_out[12][20:0], theta_out[12][63:21]};
    // Lane 13 (x=3,y=2): rotate 25
    assign rho_out[13] = {theta_out[13][38:0], theta_out[13][63:39]};
    // Lane 14 (x=4,y=2): rotate 39
    assign rho_out[14] = {theta_out[14][24:0], theta_out[14][63:25]};
    // Lane 15 (x=0,y=3): rotate 41
    assign rho_out[15] = {theta_out[15][22:0], theta_out[15][63:23]};
    // Lane 16 (x=1,y=3): rotate 45
    assign rho_out[16] = {theta_out[16][18:0], theta_out[16][63:19]};
    // Lane 17 (x=2,y=3): rotate 15
    assign rho_out[17] = {theta_out[17][48:0], theta_out[17][63:49]};
    // Lane 18 (x=3,y=3): rotate 21
    assign rho_out[18] = {theta_out[18][42:0], theta_out[18][63:43]};
    // Lane 19 (x=4,y=3): rotate  8
    assign rho_out[19] = {theta_out[19][55:0], theta_out[19][63:56]};
    // Lane 20 (x=0,y=4): rotate 18
    assign rho_out[20] = {theta_out[20][45:0], theta_out[20][63:46]};
    // Lane 21 (x=1,y=4): rotate  2
    assign rho_out[21] = {theta_out[21][61:0], theta_out[21][63:62]};
    // Lane 22 (x=2,y=4): rotate 61
    assign rho_out[22] = {theta_out[22][ 2:0], theta_out[22][63: 3]};
    // Lane 23 (x=3,y=4): rotate 56
    assign rho_out[23] = {theta_out[23][ 7:0], theta_out[23][63: 8]};
    // Lane 24 (x=4,y=4): rotate 14
    assign rho_out[24] = {theta_out[24][49:0], theta_out[24][63:50]};

    // =========================================================================
    // PI — permute lane positions
    // A'[y, (2x+3y) mod 5] = A[x,y]
    // target_index = 5*((2*x+3*y) mod 5) + y
    //
    // Mapping: pi_out[target] = rho_out[source=5*y+x]
    //   pi_out[ 0] = rho_out[ 0]   (x=0,y=0)
    //   pi_out[10] = rho_out[ 1]   (x=1,y=0)
    //   pi_out[20] = rho_out[ 2]   (x=2,y=0)
    //   pi_out[ 5] = rho_out[ 3]   (x=3,y=0)
    //   pi_out[15] = rho_out[ 4]   (x=4,y=0)
    //   pi_out[16] = rho_out[ 5]   (x=0,y=1)
    //   pi_out[ 1] = rho_out[ 6]   (x=1,y=1)
    //   pi_out[11] = rho_out[ 7]   (x=2,y=1)
    //   pi_out[21] = rho_out[ 8]   (x=3,y=1)
    //   pi_out[ 6] = rho_out[ 9]   (x=4,y=1)
    //   pi_out[ 7] = rho_out[10]   (x=0,y=2)
    //   pi_out[17] = rho_out[11]   (x=1,y=2)
    //   pi_out[ 2] = rho_out[12]   (x=2,y=2)
    //   pi_out[12] = rho_out[13]   (x=3,y=2)
    //   pi_out[22] = rho_out[14]   (x=4,y=2)
    //   pi_out[23] = rho_out[15]   (x=0,y=3)
    //   pi_out[ 8] = rho_out[16]   (x=1,y=3)
    //   pi_out[18] = rho_out[17]   (x=2,y=3)
    //   pi_out[ 3] = rho_out[18]   (x=3,y=3)
    //   pi_out[13] = rho_out[19]   (x=4,y=3)
    //   pi_out[14] = rho_out[20]   (x=0,y=4)
    //   pi_out[24] = rho_out[21]   (x=1,y=4)
    //   pi_out[ 9] = rho_out[22]   (x=2,y=4)
    //   pi_out[19] = rho_out[23]   (x=3,y=4)
    //   pi_out[ 4] = rho_out[24]   (x=4,y=4)
    // =========================================================================

    wire [63:0] pi_out [0:24];

    assign pi_out[ 0] = rho_out[ 0];
    assign pi_out[ 1] = rho_out[ 6];
    assign pi_out[ 2] = rho_out[12];
    assign pi_out[ 3] = rho_out[18];
    assign pi_out[ 4] = rho_out[24];
    assign pi_out[ 5] = rho_out[ 3];
    assign pi_out[ 6] = rho_out[ 9];
    assign pi_out[ 7] = rho_out[10];
    assign pi_out[ 8] = rho_out[16];
    assign pi_out[ 9] = rho_out[22];
    assign pi_out[10] = rho_out[ 1];
    assign pi_out[11] = rho_out[ 7];
    assign pi_out[12] = rho_out[13];
    assign pi_out[13] = rho_out[19];
    assign pi_out[14] = rho_out[20];
    assign pi_out[15] = rho_out[ 4];
    assign pi_out[16] = rho_out[ 5];
    assign pi_out[17] = rho_out[11];
    assign pi_out[18] = rho_out[17];
    assign pi_out[19] = rho_out[23];
    assign pi_out[20] = rho_out[ 2];
    assign pi_out[21] = rho_out[ 8];
    assign pi_out[22] = rho_out[14];
    assign pi_out[23] = rho_out[15];
    assign pi_out[24] = rho_out[21];

    // =========================================================================
    // CHI — nonlinear step
    // A'[x,y] = A[x,y] ^ (~A[(x+1) mod 5, y] & A[(x+2) mod 5, y])
    // =========================================================================

    wire [63:0] chi_out [0:24];

    generate
        for (gy = 0; gy < 5; gy = gy + 1) begin : gen_chi_y
            for (gx = 0; gx < 5; gx = gx + 1) begin : gen_chi_x
                assign chi_out[5*gy+gx] = pi_out[5*gy+gx]
                    ^ (~pi_out[5*gy + (gx+1)%5] & pi_out[5*gy + (gx+2)%5]);
            end
        end
    endgenerate

    // =========================================================================
    // IOTA — XOR round constant into lane A[0,0]
    // Only 7 active bit positions in RC: bits 0, 1, 3, 7, 15, 31, 63
    // =========================================================================

    wire [63:0] iota_lane0;
    assign iota_lane0 = chi_out[0] ^ rc;

    // =========================================================================
    // Pack output state
    // =========================================================================

    assign state_out[  0*64 +: 64] = iota_lane0;

    generate
        for (gx = 1; gx < 25; gx = gx + 1) begin : gen_pack
            assign state_out[gx*64 +: 64] = chi_out[gx];
        end
    endgenerate

endmodule
