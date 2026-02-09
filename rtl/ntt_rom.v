// ntt_rom — Twiddle factor (zeta) lookup ROM for NTT/INTT
//
// 128 entries x 12 bits. Combinational case-statement ROM.
// zetas[k] = pow(17, bitrev7(k), 3329) for k = 0..127
//
// 17 is a primitive 256th root of unity mod 3329.
// Values verified against ref/kyber_math.py ZETAS table.
//
// Forward NTT reads k=1..127 (ascending).
// Inverse NTT reads k=127..1 (descending).
// zetas[0]=1 is the identity (unused in practice).

module ntt_rom (
    input  wire [6:0]  addr,     // [0, 127]
    output reg  [11:0] zeta      // [0, 3328]
);

    always @(*) begin
        case (addr)
            7'd  0: zeta = 12'd1;
            7'd  1: zeta = 12'd1729;
            7'd  2: zeta = 12'd2580;
            7'd  3: zeta = 12'd3289;
            7'd  4: zeta = 12'd2642;
            7'd  5: zeta = 12'd630;
            7'd  6: zeta = 12'd1897;
            7'd  7: zeta = 12'd848;
            7'd  8: zeta = 12'd1062;
            7'd  9: zeta = 12'd1919;
            7'd 10: zeta = 12'd193;
            7'd 11: zeta = 12'd797;
            7'd 12: zeta = 12'd2786;
            7'd 13: zeta = 12'd3260;
            7'd 14: zeta = 12'd569;
            7'd 15: zeta = 12'd1746;
            7'd 16: zeta = 12'd296;
            7'd 17: zeta = 12'd2447;
            7'd 18: zeta = 12'd1339;
            7'd 19: zeta = 12'd1476;
            7'd 20: zeta = 12'd3046;
            7'd 21: zeta = 12'd56;
            7'd 22: zeta = 12'd2240;
            7'd 23: zeta = 12'd1333;
            7'd 24: zeta = 12'd1426;
            7'd 25: zeta = 12'd2094;
            7'd 26: zeta = 12'd535;
            7'd 27: zeta = 12'd2882;
            7'd 28: zeta = 12'd2393;
            7'd 29: zeta = 12'd2879;
            7'd 30: zeta = 12'd1974;
            7'd 31: zeta = 12'd821;
            7'd 32: zeta = 12'd289;
            7'd 33: zeta = 12'd331;
            7'd 34: zeta = 12'd3253;
            7'd 35: zeta = 12'd1756;
            7'd 36: zeta = 12'd1197;
            7'd 37: zeta = 12'd2304;
            7'd 38: zeta = 12'd2277;
            7'd 39: zeta = 12'd2055;
            7'd 40: zeta = 12'd650;
            7'd 41: zeta = 12'd1977;
            7'd 42: zeta = 12'd2513;
            7'd 43: zeta = 12'd632;
            7'd 44: zeta = 12'd2865;
            7'd 45: zeta = 12'd33;
            7'd 46: zeta = 12'd1320;
            7'd 47: zeta = 12'd1915;
            7'd 48: zeta = 12'd2319;
            7'd 49: zeta = 12'd1435;
            7'd 50: zeta = 12'd807;
            7'd 51: zeta = 12'd452;
            7'd 52: zeta = 12'd1438;
            7'd 53: zeta = 12'd2868;
            7'd 54: zeta = 12'd1534;
            7'd 55: zeta = 12'd2402;
            7'd 56: zeta = 12'd2647;
            7'd 57: zeta = 12'd2617;
            7'd 58: zeta = 12'd1481;
            7'd 59: zeta = 12'd648;
            7'd 60: zeta = 12'd2474;
            7'd 61: zeta = 12'd3110;
            7'd 62: zeta = 12'd1227;
            7'd 63: zeta = 12'd910;
            7'd 64: zeta = 12'd17;
            7'd 65: zeta = 12'd2761;
            7'd 66: zeta = 12'd583;
            7'd 67: zeta = 12'd2649;
            7'd 68: zeta = 12'd1637;
            7'd 69: zeta = 12'd723;
            7'd 70: zeta = 12'd2288;
            7'd 71: zeta = 12'd1100;
            7'd 72: zeta = 12'd1409;
            7'd 73: zeta = 12'd2662;
            7'd 74: zeta = 12'd3281;
            7'd 75: zeta = 12'd233;
            7'd 76: zeta = 12'd756;
            7'd 77: zeta = 12'd2156;
            7'd 78: zeta = 12'd3015;
            7'd 79: zeta = 12'd3050;
            7'd 80: zeta = 12'd1703;
            7'd 81: zeta = 12'd1651;
            7'd 82: zeta = 12'd2789;
            7'd 83: zeta = 12'd1789;
            7'd 84: zeta = 12'd1847;
            7'd 85: zeta = 12'd952;
            7'd 86: zeta = 12'd1461;
            7'd 87: zeta = 12'd2687;
            7'd 88: zeta = 12'd939;
            7'd 89: zeta = 12'd2308;
            7'd 90: zeta = 12'd2437;
            7'd 91: zeta = 12'd2388;
            7'd 92: zeta = 12'd733;
            7'd 93: zeta = 12'd2337;
            7'd 94: zeta = 12'd268;
            7'd 95: zeta = 12'd641;
            7'd 96: zeta = 12'd1584;
            7'd 97: zeta = 12'd2298;
            7'd 98: zeta = 12'd2037;
            7'd 99: zeta = 12'd3220;
            7'd100: zeta = 12'd375;
            7'd101: zeta = 12'd2549;
            7'd102: zeta = 12'd2090;
            7'd103: zeta = 12'd1645;
            7'd104: zeta = 12'd1063;
            7'd105: zeta = 12'd319;
            7'd106: zeta = 12'd2773;
            7'd107: zeta = 12'd757;
            7'd108: zeta = 12'd2099;
            7'd109: zeta = 12'd561;
            7'd110: zeta = 12'd2466;
            7'd111: zeta = 12'd2594;
            7'd112: zeta = 12'd2804;
            7'd113: zeta = 12'd1092;
            7'd114: zeta = 12'd403;
            7'd115: zeta = 12'd1026;
            7'd116: zeta = 12'd1143;
            7'd117: zeta = 12'd2150;
            7'd118: zeta = 12'd2775;
            7'd119: zeta = 12'd886;
            7'd120: zeta = 12'd1722;
            7'd121: zeta = 12'd1212;
            7'd122: zeta = 12'd1874;
            7'd123: zeta = 12'd1029;
            7'd124: zeta = 12'd2110;
            7'd125: zeta = 12'd2935;
            7'd126: zeta = 12'd885;
            7'd127: zeta = 12'd2154;
            default: zeta = 12'd0;
        endcase
    end

endmodule
