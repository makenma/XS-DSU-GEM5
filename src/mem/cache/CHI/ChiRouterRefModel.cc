#include "mem/cache/CHI/ChiRouterRefModel.hh"

#include <algorithm>

#include "base/cprintf.hh"
#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/ChiRouterRefModel.hh"

namespace gem5::Chi
{

namespace
{

constexpr int DevicePortCount = 16;

} // anonymous namespace

ChiRouterRefModel::ChiRouterRefModel(const Params &p)
    : BasicChiComponent(p),
      ruby::Consumer(this),
      localX(p.local_x),
      localY(p.local_y),
      yIncIsNorth(p.y_inc_is_north),
      multiFlitOut(p.multi_flit_out),
      pNum(p.pnum),
      dNum(p.dnum),
      internalNum(p.internal_ports_num),
      pTxDepth(p.p_tx_depth),
      pCandDepth(p.p_cand_depth)
{
    fatal_if(pNum != DefaultPNum || dNum != DefaultDNum ||
                 internalNum != InternalNum,
             "%s currently mirrors the SV refm fixed topology: pnum=4, "
             "dnum=4, internal_ports_num=8\n",
             name());
    fatal_if(pTxDepth <= 0 || pCandDepth <= 0,
             "%s requires positive p_tx_depth and p_cand_depth\n", name());

    useDualCh[chIdx(ChannelType::REQ)] = p.dual_lane_req;
    useDualCh[chIdx(ChannelType::RSP)] = p.dual_lane_rsp;
    useDualCh[chIdx(ChannelType::SNP)] = p.dual_lane_snp;
    useDualCh[chIdx(ChannelType::DAT)] = p.dual_lane_dat;

    for (uint64_t entry : p.route_table) {
        const uint32_t node_id = static_cast<uint32_t>(entry >> 16);
        const uint32_t router_field = static_cast<uint32_t>(entry & 0x7ff);
        routeTable[node_id] = router_field;
    }

    devicePorts.reserve(DevicePortCount);
    for (int i = 0; i < DevicePortCount; ++i) {
        devicePorts.emplace_back(std::make_unique<ChiCommonPort>(
            csprintf("%s.device_ports[%d]", name(), i),
            static_cast<ruby::Consumer *>(this), i));
    }

    localPorts.reserve(DevicePortCount);
    for (int i = 0; i < DevicePortCount; ++i) {
        localPorts.emplace_back(std::make_unique<ChiCommonPort>(
            csprintf("%s.local_ports[%d]", name(), i),
            static_cast<ruby::Consumer *>(this), DevicePortCount + i));
    }

    internalPorts.reserve(InternalNum);
    for (int i = 0; i < InternalNum; ++i) {
        internalPorts.emplace_back(std::make_unique<ChiCommonPort>(
            csprintf("%s.internal_ports[%d]", name(), i),
            static_cast<ruby::Consumer *>(this), DevicePortCount * 2 + i));
    }

    internalPeerPorts.reserve(InternalNum);
    for (int i = 0; i < InternalNum; ++i) {
        internalPeerPorts.emplace_back(std::make_unique<ChiCommonPort>(
            csprintf("%s.internal_peer_ports[%d]", name(), i),
            static_cast<ruby::Consumer *>(this),
            DevicePortCount * 2 + InternalNum + i));
    }

    softReset();

    DPRINTF(ChiRouterRefModel,
            "constructed local=(%u,%u) dual(req/rsp/snp/dat)=%u/%u/%u/%u "
            "multi_flit_out=%u route_entries=%llu\n",
            localX, localY, p.dual_lane_req, p.dual_lane_rsp,
            p.dual_lane_snp, p.dual_lane_dat, multiFlitOut,
            static_cast<unsigned long long>(routeTable.size()));
}

void
ChiRouterRefModel::wakeup()
{
    DPRINTF(ChiRouterRefModel, "wakeup\n");
    sampleRxPorts();
    stepOneCycle();

    if (hasWork()) {
        scheduleEvent(Cycles(1));
    }
}

void
ChiRouterRefModel::print(std::ostream &out) const
{
    out << "ChiRouterRefModel(" << name() << ")";
}

Port &
ChiRouterRefModel::getPort(const std::string &if_name, PortID idx)
{
    if (if_name == "device_ports") {
        panic_if(idx == InvalidPortID || idx >= devicePorts.size(),
                 "%s invalid device_ports index %d\n", name(), idx);
        return *devicePorts[idx];
    }

    if (if_name == "local_ports") {
        panic_if(idx == InvalidPortID || idx >= localPorts.size(),
                 "%s invalid local_ports index %d\n", name(), idx);
        return *localPorts[idx];
    }

    if (if_name == "internal_ports") {
        panic_if(idx == InvalidPortID || idx >= internalPorts.size(),
                 "%s invalid internal_ports index %d\n", name(), idx);
        return *internalPorts[idx];
    }

    if (if_name == "internal_peer_ports") {
        panic_if(idx == InvalidPortID || idx >= internalPeerPorts.size(),
                 "%s invalid internal_peer_ports index %d\n", name(), idx);
        return *internalPeerPorts[idx];
    }

    return BasicChiComponent::getPort(if_name, idx);
}

void
ChiRouterRefModel::softReset()
{
    for (int c = 0; c < RefChannels; ++c) {
        for (int r = 0; r < MaxRouters; ++r) {
            for (int ip = 0; ip < InPortNum; ++ip) {
                inbufData[c][r][ip].clear();
                internalReqQ[c][r][ip].clear();
                occ[c][r][ip] = 0;
                occIncPipe[c][r][ip] = 0;
                occDecPipe[c][r][ip] = 0;

                for (int o = 0; o < OutPortNum; ++o) {
                    inbufDataByOut[c][r][ip][o].clear();
                    internalReqByOutQ[c][r][ip][o].clear();
                }
            }

            for (int o = 0; o < OutPortNum; ++o) {
                outQ[c][r][o].clear();
                outGrantVld[c][r][o] = false;
                outGrantWinIp[c][r][o] = IN_P0;
                outCredit[c][r][o] = pTxDepth;
            }

            for (int p = 0; p < DefaultPNum; ++p) {
                pCandQ[c][r][p].clear();
            }
        }

        for (int p = 0; p < DefaultPNum; ++p) {
            pPipeQ[c][p].clear();
            pTxFifo[c][p].clear();
            mdlCredit[c][p] = getPdevCreditInit(idxToCh(c));
            mdlCreditIncPipe[c][p] = 0;
            mdlCreditIncPipeH1[c][p] = 0;
            mdlCreditIncPipeH2[c][p] = 0;
            mdlCreditDecPipe[c][p] = 0;
            rrPtrPdevOut[c][p] = 0;

            for (int d = 0; d < DefaultDNum; ++d) {
                pdevReqPipe[c][p][d] = false;
                pdevCredit[c][p][d] = pTxDepth;
                outQPdev[c][p][d].clear();
            }
        }

        for (int src = 0; src < InternalNum; ++src) {
            inputFlitvQ[c][src].clear();
        }

        for (int src = 0; src < 24; ++src) {
            inputQ[c][src].clear();
        }
    }

    initPdevAge();
    initOutAge();
    initMergeAge();
}

void
ChiRouterRefModel::initPdevAge()
{
    for (int c = 0; c < RefChannels; ++c) {
        for (int p = 0; p < DefaultPNum; ++p) {
            for (int i = 0; i < DefaultDNum; ++i) {
                for (int j = 0; j < DefaultDNum; ++j) {
                    pdevAge[c][p][i][j] = (i != j) && (i < j);
                }
            }
        }
    }
}

void
ChiRouterRefModel::initOutAge()
{
    for (int c = 0; c < RefChannels; ++c) {
        for (int r = 0; r < MaxRouters; ++r) {
            for (int o = 0; o < OutPortNum; ++o) {
                for (int i = 0; i < OutArbN; ++i) {
                    for (int j = 0; j < OutArbN; ++j) {
                        outAge[c][r][o][i][j] = (i != j) && (i < j);
                    }
                }
            }
        }
    }
}

void
ChiRouterRefModel::initMergeAge()
{
    for (int c = 0; c < RefChannels; ++c) {
        for (int p = 0; p < DefaultPNum; ++p) {
            for (int i = 0; i < MaxRouters; ++i) {
                for (int j = 0; j < MaxRouters; ++j) {
                    mergeAge[c][p][i][j] = (i != j) && (i < j);
                }
            }
        }
    }
}

void
ChiRouterRefModel::sampleRxPorts()
{
    for (int c = 0; c < RefChannels; ++c) {
        const auto ch = idxToCh(c);

        for (int src = 0; src < InternalNum; ++src) {
            auto sample_internal = [&](QueueDir dir, bool from_peer,
                                       const FlitVariant &flit) {
                auto tr = makeRoutedFlit(ch, flit, dir, false, src, -1, -1,
                                         src);
                tr.fromInternalPeerPort = from_peer;
                inputQ[c][src].push_back(tr);
                inputFlitvQ[c][src].push_back(tr);

                DPRINTF(ChiRouterRefModel,
                        "[RX_INT] ch=%s dir=%s src=%d route=0x%x out=%s "
                        "tgt=%u srcid=%u txn=%u opcode=0x%x seq=%llu\n",
                        channelName(ch), dir == QueueDir::Rx ? "rx" : "tx",
                        src, tr.routerField, outPortName(tr.out),
                        flitTargetId(tr.flit),
                        std::visit([](const auto &f) { return f.srcid; },
                                   tr.flit),
                        std::visit([](const auto &f) { return f.txnid; },
                                   tr.flit),
                        std::visit([](const auto &f) { return f.opcode; },
                                   tr.flit),
                        tr.seq);
            };

            auto drain_internal_port = [&](ChiCommonPort &port,
                                           bool from_peer,
                                           const char *kind) {
                while (port.hasRxFlit(ch)) {
                    auto flit = port.getRxFlitNoCredit(ch);
                    panic_if(!flit, "%s internal %s RX had flit then lost it\n",
                             name(), kind);
                    sample_internal(QueueDir::Rx, from_peer, *flit);
                }

                while (port.hasTxFlit(ch)) {
                    auto flit = port.getTxFlit(ch);
                    panic_if(!flit, "%s internal %s TX had flit then lost it\n",
                             name(), kind);
                    sample_internal(QueueDir::Tx, from_peer, *flit);
                }
            };

            drain_internal_port(*internalPorts[src], false, "out");
            drain_internal_port(*internalPeerPorts[src], true, "peer");
        }

        for (int p = 0; p < DefaultPNum; ++p) {
            for (int d = 0; d < DefaultDNum; ++d) {
                const int port_idx = p * DefaultDNum + d;
                const int src = InternalNum + port_idx;
                auto sample_device = [&](QueueDir dir,
                                         bool from_local,
                                         const FlitVariant &flit) {
                    auto tr = makeRoutedFlit(ch, flit, dir, true, src, p, d,
                                             -1);
                    tr.fromLocalPort = from_local;
                    inputQ[c][src].push_back(tr);

                    DPRINTF(ChiRouterRefModel,
                            "[RX_PDEV] ch=%s dir=%s local=%u p=%d d=%d route=0x%x "
                            "out=%s dst_dev=%d tgt=%u srcid=%u txn=%u "
                            "opcode=0x%x seq=%llu\n",
                            channelName(ch),
                            dir == QueueDir::Rx ? "rx" : "tx", from_local,
                            p, d,
                            tr.routerField, outPortName(tr.out), tr.destDevice,
                            flitTargetId(tr.flit),
                            std::visit([](const auto &f) { return f.srcid; },
                                       tr.flit),
                            std::visit([](const auto &f) { return f.txnid; },
                                       tr.flit),
                            std::visit([](const auto &f) { return f.opcode; },
                                       tr.flit),
                            tr.seq);
                };

                auto drain_device_port = [&](ChiCommonPort &port,
                                             bool from_local,
                                             const char *kind) {
                    while (port.hasRxFlit(ch)) {
                        auto flit = port.getRxFlitNoCredit(ch);
                        panic_if(!flit, "%s %s RX had flit then lost it\n",
                                 name(), kind);
                        sample_device(QueueDir::Rx, from_local, *flit);
                    }

                    while (port.hasTxFlit(ch)) {
                        auto flit = port.getTxFlit(ch);
                        panic_if(!flit, "%s %s TX had flit then lost it\n",
                                 name(), kind);
                        sample_device(QueueDir::Tx, from_local, *flit);
                    }
                };

                drain_device_port(*devicePorts[port_idx], false, "device");
                drain_device_port(*localPorts[port_idx], true, "local");
            }
        }
    }
}

void
ChiRouterRefModel::stepOneCycle()
{
    updateCreditFromIf();

    for (int c = 0; c < RefChannels; ++c) ptxDrainByCreditStage(idxToCh(c));
    for (int c = 0; c < RefChannels; ++c) outSendCommitStage(idxToCh(c));
    for (int c = 0; c < RefChannels; ++c) pportMergeStage(idxToCh(c));
    for (int c = 0; c < RefChannels; ++c) ptxEnqueueStage(idxToCh(c));

    for (int c = 0; c < RefChannels; ++c) {
        internalPayloadCommitStage(idxToCh(c));
    }
    for (int c = 0; c < RefChannels; ++c) doOutputArbitration(idxToCh(c));
    for (int c = 0; c < RefChannels; ++c) pdevArbStage(idxToCh(c));
    for (int c = 0; c < RefChannels; ++c) samplePdevReqStage(idxToCh(c));
    for (int c = 0; c < RefChannels; ++c) sampleInternalFlitvStage(idxToCh(c));

    occUpdateStage();
}

void
ChiRouterRefModel::updateCreditFromIf()
{
    for (int c = 0; c < RefChannels; ++c) {
        const auto ch = idxToCh(c);

        for (int r = 0; r < MaxRouters; ++r) {
            for (int o = 0; o < OutPortNum; ++o) {
                const auto out = static_cast<OutPort>(o);
                if (isPOut(out)) {
                    continue;
                }

                const int idx = internalOutPortIndex(r, out);
                if (idx < 0 || idx >= static_cast<int>(internalPorts.size())) {
                    continue;
                }

                if (internalPorts[idx]->isConnected() &&
                    (internalPorts[idx]->peerHasRxCredit(ch) ||
                     internalPorts[idx]->peerHasTxCredit(ch)) &&
                    outCredit[c][r][o] < pTxDepth) {
                    outCredit[c][r][o]++;
                    DPRINTF(ChiRouterRefModel,
                            "[CREDIT+] internal ch=%s r=%d out=%s credit=%d\n",
                            channelName(ch), r, outPortName(out),
                            outCredit[c][r][o]);
                }
            }
        }

        for (int p = 0; p < DefaultPNum; ++p) {
            for (int d = 0; d < DefaultDNum; ++d) {
                const int idx = p * DefaultDNum + d;
                const bool device_credit = devicePorts[idx]->isConnected() &&
                    (devicePorts[idx]->peerHasRxCredit(ch) ||
                     devicePorts[idx]->peerHasTxCredit(ch));
                const bool local_credit = localPorts[idx]->isConnected() &&
                    (localPorts[idx]->peerHasRxCredit(ch) ||
                     localPorts[idx]->peerHasTxCredit(ch));
                if ((device_credit || local_credit) &&
                    pdevCredit[c][p][d] < pTxDepth) {
                    pdevCredit[c][p][d]++;
                    DPRINTF(ChiRouterRefModel,
                            "[CREDIT+] pdev ch=%s p=%d d=%d credit=%d\n",
                            channelName(ch), p, d, pdevCredit[c][p][d]);
                }
            }
        }
    }
}

void
ChiRouterRefModel::ptxDrainByCreditStage(ChannelType ch)
{
    const int c = chIdx(ch);
    for (int p = 0; p < DefaultPNum; ++p) {
        const int start = rrPtrPdevOut[c][p];

        if (pTxFifo[c][p].empty()) {
            continue;
        }

        bool sent = false;
        for (int k = 0; k < DefaultDNum; ++k) {
            const int d = (start + k) % DefaultDNum;
            RoutedFlit tr;

            if (pdevCredit[c][p][d] <= 0) {
                continue;
            }
            if (popFirstMatchDev(pTxFifo[c][p], d, tr)) {
                if (!selectLocalOutputPort(ch, p, d, tr.dir)) {
                    pTxFifo[c][p].push_front(tr);
                    continue;
                }
                if (!sendToDevice(ch, p, d, tr)) {
                    pTxFifo[c][p].push_front(tr);
                    continue;
                }

                pdevCredit[c][p][d]--;
                rrPtrPdevOut[c][p] = (d + 1) % DefaultDNum;
                sent = true;

                DPRINTF(ChiRouterRefModel,
                        "[P_TX_DEQ] ch=%s p=%d -> d=%d tx=%llu/%d "
                        "credit=%d seq=%llu\n",
                        channelName(ch), p, d,
                        static_cast<unsigned long long>(
                            pTxFifo[c][p].size()),
                        pTxDepth, pdevCredit[c][p][d], tr.seq);
                break;
            }
        }

        if (!sent) {
            DPRINTF(ChiRouterRefModel,
                    "[P_TX_STALL] ch=%s p=%d tx=%llu no_credit_or_match\n",
                    channelName(ch), p,
                    static_cast<unsigned long long>(pTxFifo[c][p].size()));
        }
    }
}

void
ChiRouterRefModel::outSendCommitStage(ChannelType ch)
{
    const int c = chIdx(ch);
    const int routers = useDualCh[c] ? MaxRouters : 1;

    for (int r = 0; r < routers; ++r) {
        for (int o = 0; o < OutPortNum; ++o) {
            const auto out = static_cast<OutPort>(o);
            if (!outGrantVld[c][r][o]) {
                continue;
            }

            const auto win_ip = outGrantWinIp[c][r][o];
            if (!isPOut(out)) {
                RoutedFlit head;
                if (!peekInbufFlit(ch, r, win_ip, out, head)) {
                    warn("%s [OUT_SEND] ch=%s r=%d out=%s win_ip=%s "
                         "missing granted payload\n",
                         name(), channelName(ch), r, outPortName(out),
                         inPortName(win_ip));
                    outGrantVld[c][r][o] = false;
                    continue;
                }
                if (!canSendOut(ch, r, out, head.dir)) {
                    continue;
                }
            }

            RoutedFlit send;
            if (!popInbufFlit(ch, r, win_ip, out, send)) {
                warn("%s [OUT_SEND] ch=%s r=%d out=%s win_ip=%s missing "
                     "payload or head mismatch\n",
                     name(), channelName(ch), r, outPortName(out),
                     inPortName(win_ip));
                outGrantVld[c][r][o] = false;
                continue;
            }

            if (isInternalIp(win_ip) &&
                !popInternalReq(ch, r, win_ip, out)) {
                warn("%s [OUT_SEND] ch=%s r=%d win_ip=%s out=%s missing "
                     "internal control token\n",
                     name(), channelName(ch), r, inPortName(win_ip),
                     outPortName(out));
            }

            if (isPOut(out)) {
                const int p = static_cast<int>(out) - static_cast<int>(OUT_P0);
                pCandQ[c][r][p].push_back(send);
            } else {
                if (!sendToInternal(ch, r, out, send)) {
                    warn("%s [OUT_SEND] ch=%s r=%d out=%s enqueue failed, "
                         "dropping seq=%llu\n",
                         name(), channelName(ch), r, outPortName(out),
                         send.seq);
                }
            }

            outGrantVld[c][r][o] = false;

            DPRINTF(ChiRouterRefModel,
                    "[OUT_SEND] ch=%s r=%d out=%s win_ip=%s seq=%llu\n",
                    channelName(ch), r, outPortName(out), inPortName(win_ip),
                    send.seq);
        }
    }
}

void
ChiRouterRefModel::pportMergeStage(ChannelType ch)
{
    const int c = chIdx(ch);
    const bool dual = useDualCh[c];

    for (int p = 0; p < DefaultPNum; ++p) {
        if (!dual) {
            if (!pCandQ[c][0][p].empty()) {
                pPipeQ[c][p].push_back(pCandQ[c][0][p].front());
                pCandQ[c][0][p].pop_front();
                DPRINTF(ChiRouterRefModel,
                        "[P_MERGE] dual=0 ch=%s p=%d pick_r=0\n",
                        channelName(ch), p);
            }
            continue;
        }

        int picked = -1;
        if (pickMergeRouterAge(ch, p, picked)) {
            pPipeQ[c][p].push_back(pCandQ[c][picked][p].front());
            pCandQ[c][picked][p].pop_front();
            updateMergeAge(ch, p, picked);
            DPRINTF(ChiRouterRefModel,
                    "[P_MERGE] dual=1 ch=%s p=%d pick_r=%d\n",
                    channelName(ch), p, picked);
        }
    }
}

void
ChiRouterRefModel::ptxEnqueueStage(ChannelType ch)
{
    const int c = chIdx(ch);

    for (int p = 0; p < DefaultPNum; ++p) {
        if (pPipeQ[c][p].empty()) {
            continue;
        }

        if (!pTxHasSpace(ch, p)) {
            DPRINTF(ChiRouterRefModel,
                    "[P_TX_BP] ch=%s p=%d tx_full=%d pipe=%llu\n",
                    channelName(ch), p, pTxDepth,
                    static_cast<unsigned long long>(pPipeQ[c][p].size()));
            continue;
        }

        auto tr = pPipeQ[c][p].front();
        pPipeQ[c][p].pop_front();
        pTxFifo[c][p].push_back(tr);

        DPRINTF(ChiRouterRefModel,
                "[P_TX_ENQ] ch=%s p=%d dst_dev=%d tx=%llu/%d seq=%llu\n",
                channelName(ch), p, tr.destDevice,
                static_cast<unsigned long long>(pTxFifo[c][p].size()),
                pTxDepth, tr.seq);
    }
}

void
ChiRouterRefModel::internalPayloadCommitStage(ChannelType ch)
{
    const int c = chIdx(ch);

    for (int src = 0; src < InternalNum; ++src) {
        const auto ip = srcToInport(src);
        const int r = selectRouterForInternal(ch, src);

        if (internalReqSize(ch, r, ip) == inbufSize(ch, r, ip)) {
            continue;
        }

        if (internalReqSize(ch, r, ip) < inbufSize(ch, r, ip)) {
            warn("%s [PAYLOAD_ERROR] ch=%s r=%d ip=%s inbuf=%d ctrl=%d\n",
                 name(), channelName(ch), r, inPortName(ip),
                 inbufSize(ch, r, ip), internalReqSize(ch, r, ip));
            continue;
        }

        if (inputQ[c][src].empty()) {
            DPRINTF(ChiRouterRefModel,
                    "[INT_PAYLOAD] ch=%s src=%d ctrl_seen_but_payload_empty\n",
                    channelName(ch), src);
            continue;
        }

        auto tr = inputQ[c][src].front();
        inputQ[c][src].pop_front();
        enqueueInbufFlit(ch, r, ip, tr);
        returnIngressCredit(tr);

        DPRINTF(ChiRouterRefModel,
                "[INT_PAYLOAD] ch=%s src=%d r=%d ip=%s out=%s inbuf=%d "
                "ctrl=%d seq=%llu\n",
                channelName(ch), src, r, inPortName(ip), outPortName(tr.out),
                inbufSize(ch, r, ip), internalReqSize(ch, r, ip), tr.seq);
    }
}

void
ChiRouterRefModel::doOutputArbitration(ChannelType ch)
{
    const int c = chIdx(ch);
    const int routers = useDualCh[c] ? MaxRouters : 1;

    for (int r = 0; r < routers; ++r) {
        for (int o = 0; o < OutPortNum; ++o) {
            const auto out = static_cast<OutPort>(o);
            InPort win_ip;
            int win_idx = -1;

            if (outGrantVld[c][r][o]) {
                continue;
            }
            if (!routerCanDriveOut(ch, r, out)) {
                continue;
            }
            if (!pickOutputWinnerByAge(ch, r, out, win_ip, win_idx)) {
                continue;
            }

            if (isPOut(out)) {
                const int p = static_cast<int>(out) - static_cast<int>(OUT_P0);
                if (!pCandHasSpace(ch, r, p)) {
                    continue;
                }
            } else {
                RoutedFlit head;
                if (!peekInbufFlit(ch, r, win_ip, out, head) ||
                    !canSendOut(ch, r, out, head.dir)) {
                    continue;
                }
            }

            outGrantVld[c][r][o] = true;
            outGrantWinIp[c][r][o] = win_ip;
            occDecPipe[c][r][win_ip]++;

            if (isPInport(win_ip)) {
                const int p_in = inportToP(win_ip);
                if (p_in >= 0) {
                    mdlCreditIncPipe[c][p_in]++;
                }
            }

            if (!isPOut(out) && outCredit[c][r][o] > 0) {
                outCredit[c][r][o]--;
            }

            updateOutAge(ch, r, out, win_idx);

            DPRINTF(ChiRouterRefModel,
                    "[OUT_ARB] ch=%s r=%d out=%s win_ip=%s credit_left=%d\n",
                    channelName(ch), r, outPortName(out), inPortName(win_ip),
                    outCredit[c][r][o]);
        }
    }
}

void
ChiRouterRefModel::pdevArbStage(ChannelType ch)
{
    const int c = chIdx(ch);

    for (int p = 0; p < DefaultPNum; ++p) {
        if (!hasPdevCredit(ch, p)) {
            DPRINTF(ChiRouterRefModel,
                    "[PDEV_ARB_BP] ch=%s p=%d no_credit credit=%d\n",
                    channelName(ch), p, mdlCredit[c][p]);
            continue;
        }

        std::array<bool, DefaultDNum> req{};
        for (int d = 0; d < DefaultDNum; ++d) {
            req[d] = pdevReqPipe[c][p][d];
        }

        const int win_d = pickOldestPdev(ch, p, req);
        if (win_d < 0) {
            continue;
        }

        const int src = InternalNum + p * DefaultDNum + win_d;
        if (inputQ[c][src].empty()) {
            warn("%s [PDEV_ARB] ch=%s p=%d d=%d req_seen_but_empty\n",
                 name(), channelName(ch), p, win_d);
            continue;
        }

        const int r = selectRouterForPinj(ch, p);
        const auto ip = pToInport(p);
        auto tr = inputQ[c][src].front();
        inputQ[c][src].pop_front();
        enqueueInbufFlit(ch, r, ip, tr);
        returnIngressCredit(tr);

        occIncPipe[c][r][ip]++;
        mdlCreditDecPipe[c][p]++;
        updatePdevAge(ch, p, win_d);

        DPRINTF(ChiRouterRefModel,
                "[PDEV_ARB] ch=%s p=%d win_d=%d src=%d r=%d ip=%s "
                "inbuf=%d pend_inc=%d credit=%d seq=%llu\n",
                channelName(ch), p, win_d, src, r, inPortName(ip),
                inbufSize(ch, r, ip), occIncPipe[c][r][ip],
                mdlCredit[c][p], tr.seq);
    }
}

void
ChiRouterRefModel::samplePdevReqStage(ChannelType ch)
{
    const int c = chIdx(ch);
    for (int p = 0; p < DefaultPNum; ++p) {
        const int base = InternalNum + p * DefaultDNum;
        for (int d = 0; d < DefaultDNum; ++d) {
            pdevReqPipe[c][p][d] = !inputQ[c][base + d].empty();
        }
    }
}

void
ChiRouterRefModel::sampleInternalFlitvStage(ChannelType ch)
{
    const int c = chIdx(ch);

    for (int src = 0; src < InternalNum; ++src) {
        if (inputFlitvQ[c][src].empty()) {
            continue;
        }

        auto tr = inputFlitvQ[c][src].front();
        inputFlitvQ[c][src].pop_front();

        const auto ip = srcToInport(src);
        const int r = selectRouterForInternal(ch, src);
        enqueueInternalReq(ch, r, ip, tr.out);
        occIncPipe[c][r][ip]++;

        DPRINTF(ChiRouterRefModel,
                "[INT_FLITV] ch=%s src=%d r=%d ip=%s out=%s route=0x%x "
                "pend_inc=%d seq=%llu\n",
                channelName(ch), src, r, inPortName(ip), outPortName(tr.out),
                tr.routerField, occIncPipe[c][r][ip], tr.seq);
    }
}

void
ChiRouterRefModel::occUpdateStage()
{
    for (int c = 0; c < RefChannels; ++c) {
        for (int r = 0; r < MaxRouters; ++r) {
            for (int ip = 0; ip < InPortNum; ++ip) {
                const int inc = occIncPipe[c][r][ip];
                const int dec = occDecPipe[c][r][ip];
                if (inc == 0 && dec == 0) {
                    continue;
                }

                occ[c][r][ip] += inc;
                occ[c][r][ip] -= dec;
                if (occ[c][r][ip] < 0) {
                    warn("%s [OCC_NEG] ch=%s r=%d ip=%s inc=%d dec=%d\n",
                         name(), channelName(idxToCh(c)), r,
                         inPortName(static_cast<InPort>(ip)), inc, dec);
                    occ[c][r][ip] = 0;
                }

                occIncPipe[c][r][ip] = 0;
                occDecPipe[c][r][ip] = 0;
            }
        }

        for (int p = 0; p < DefaultPNum; ++p) {
            const int inc = mdlCreditIncPipe[c][p];
            const int inc_h1 = mdlCreditIncPipeH1[c][p];
            const int inc_h2 = mdlCreditIncPipeH2[c][p];
            const int dec = mdlCreditDecPipe[c][p];
            const int max_cr = getPdevCreditInit(idxToCh(c));

            mdlCreditIncPipeH1[c][p] = inc;
            mdlCreditIncPipe[c][p] = 0;
            mdlCreditIncPipeH2[c][p] = inc_h1;

            if (!useDualCh[c]) {
                mdlCredit[c][p] = std::min(max_cr, mdlCredit[c][p] + inc_h1);
            } else {
                mdlCredit[c][p] = std::min(max_cr, mdlCredit[c][p] + inc_h2);
            }

            if (dec != 0) {
                int next = mdlCredit[c][p] - dec;
                if (next < 0) {
                    warn("%s [MDL_CR_NEG] ch=%s p=%d credit=%d dec=%d\n",
                         name(), channelName(idxToCh(c)), p,
                         mdlCredit[c][p], dec);
                    next = 0;
                }
                mdlCredit[c][p] = std::min(next, max_cr);
                mdlCreditDecPipe[c][p] = 0;

                DPRINTF(ChiRouterRefModel,
                        "[MDL_CR_COMMIT] ch=%s p=%d -%d => credit=%d\n",
                        channelName(idxToCh(c)), p, dec, mdlCredit[c][p]);
            }
        }
    }
}

ChiRouterRefModel::RoutedFlit
ChiRouterRefModel::makeRoutedFlit(ChannelType ch, const FlitVariant &flit,
                                  QueueDir dir, bool from_device,
                                  int src_index, int p, int d, int internal)
{
    RoutedFlit tr;
    tr.flit = flit;
    tr.channel = ch;
    tr.srcIndex = src_index;
    tr.fromDevice = from_device;
    tr.p = p;
    tr.d = d;
    tr.internal = internal;
    tr.routerField = routeFieldFor(flit);
    tr.out = decodeRouterToOutport(tr.routerField);
    tr.destDevice = tr.routerField & 0x3;
    tr.dir = dir;
    tr.seq = nextSeq++;
    return tr;
}

uint32_t
ChiRouterRefModel::flitTargetId(const FlitVariant &flit) const
{
    return std::visit([](const auto &f) { return f.tgtid; }, flit);
}

uint32_t
ChiRouterRefModel::routeFieldFor(const FlitVariant &flit) const
{
    const uint32_t tgt = flitTargetId(flit);
    const auto it = routeTable.find(tgt);
    if (it != routeTable.end()) {
        return it->second & 0x7ff;
    }
    return tgt & 0x7ff;
}

ChiRouterRefModel::OutPort
ChiRouterRefModel::decodeRouterToOutport(uint32_t router_field) const
{
    const auto id = DecodeFlitId(router_field);
    const bool is_ext = false;

    if (id.XId > localX) {
        return is_ext ? OUT_E_EXT : OUT_E;
    }
    if (id.XId < localX) {
        return is_ext ? OUT_W_EXT : OUT_W;
    }
    if (id.YId > localY) {
        return yIncIsNorth ? (is_ext ? OUT_N_EXT : OUT_N) :
                             (is_ext ? OUT_S_EXT : OUT_S);
    }
    if (id.YId < localY) {
        return yIncIsNorth ? (is_ext ? OUT_S_EXT : OUT_S) :
                             (is_ext ? OUT_N_EXT : OUT_N);
    }

    switch (id.PId) {
      case 0:
        return OUT_P0;
      case 1:
        return OUT_P1;
      case 2:
        return OUT_P2;
      case 3:
        return OUT_P3;
      default:
        return OUT_P0;
    }
}

const char *
ChiRouterRefModel::channelName(ChannelType ch) const
{
    switch (ch) {
      case ChannelType::REQ:
        return "REQ";
      case ChannelType::RSP:
        return "RSP";
      case ChannelType::SNP:
        return "SNP";
      case ChannelType::DAT:
        return "DAT";
      default:
        return "UNK";
    }
}

const char *
ChiRouterRefModel::inPortName(InPort ip) const
{
    switch (ip) {
      case IN_P0:
        return "IN_P0";
      case IN_P1:
        return "IN_P1";
      case IN_P2:
        return "IN_P2";
      case IN_P3:
        return "IN_P3";
      case IN_E:
        return "IN_E";
      case IN_S:
        return "IN_S";
      case IN_W:
        return "IN_W";
      case IN_N:
        return "IN_N";
      case IN_E_EXT:
        return "IN_E_EXT";
      case IN_S_EXT:
        return "IN_S_EXT";
      case IN_W_EXT:
        return "IN_W_EXT";
      case IN_N_EXT:
        return "IN_N_EXT";
      default:
        return "IN_UNKNOWN";
    }
}

const char *
ChiRouterRefModel::outPortName(OutPort op) const
{
    switch (op) {
      case OUT_P0:
        return "OUT_P0";
      case OUT_P1:
        return "OUT_P1";
      case OUT_P2:
        return "OUT_P2";
      case OUT_P3:
        return "OUT_P3";
      case OUT_E:
        return "OUT_E";
      case OUT_S:
        return "OUT_S";
      case OUT_W:
        return "OUT_W";
      case OUT_N:
        return "OUT_N";
      case OUT_E_EXT:
        return "OUT_E_EXT";
      case OUT_S_EXT:
        return "OUT_S_EXT";
      case OUT_W_EXT:
        return "OUT_W_EXT";
      case OUT_N_EXT:
        return "OUT_N_EXT";
      default:
        return "OUT_UNKNOWN";
    }
}

ChiRouterRefModel::InPort
ChiRouterRefModel::srcToInport(int src_index) const
{
    if (src_index < InternalNum) {
        switch (src_index) {
          case 0:
          case 4:
            return IN_E;
          case 1:
          case 5:
            return IN_S;
          case 2:
          case 6:
            return IN_W;
          case 3:
          case 7:
            return IN_N;
          default:
            return IN_E;
        }
    }

    const int p = (src_index - InternalNum) / DefaultDNum;
    return pToInport(p);
}

ChiRouterRefModel::InPort
ChiRouterRefModel::pToInport(int p) const
{
    switch (p) {
      case 0:
        return IN_P0;
      case 1:
        return IN_P1;
      case 2:
        return IN_P2;
      case 3:
        return IN_P3;
      default:
        return IN_P0;
    }
}

int
ChiRouterRefModel::inportToP(InPort ip) const
{
    switch (ip) {
      case IN_P0:
        return 0;
      case IN_P1:
        return 1;
      case IN_P2:
        return 2;
      case IN_P3:
        return 3;
      default:
        return -1;
    }
}

bool
ChiRouterRefModel::isPInport(InPort ip) const
{
    return ip == IN_P0 || ip == IN_P1 || ip == IN_P2 || ip == IN_P3;
}

bool
ChiRouterRefModel::isPOut(OutPort op) const
{
    return op == OUT_P0 || op == OUT_P1 || op == OUT_P2 || op == OUT_P3;
}

bool
ChiRouterRefModel::isExtOut(OutPort op) const
{
    return op == OUT_E_EXT || op == OUT_S_EXT || op == OUT_W_EXT ||
           op == OUT_N_EXT;
}

bool
ChiRouterRefModel::isInternalIp(InPort ip) const
{
    return ip == IN_E || ip == IN_S || ip == IN_W || ip == IN_N ||
           ip == IN_E_EXT || ip == IN_S_EXT || ip == IN_W_EXT ||
           ip == IN_N_EXT;
}

int
ChiRouterRefModel::internalOutPortIndex(int router, OutPort op) const
{
    if (op >= OUT_E && op <= OUT_N) {
        return (static_cast<int>(op) - static_cast<int>(OUT_E)) + router * 4;
    }
    if (op >= OUT_E_EXT && op <= OUT_N_EXT) {
        return (static_cast<int>(op) - static_cast<int>(OUT_E_EXT)) + 4;
    }
    return -1;
}

int
ChiRouterRefModel::selectRouterForInternal(ChannelType ch, int src) const
{
    if (!useDualCh[chIdx(ch)]) {
        return 0;
    }
    return src >= 4 ? 1 : 0;
}

int
ChiRouterRefModel::selectRouterForPinj(ChannelType ch, int p) const
{
    const int c = chIdx(ch);
    if (!useDualCh[c]) {
        return 0;
    }

    const auto ip = pToInport(p);
    return (occ[c][0][ip] < occ[c][1][ip]) ? 0 : 1;
}

int
ChiRouterRefModel::getPdevCreditInit(ChannelType ch) const
{
    return useDualCh[chIdx(ch)] ? 8 : 4;
}

bool
ChiRouterRefModel::hasPdevCredit(ChannelType ch, int p) const
{
    return mdlCredit[chIdx(ch)][p] > 0;
}

int
ChiRouterRefModel::inbufSize(ChannelType ch, int router, InPort ip) const
{
    const int c = chIdx(ch);
    int total = inbufData[c][router][ip].size();
    if (!multiFlitOut) {
        return total;
    }

    for (int o = 0; o < OutPortNum; ++o) {
        total += inbufDataByOut[c][router][ip][o].size();
    }
    return total;
}

int
ChiRouterRefModel::internalReqSize(ChannelType ch, int router, InPort ip) const
{
    const int c = chIdx(ch);
    int total = internalReqQ[c][router][ip].size();
    if (!multiFlitOut) {
        return total;
    }

    for (int o = 0; o < OutPortNum; ++o) {
        total += internalReqByOutQ[c][router][ip][o].size();
    }
    return total;
}

void
ChiRouterRefModel::enqueueInbufFlit(ChannelType ch, int router, InPort ip,
                                    const RoutedFlit &flit)
{
    const int c = chIdx(ch);
    const int out = static_cast<int>(flit.out);
    if (multiFlitOut && out >= 0 && out < OutPortNum) {
        inbufDataByOut[c][router][ip][out].push_back(flit);
    } else {
        inbufData[c][router][ip].push_back(flit);
    }
}

void
ChiRouterRefModel::enqueueInternalReq(ChannelType ch, int router, InPort ip,
                                      OutPort out)
{
    const int c = chIdx(ch);
    const int out_idx = static_cast<int>(out);
    if (multiFlitOut && out_idx >= 0 && out_idx < OutPortNum) {
        internalReqByOutQ[c][router][ip][out_idx].push_back(out);
    } else {
        internalReqQ[c][router][ip].push_back(out);
    }
}

bool
ChiRouterRefModel::popInbufFlit(ChannelType ch, int router, InPort ip,
                                OutPort out, RoutedFlit &flit)
{
    const int c = chIdx(ch);
    const int out_idx = static_cast<int>(out);

    if (!multiFlitOut) {
        auto &q = inbufData[c][router][ip];
        if (q.empty() || q.front().out != out) {
            return false;
        }
        flit = q.front();
        q.pop_front();
        return true;
    }

    auto &q = inbufDataByOut[c][router][ip][out_idx];
    if (q.empty()) {
        return false;
    }
    flit = q.front();
    q.pop_front();
    return true;
}

bool
ChiRouterRefModel::peekInbufFlit(ChannelType ch, int router, InPort ip,
                                 OutPort out, RoutedFlit &flit) const
{
    const int c = chIdx(ch);
    const int out_idx = static_cast<int>(out);

    if (!multiFlitOut) {
        const auto &q = inbufData[c][router][ip];
        if (q.empty() || q.front().out != out) {
            return false;
        }
        flit = q.front();
        return true;
    }

    const auto &q = inbufDataByOut[c][router][ip][out_idx];
    if (q.empty()) {
        return false;
    }
    flit = q.front();
    return true;
}

bool
ChiRouterRefModel::popInternalReq(ChannelType ch, int router, InPort ip,
                                  OutPort out)
{
    const int c = chIdx(ch);
    const int out_idx = static_cast<int>(out);

    if (!multiFlitOut) {
        auto &q = internalReqQ[c][router][ip];
        if (q.empty() || q.front() != out) {
            return false;
        }
        q.pop_front();
        return true;
    }

    auto &q = internalReqByOutQ[c][router][ip][out_idx];
    if (q.empty()) {
        return false;
    }
    q.pop_front();
    return true;
}

bool
ChiRouterRefModel::ctrlHasReq(ChannelType ch, int router, InPort ip,
                              OutPort &out) const
{
    const int c = chIdx(ch);

    if (multiFlitOut) {
        for (int o = 0; o < OutPortNum; ++o) {
            out = static_cast<OutPort>(o);
            if (hasReqForOut(ch, router, ip, out)) {
                return true;
            }
        }
        return false;
    }

    if (isInternalIp(ip)) {
        const auto &q = internalReqQ[c][router][ip];
        if (q.empty()) {
            return false;
        }
        out = q.front();
        return true;
    }

    const auto &q = inbufData[c][router][ip];
    if (q.empty()) {
        return false;
    }
    out = q.front().out;
    return true;
}

bool
ChiRouterRefModel::hasReqForOut(ChannelType ch, int router, InPort ip,
                                OutPort out) const
{
    const int c = chIdx(ch);
    if (!multiFlitOut) {
        OutPort req_out;
        return ctrlHasReq(ch, router, ip, req_out) && req_out == out;
    }

    const int o = static_cast<int>(out);
    if (inbufDataByOut[c][router][ip][o].empty()) {
        return false;
    }
    if (isInternalIp(ip) && internalReqByOutQ[c][router][ip][o].empty()) {
        return false;
    }
    return true;
}

int
ChiRouterRefModel::pickOldestPdev(ChannelType ch, int p,
                                  const std::array<bool, DefaultDNum> &req)
    const
{
    const int c = chIdx(ch);
    for (int i = 0; i < DefaultDNum; ++i) {
        if (!req[i]) {
            continue;
        }

        bool oldest = true;
        for (int j = 0; j < DefaultDNum; ++j) {
            if (i == j) {
                continue;
            }
            if (req[j] && !pdevAge[c][p][i][j]) {
                oldest = false;
                break;
            }
        }
        if (oldest) {
            return i;
        }
    }
    return -1;
}

void
ChiRouterRefModel::updatePdevAge(ChannelType ch, int p, int win_d)
{
    const int c = chIdx(ch);
    for (int j = 0; j < DefaultDNum; ++j) {
        if (j == win_d) {
            continue;
        }
        pdevAge[c][p][win_d][j] = false;
        pdevAge[c][p][j][win_d] = true;
    }
}

ChiRouterRefModel::InPort
ChiRouterRefModel::arbReqIdxToIp(int router, int idx) const
{
    switch (idx) {
      case 0:
        return IN_E;
      case 1:
        return IN_W;
      case 2:
        return IN_N;
      case 3:
        return IN_S;
      case 4:
        return IN_P0;
      case 5:
        return IN_P1;
      case 6:
        return IN_P2;
      case 7:
        return IN_P3;
      default:
        return IN_P0;
    }
}

int
ChiRouterRefModel::pickOldestOut(ChannelType ch, int router, OutPort out,
                                 const std::array<bool, OutArbN> &req) const
{
    const int c = chIdx(ch);
    const int o = static_cast<int>(out);

    for (int i = 0; i < OutArbN; ++i) {
        if (!req[i]) {
            continue;
        }

        bool oldest = true;
        for (int j = 0; j < OutArbN; ++j) {
            if (i == j) {
                continue;
            }
            if (req[j] && !outAge[c][router][o][i][j]) {
                oldest = false;
                break;
            }
        }
        if (oldest) {
            return i;
        }
    }
    return -1;
}

bool
ChiRouterRefModel::pickOutputWinnerByAge(ChannelType ch, int router,
                                         OutPort out, InPort &win_ip,
                                         int &win_idx) const
{
    std::array<bool, OutArbN> req{};
    for (int i = 0; i < OutArbN; ++i) {
        const auto ip = arbReqIdxToIp(router, i);
        req[i] = hasReqForOut(ch, router, ip, out);
    }

    win_idx = pickOldestOut(ch, router, out, req);
    if (win_idx < 0) {
        return false;
    }

    win_ip = arbReqIdxToIp(router, win_idx);
    return true;
}

void
ChiRouterRefModel::updateOutAge(ChannelType ch, int router, OutPort out,
                                int win_idx)
{
    const int c = chIdx(ch);
    const int o = static_cast<int>(out);

    for (int j = 0; j < OutArbN; ++j) {
        if (j == win_idx) {
            continue;
        }
        outAge[c][router][o][win_idx][j] = false;
        outAge[c][router][o][j][win_idx] = true;
    }
}

bool
ChiRouterRefModel::pCandHasSpace(ChannelType ch, int router, int p) const
{
    return static_cast<int>(pCandQ[chIdx(ch)][router][p].size()) < pCandDepth;
}

bool
ChiRouterRefModel::pTxHasSpace(ChannelType ch, int p) const
{
    return static_cast<int>(pTxFifo[chIdx(ch)][p].size()) < pTxDepth;
}

int
ChiRouterRefModel::pickOldestMerge(
    ChannelType ch, int p, const std::array<bool, MaxRouters> &req) const
{
    const int c = chIdx(ch);
    for (int i = 0; i < MaxRouters; ++i) {
        if (!req[i]) {
            continue;
        }

        bool oldest = true;
        for (int j = 0; j < MaxRouters; ++j) {
            if (i == j) {
                continue;
            }
            if (req[j] && !mergeAge[c][p][i][j]) {
                oldest = false;
                break;
            }
        }
        if (oldest) {
            return i;
        }
    }
    return -1;
}

bool
ChiRouterRefModel::pickMergeRouterAge(ChannelType ch, int p,
                                      int &picked_router) const
{
    const int c = chIdx(ch);
    std::array<bool, MaxRouters> req{
        !pCandQ[c][0][p].empty(),
        !pCandQ[c][1][p].empty(),
    };

    picked_router = pickOldestMerge(ch, p, req);
    return picked_router >= 0;
}

void
ChiRouterRefModel::updateMergeAge(ChannelType ch, int p, int win_router)
{
    const int c = chIdx(ch);
    for (int j = 0; j < MaxRouters; ++j) {
        if (j == win_router) {
            continue;
        }
        mergeAge[c][p][win_router][j] = false;
        mergeAge[c][p][j][win_router] = true;
    }
}

bool
ChiRouterRefModel::popFirstMatchDev(FlitQueue &queue, int d,
                                    RoutedFlit &flit)
{
    for (auto it = queue.begin(); it != queue.end(); ++it) {
        if (it->destDevice == d) {
            flit = *it;
            queue.erase(it);
            return true;
        }
    }
    return false;
}

bool
ChiRouterRefModel::canSendOut(ChannelType ch, int router, OutPort out,
                              QueueDir dir) const
{
    if (isPOut(out)) {
        return true;
    }

    const int c = chIdx(ch);
    const int idx = internalOutPortIndex(router, out);
    if (idx < 0 || idx >= static_cast<int>(internalPorts.size())) {
        return false;
    }

    return outCredit[c][router][static_cast<int>(out)] > 0 &&
           portCanSend(*internalPorts[idx], ch, dir);
}

bool
ChiRouterRefModel::routerCanDriveOut(ChannelType ch, int router,
                                     OutPort out) const
{
    if (!useDualCh[chIdx(ch)]) {
        return router == 0;
    }
    return !isExtOut(out);
}

bool
ChiRouterRefModel::portCanSend(const ChiCommonPort &port, ChannelType ch,
                               QueueDir dir) const
{
    if (!port.isConnected()) {
        return false;
    }

    return dir == QueueDir::Rx ? port.peerHasRxCredit(ch) :
                                 port.peerHasTxCredit(ch);
}

ChiCommonPort *
ChiRouterRefModel::selectLocalOutputPort(ChannelType ch, int p, int d,
                                         QueueDir dir) const
{
    const int idx = p * DefaultDNum + d;
    if (idx < 0 || idx >= static_cast<int>(devicePorts.size())) {
        return nullptr;
    }

    if (localPorts[idx]->isConnected() &&
        portCanSend(*localPorts[idx], ch, dir)) {
        return localPorts[idx].get();
    }

    if (devicePorts[idx]->isConnected() &&
        portCanSend(*devicePorts[idx], ch, dir)) {
        return devicePorts[idx].get();
    }

    return nullptr;
}

bool
ChiRouterRefModel::sendToInternal(ChannelType ch, int router, OutPort out,
                                  const RoutedFlit &flit)
{
    const int idx = internalOutPortIndex(router, out);
    if (idx < 0 || idx >= static_cast<int>(internalPorts.size())) {
        return false;
    }

    const bool sent = flit.dir == QueueDir::Rx ?
        internalPorts[idx]->enqueueRx(ch, flit.flit) :
        internalPorts[idx]->enqueueTx(ch, flit.flit);

    if (!sent) {
        return false;
    }

    DPRINTF(ChiRouterRefModel,
            "[TX_INT] ch=%s dir=%s r=%d out=%s internal_idx=%d seq=%llu "
            "tgt=%u\n",
            channelName(ch), flit.dir == QueueDir::Rx ? "rx" : "tx", router,
            outPortName(out), idx, flit.seq, flitTargetId(flit.flit));
    return true;
}

bool
ChiRouterRefModel::sendToDevice(ChannelType ch, int p, int d,
                                const RoutedFlit &flit)
{
    ChiCommonPort *port = selectLocalOutputPort(ch, p, d, flit.dir);
    if (!port) {
        return false;
    }

    const bool sent = flit.dir == QueueDir::Rx ?
        port->enqueueRx(ch, flit.flit) :
        port->enqueueTx(ch, flit.flit);

    if (!sent) {
        return false;
    }

    DPRINTF(ChiRouterRefModel,
            "[TX_PDEV] ch=%s dir=%s p=%d d=%d seq=%llu tgt=%u\n",
            channelName(ch), flit.dir == QueueDir::Rx ? "rx" : "tx", p, d,
            flit.seq, flitTargetId(flit.flit));
    return true;
}

void
ChiRouterRefModel::returnIngressCredit(const RoutedFlit &flit)
{
    if (flit.dir == QueueDir::Tx) {
        return;
    }

    if (flit.fromDevice) {
        const int idx = flit.p * DefaultDNum + flit.d;
        auto &ports = flit.fromLocalPort ? localPorts : devicePorts;
        if (idx >= 0 && idx < static_cast<int>(ports.size())) {
            ports[idx]->returnRxCredit(flit.channel, 1);
            DPRINTF(ChiRouterRefModel,
                    "[RX_CREDIT] ch=%s return local=%u p=%d d=%d seq=%llu\n",
                    channelName(flit.channel), flit.fromLocalPort, flit.p,
                    flit.d, flit.seq);
        }
    } else {
        const int idx = flit.internal;
        auto &ports = flit.fromInternalPeerPort ? internalPeerPorts :
            internalPorts;
        if (idx >= 0 && idx < static_cast<int>(ports.size())) {
            ports[idx]->returnRxCredit(flit.channel, 1);
            DPRINTF(ChiRouterRefModel,
                    "[RX_CREDIT] ch=%s return internal_peer=%u internal=%d "
                    "seq=%llu\n",
                    channelName(flit.channel), flit.fromInternalPeerPort, idx,
                    flit.seq);
        }
    }
}

bool
ChiRouterRefModel::hasWork() const
{
    for (int c = 0; c < RefChannels; ++c) {
        const auto ch = idxToCh(c);
        for (const auto &port : internalPorts) {
            if (port->hasRxFlit(ch) || port->hasTxFlit(ch)) {
                return true;
            }
        }
        for (const auto &port : internalPeerPorts) {
            if (port->hasRxFlit(ch) || port->hasTxFlit(ch)) {
                return true;
            }
        }
        for (const auto &port : devicePorts) {
            if (port->hasRxFlit(ch) || port->hasTxFlit(ch)) {
                return true;
            }
        }
        for (const auto &port : localPorts) {
            if (port->hasRxFlit(ch) || port->hasTxFlit(ch)) {
                return true;
            }
        }

        for (int src = 0; src < 24; ++src) {
            if (!inputQ[c][src].empty()) {
                return true;
            }
        }
        for (int src = 0; src < InternalNum; ++src) {
            if (!inputFlitvQ[c][src].empty()) {
                return true;
            }
        }

        for (int r = 0; r < MaxRouters; ++r) {
            for (int ip = 0; ip < InPortNum; ++ip) {
                if (!inbufData[c][r][ip].empty() ||
                    !internalReqQ[c][r][ip].empty()) {
                    return true;
                }
                for (int o = 0; o < OutPortNum; ++o) {
                    if (!inbufDataByOut[c][r][ip][o].empty() ||
                        !internalReqByOutQ[c][r][ip][o].empty()) {
                        return true;
                    }
                }
            }

            for (int o = 0; o < OutPortNum; ++o) {
                if (outGrantVld[c][r][o]) {
                    return true;
                }
            }

            for (int p = 0; p < DefaultPNum; ++p) {
                if (!pCandQ[c][r][p].empty()) {
                    return true;
                }
            }
        }

        for (int p = 0; p < DefaultPNum; ++p) {
            if (!pPipeQ[c][p].empty() || !pTxFifo[c][p].empty()) {
                return true;
            }
        }
    }

    return false;
}

} // namespace gem5::Chi
