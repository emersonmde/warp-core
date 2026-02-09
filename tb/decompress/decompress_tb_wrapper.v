// decompress_tb_wrapper — Instantiates decompress for all 5 Kyber D values
//
// Allows a single testbench to test all D values in one simulation.
// The input y is 11 bits wide (max D=11); smaller D values ignore upper bits.

module decompress_tb_wrapper (
    input  wire [10:0] y,
    output wire [11:0] result_d1,
    output wire [11:0] result_d4,
    output wire [11:0] result_d5,
    output wire [11:0] result_d10,
    output wire [11:0] result_d11
);

    decompress #(.D(1))  u_d1  (.y(y[0:0]),   .result(result_d1));
    decompress #(.D(4))  u_d4  (.y(y[3:0]),   .result(result_d4));
    decompress #(.D(5))  u_d5  (.y(y[4:0]),   .result(result_d5));
    decompress #(.D(10)) u_d10 (.y(y[9:0]),   .result(result_d10));
    decompress #(.D(11)) u_d11 (.y(y[10:0]),  .result(result_d11));

endmodule
