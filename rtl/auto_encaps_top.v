// auto_encaps_top — Autonomous ML-KEM-768 Encaps with Keccak integration
//
// Wires auto_encaps_ctrl (sequencer), keccak_sponge (hash), and kyber_top
// (poly bank + micro-ops) together. When idle, the host can read/write
// polynomial bank slots. When busy, the controller owns all interfaces.
//
// Usage:
//   1. Assert start for 1 cycle.
//   2. Feed 32 bytes of m then 1184 bytes of ek via din_valid/din_data.
//   3. Wait for done pulse.
//   4. Read results: compressed u[0..2] in slots 16-18 (D=10),
//      compressed v in slot 19 (D=4).
//   5. Read K via k_byte_out (32 bytes of shared secret).

module auto_encaps_top (
    input  wire        clk,
    input  wire        rst_n,

    // Control
    input  wire        start,
    output wire        done,
    output wire        busy,

    // Data input: m (32 bytes) then ek (1184 bytes), total 1216 bytes
    input  wire        din_valid,
    input  wire [7:0]  din_data,
    output wire        din_ready,

    // Host polynomial I/O (active when not busy)
    input  wire        host_we,
    input  wire [4:0]  host_slot,
    input  wire [7:0]  host_addr,
    input  wire [11:0] host_din,
    output wire [11:0] host_dout,

    // K readback (shared secret, 32 bytes)
    input  wire [4:0]  k_byte_idx,   // 0..31
    output wire [7:0]  k_byte_out
);

    // ─── auto_encaps_ctrl wires ────────────────────────────────
    wire [1:0]  ctrl_keccak_mode;
    wire        ctrl_keccak_start;
    wire        ctrl_absorb_valid;
    wire [7:0]  ctrl_absorb_data;
    wire        ctrl_absorb_last;
    wire        ctrl_squeeze_ready;
    wire        ctrl_kt_host_we;
    wire [4:0]  ctrl_kt_host_slot;
    wire [7:0]  ctrl_kt_host_addr;
    wire [11:0] ctrl_kt_host_din;
    wire [3:0]  ctrl_cmd_op;
    wire [4:0]  ctrl_cmd_slot_a;
    wire [4:0]  ctrl_cmd_slot_b;
    wire [3:0]  ctrl_cmd_param;
    wire        ctrl_cmd_start;
    wire        ctrl_cbd_bridge_en;
    wire [255:0] ctrl_k_reg;

    // ─── keccak_sponge wires ───────────────────────────────────
    wire        keccak_absorb_ready;
    wire [7:0]  keccak_squeeze_data;
    wire        keccak_squeeze_valid;
    wire        keccak_squeeze_ready;  // muxed

    // ─── kyber_top wires ───────────────────────────────────────
    wire        kt_done;
    wire        kt_busy;
    wire        kt_cbd_byte_ready;

    // ─── CBD byte bridge mux ───────────────────────────────────
    // When bridge enabled: keccak squeeze -> cbd byte interface
    // When bridge disabled: cbd_byte_valid=0
    wire        cbd_byte_valid = ctrl_cbd_bridge_en ? keccak_squeeze_valid : 1'b0;
    wire [7:0]  cbd_byte_data  = ctrl_cbd_bridge_en ? keccak_squeeze_data  : 8'd0;

    // Squeeze ready mux: bridge routes cbd backpressure to keccak
    assign keccak_squeeze_ready = ctrl_cbd_bridge_en
                                ? kt_cbd_byte_ready
                                : ctrl_squeeze_ready;

    // ─── Host port mux ────────────────────────────────────────
    // When ctrl busy: ctrl drives host port (EK parse, EXPAND_A, LOAD_M writes)
    // When ctrl idle: external host drives
    wire        mux_host_we   = busy ? ctrl_kt_host_we   : host_we;
    wire [4:0]  mux_host_slot = busy ? ctrl_kt_host_slot  : host_slot;
    wire [7:0]  mux_host_addr = busy ? ctrl_kt_host_addr  : host_addr;
    wire [11:0] mux_host_din  = busy ? ctrl_kt_host_din   : host_din;

    // ─── K byte readback ───────────────────────────────────────
    assign k_byte_out = ctrl_k_reg[k_byte_idx * 8 +: 8];

    // ─── auto_encaps_ctrl ──────────────────────────────────────
    auto_encaps_ctrl u_ctrl (
        .clk           (clk),
        .rst_n         (rst_n),
        .start         (start),
        .done          (done),
        .busy          (busy),

        .din_valid     (din_valid),
        .din_data      (din_data),
        .din_ready     (din_ready),

        .keccak_mode   (ctrl_keccak_mode),
        .keccak_start  (ctrl_keccak_start),
        .absorb_valid  (ctrl_absorb_valid),
        .absorb_data   (ctrl_absorb_data),
        .absorb_last   (ctrl_absorb_last),
        .absorb_ready  (keccak_absorb_ready),
        .squeeze_data  (keccak_squeeze_data),
        .squeeze_valid (keccak_squeeze_valid),
        .squeeze_ready (ctrl_squeeze_ready),

        .kt_host_we    (ctrl_kt_host_we),
        .kt_host_slot  (ctrl_kt_host_slot),
        .kt_host_addr  (ctrl_kt_host_addr),
        .kt_host_din   (ctrl_kt_host_din),

        .cmd_op        (ctrl_cmd_op),
        .cmd_slot_a    (ctrl_cmd_slot_a),
        .cmd_slot_b    (ctrl_cmd_slot_b),
        .cmd_param     (ctrl_cmd_param),
        .cmd_start     (ctrl_cmd_start),
        .cmd_done      (kt_done),

        .cbd_bridge_en (ctrl_cbd_bridge_en),
        .k_reg_out     (ctrl_k_reg)
    );

    // ─── keccak_sponge ────────────────────────────────────────
    keccak_sponge u_keccak (
        .clk           (clk),
        .rst_n         (rst_n),
        .mode          (ctrl_keccak_mode),
        .start         (ctrl_keccak_start),
        .absorb_valid  (ctrl_absorb_valid),
        .absorb_data   (ctrl_absorb_data),
        .absorb_last   (ctrl_absorb_last),
        .absorb_ready  (keccak_absorb_ready),
        .squeeze_data  (keccak_squeeze_data),
        .squeeze_valid (keccak_squeeze_valid),
        .squeeze_last  (),  // unused
        .squeeze_ready (keccak_squeeze_ready),
        .busy          ()   // unused
    );

    // ─── kyber_top ─────────────────────────────────────────────
    kyber_top u_kt (
        .clk            (clk),
        .rst_n          (rst_n),

        .host_we        (mux_host_we),
        .host_slot      (mux_host_slot),
        .host_addr      (mux_host_addr),
        .host_din       (mux_host_din),
        .host_dout      (host_dout),

        .cmd_op         (ctrl_cmd_op),
        .cmd_slot_a     (ctrl_cmd_slot_a),
        .cmd_slot_b     (ctrl_cmd_slot_b),
        .cmd_param      (ctrl_cmd_param),
        .start          (ctrl_cmd_start),
        .done           (kt_done),
        .busy           (kt_busy),

        .cbd_byte_valid (cbd_byte_valid),
        .cbd_byte_data  (cbd_byte_data),
        .cbd_byte_ready (kt_cbd_byte_ready)
    );

endmodule
