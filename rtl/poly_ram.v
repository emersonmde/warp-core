// poly_ram — True dual-port synchronous RAM for polynomial coefficients
//
// 256 entries x 12 bits. Read-first mode on both ports.
// Both ports can read and write independently on each clock edge.
//
// Read-first: on a write cycle, the output reflects the OLD value
// at that address (before the write takes effect).
//
// Targets Xilinx BRAM inference (RAMB18E1 on Artix-7).

module poly_ram (
    input  wire        clk,

    // Port A
    input  wire        we_a,
    input  wire [7:0]  addr_a,
    input  wire [11:0] din_a,
    output reg  [11:0] dout_a,

    // Port B
    input  wire        we_b,
    input  wire [7:0]  addr_b,
    input  wire [11:0] din_b,
    output reg  [11:0] dout_b
);

    reg [11:0] mem [0:255];

    // Port A: read-first
    always @(posedge clk) begin
        dout_a <= mem[addr_a];
        if (we_a)
            mem[addr_a] <= din_a;
    end

    // Port B: read-first
    always @(posedge clk) begin
        dout_b <= mem[addr_b];
        if (we_b)
            mem[addr_b] <= din_b;
    end

endmodule
