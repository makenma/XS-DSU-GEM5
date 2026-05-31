from m5.params import *
from m5.objects.BasicChiComponent import BasicChiComponent


class ChiRouterRefModel(BasicChiComponent):
    type = "ChiRouterRefModel"
    cxx_header = "mem/cache/CHI/ChiRouterRefModel.hh"
    cxx_class = "gem5::Chi::ChiRouterRefModel"

    device_ports = VectorResponsePort(
        "Router P-device ports, indexed as p * dnum + d"
    )
    local_ports = VectorRequestPort(
        "Router local endpoint ports for CHI ResponsePort nodes, indexed as "
        "p * dnum + d"
    )
    internal_ports = VectorRequestPort(
        "Router outgoing internal directional ports: E,S,W,N,E_EXT,S_EXT,W_EXT,N_EXT"
    )
    internal_peer_ports = VectorResponsePort(
        "Router incoming-peer internal directional ports: E,S,W,N,E_EXT,S_EXT,W_EXT,N_EXT"
    )

    local_x = Param.UInt8(1, "Router local X coordinate")
    local_y = Param.UInt8(1, "Router local Y coordinate")
    y_inc_is_north = Param.Bool(True, "Decode increasing Y as north")

    dual_lane_req = Param.Bool(False, "Enable dual-router lane for REQ")
    dual_lane_rsp = Param.Bool(False, "Enable dual-router lane for RSP")
    dual_lane_snp = Param.Bool(False, "Enable dual-router lane for SNP")
    dual_lane_dat = Param.Bool(False, "Enable dual-router lane for DAT")
    multi_flit_out = Param.Bool(False, "Keep independent per-output queues")

    pnum = Param.UInt8(4, "Number of P ports in the reference model")
    dnum = Param.UInt8(4, "Number of D lanes per P port")
    internal_ports_num = Param.UInt8(8, "Number of internal router ports")

    p_tx_depth = Param.UInt8(4, "Reference P TX FIFO depth")
    p_cand_depth = Param.UInt8(2, "Reference P candidate FIFO depth")

    route_table = VectorParam.UInt64(
        [],
        "Optional node-id to router-field map. Each entry is "
        "(node_id << 16) | router_field[10:0]. If absent, tgtid[10:0] "
        "is used as the router field.",
    )
