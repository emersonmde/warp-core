// kyber_top — Top-level Kyber accelerator: 20-slot polynomial RAM bank + host I/O
//
// Provides coefficient-level read/write access to 20 polynomial slots via
// host_slot/host_addr/host_din/host_dout. Each slot is a 256×12 dual-port RAM
// (poly_ram). Port A is used for host access; Port B is reserved for future
// micro-op FSM (tied off in this skeleton).
//
// BRAM budget: 20 slots × 1 RAMB18E1 = 20 out of 50 on Artix-7 XC7A35T.

`include "kyber_pkg.vh"

module kyber_top (
    input  wire        clk,
    input  wire        rst_n,

    // Host polynomial I/O (coefficient-level)
    input  wire        host_we,
    input  wire [4:0]  host_slot,     // 0..19
    input  wire [7:0]  host_addr,     // 0..255
    input  wire [11:0] host_din,
    output wire [11:0] host_dout
);

    localparam NUM_SLOTS = 20;

    // Per-slot port A signals
    wire        slot_we_a   [0:NUM_SLOTS-1];
    wire [11:0] slot_dout_a [0:NUM_SLOTS-1];

    // Generate RAM bank
    genvar i;
    generate
        for (i = 0; i < NUM_SLOTS; i = i + 1) begin : bank
            // Only the selected slot gets write-enable
            assign slot_we_a[i] = (host_slot == i[4:0]) ? host_we : 1'b0;

            poly_ram u_ram (
                .clk    (clk),
                // Port A: host access
                .we_a   (slot_we_a[i]),
                .addr_a (host_addr),
                .din_a  (host_din),
                .dout_a (slot_dout_a[i]),
                // Port B: tied off (reserved for micro-op FSM)
                .we_b   (1'b0),
                .addr_b (8'd0),
                .din_b  (12'd0),
                .dout_b ()
            );
        end
    endgenerate

    // Output mux: select dout from addressed slot, 0 for out-of-range
    assign host_dout = (host_slot < NUM_SLOTS) ? slot_dout_a[host_slot] : 12'd0;

endmodule
