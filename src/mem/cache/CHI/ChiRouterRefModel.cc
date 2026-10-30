#include "mem/cache/CHI/ChiRouterRefModel.hh"

#include <algorithm>

#include "base/cprintf.hh"
#include "base/logging.hh"
#include "base/trace.hh"
#include "debug/ChiRouterRefModel.hh"
#include "sim/serialize.hh"

namespace gem5::Chi
{

namespace
{

constexpr int DevicePortCount = 16;
constexpr std::array<const char *, 4> ChannelNames = {
    "REQ", "RSP", "SNP", "DAT"
};
constexpr std::array<const char *, 4> DirectionNames = {
    "E", "S", "W", "N"
};
constexpr std::array<const char *, 4> LocalPortNames = {
    "P0", "P1", "P2", "P3"
};

} // anonymous namespace

ChiRouterRefModel::RouterStats::RouterStats(statistics::Group *parent)
    : statistics::Group(parent, "trafficStats"),
      ADD_STAT(localInjectedFlits, statistics::units::Count::get(),
               "Flits accepted from local endpoints, counted once at "
               "router ingress"),
      ADD_STAT(localDeliveredFlits, statistics::units::Count::get(),
               "Flits successfully enqueued to local endpoints, counted "
               "once at router egress"),
      ADD_STAT(internalReceivedFlits, statistics::units::Count::get(),
               "Flits accepted from directional links by CHI channel and "
               "E/S/W/N ingress direction"),
      ADD_STAT(internalSentFlits, statistics::units::Count::get(),
               "Flits successfully enqueued on directional links by CHI "
               "channel and E/S/W/N egress direction"),
      ADD_STAT(internalOutputStallCycles, statistics::units::Cycle::get(),
               "Pending directional-output flit-cycles blocked by link "
               "credit or peer backpressure"),
      ADD_STAT(localInjectionStallCycles, statistics::units::Cycle::get(),
               "Pending local-injection P-port cycles blocked by router "
               "ingress credit"),
      ADD_STAT(localDeliveryStallCycles, statistics::units::Cycle::get(),
               "Non-empty local-delivery P-port cycles unable to send a "
               "flit because of credit or endpoint backpressure")
{
    localInjectedFlits.init(RefChannels).flags(statistics::total);
    localDeliveredFlits.init(RefChannels).flags(statistics::total);
    internalReceivedFlits.init(RefChannels, DirectionNum)
        .flags(statistics::total);
    internalSentFlits.init(RefChannels, DirectionNum)
        .flags(statistics::total);
    internalOutputStallCycles.init(RefChannels, DirectionNum)
        .flags(statistics::total);
    localInjectionStallCycles.init(RefChannels, DefaultPNum)
        .flags(statistics::total);
    localDeliveryStallCycles.init(RefChannels, DefaultPNum)
        .flags(statistics::total);

    for (int c = 0; c < RefChannels; ++c) {
        localInjectedFlits.subname(c, ChannelNames[c]);
        localDeliveredFlits.subname(c, ChannelNames[c]);

        internalReceivedFlits.subname(c, ChannelNames[c]);
        internalSentFlits.subname(c, ChannelNames[c]);
        internalOutputStallCycles.subname(c, ChannelNames[c]);
        localInjectionStallCycles.subname(c, ChannelNames[c]);
        localDeliveryStallCycles.subname(c, ChannelNames[c]);
    }

    for (int d = 0; d < DirectionNum; ++d) {
        internalReceivedFlits.ysubname(d, DirectionNames[d]);
        internalSentFlits.ysubname(d, DirectionNames[d]);
        internalOutputStallCycles.ysubname(d, DirectionNames[d]);
    }
    for (int p = 0; p < DefaultPNum; ++p) {
        localInjectionStallCycles.ysubname(p, LocalPortNames[p]);
        localDeliveryStallCycles.ysubname(p, LocalPortNames[p]);
    }
}

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
      pCandDepth(p.p_cand_depth),
      tickEvent([this] { tick(); }, name() + ".tick"),
      stats(this)
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
        routing.addRoute(node_id, router_field);
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

    for (auto &channel : inputs)
        for (auto &lane : channel)
            for (auto &input : lane)
                input.configure(multiFlitOut);
    softReset();

    DPRINTF(ChiRouterRefModel,
            "constructed local=(%u,%u) dual(req/rsp/snp/dat)=%u/%u/%u/%u "
            "multi_flit_out=%u route_entries=%llu\n",
            localX, localY, p.dual_lane_req, p.dual_lane_rsp,
            p.dual_lane_snp, p.dual_lane_dat, multiFlitOut,
            static_cast<unsigned long long>(routing.staticEntries()));
}

void
ChiRouterRefModel::init()
{
    BasicChiComponent::init();
    auto add = [this](ChiCommonPort &port, int source, bool alternate) {
        if (port.isConnected())
            ingressPorts.push_back({&port, source, alternate});
    };
    for (int src = 0; src < InternalNum; ++src) {
        add(*internalPorts[src], src, false);
        add(*internalPeerPorts[src], src, true);
    }
    for (int pd = 0; pd < DevicePortCount; ++pd) {
        add(*devicePorts[pd], InternalNum + pd, false);
        add(*localPorts[pd], InternalNum + pd, true);
    }
}

void
ChiRouterRefModel::wakeup()
{
    if (!tickEvent.scheduled())
        schedule(tickEvent, clockEdge(Cycles(1)));
}

void
ChiRouterRefModel::tick()
{
    sampleRxPorts();
    stepOneCycle();
    if (hasWork())
        schedule(tickEvent, clockEdge(Cycles(1)));
    else if (drainState() == DrainState::Draining) {
        if (portsDrained())
            signalDrainDone();
        else
            schedule(tickEvent, clockEdge(Cycles(1)));
    }
}

bool
ChiRouterRefModel::portsDrained() const
{
    for (const auto *ports : {&devicePorts, &localPorts, &internalPorts,
                              &internalPeerPorts})
        for (const auto &port : *ports)
            if (!port->drained()) return false;
    return true;
}

DrainState
ChiRouterRefModel::drain()
{
    if (!hasWork() && portsDrained()) {
        if (tickEvent.scheduled()) deschedule(tickEvent);
        return DrainState::Drained;
    }
    wakeup();
    return DrainState::Draining;
}

void
ChiRouterRefModel::serialize(CheckpointOut &cp) const
{
    fatal_if(hasWork() || !portsDrained(), "%s checkpoint before drain", name());
    BasicChiComponent::serialize(cp);
    SERIALIZE_SCALAR(nextSeq);
    // With no packets/credit tails in flight, reset values describe all
    // queues and occupancy. Arbitration history still affects future cycles.
    std::vector<unsigned> priorities, outputRoundRobin;
    auto save = [&priorities](const auto &arbiter) {
        for (auto p : arbiter.order()) priorities.push_back(p);
    };
    for (const auto &channel : pdevArbiters)
        for (const auto &arbiter : channel) save(arbiter);
    for (const auto &channel : outputArbiters)
        for (const auto &lane : channel)
            for (const auto &arbiter : lane) save(arbiter);
    for (const auto &channel : mergeArbiters)
        for (const auto &arbiter : channel) save(arbiter);
    for (const auto &channel : rrPtrPdevOut)
        for (auto p : channel) outputRoundRobin.push_back(p);
    SERIALIZE_CONTAINER(priorities);
    SERIALIZE_CONTAINER(outputRoundRobin);
    std::vector<uint64_t> aliasKeys;
    std::vector<uint32_t> aliasRoutes;
    for (auto [key, route] : routing.aliases()) {
        aliasKeys.push_back(key);
        aliasRoutes.push_back(route);
    }
    SERIALIZE_CONTAINER(aliasKeys);
    SERIALIZE_CONTAINER(aliasRoutes);
}

void
ChiRouterRefModel::unserialize(CheckpointIn &cp)
{
    BasicChiComponent::unserialize(cp);
    UNSERIALIZE_SCALAR(nextSeq);
    std::vector<unsigned> priorities, outputRoundRobin;
    UNSERIALIZE_CONTAINER(priorities);
    UNSERIALIZE_CONTAINER(outputRoundRobin);
    std::size_t offset = 0;
    auto restore = [&priorities, &offset](auto &arbiter) {
        const auto count = arbiter.order().size();
        fatal_if(offset + count > priorities.size(), "Truncated CHI arbiter state");
        uint16_t seen = 0;
        arbiter.reset();
        for (std::size_t i = 0; i < count; ++i) {
            auto p = priorities[offset++];
            fatal_if(p >= count || (seen & (1U << p)), "Invalid CHI arbiter state");
            seen |= 1U << p;
            arbiter.grant(p);
        }
    };
    for (auto &channel : pdevArbiters)
        for (auto &arbiter : channel) restore(arbiter);
    for (auto &channel : outputArbiters)
        for (auto &lane : channel)
            for (auto &arbiter : lane) restore(arbiter);
    for (auto &channel : mergeArbiters)
        for (auto &arbiter : channel) restore(arbiter);
    fatal_if(offset != priorities.size() ||
             outputRoundRobin.size() != RefChannels * DefaultPNum,
             "Incompatible CHI router checkpoint configuration");
    offset = 0;
    for (auto &channel : rrPtrPdevOut)
        for (auto &p : channel) {
            const auto saved = outputRoundRobin[offset++];
            fatal_if(saved >= DefaultDNum, "Invalid CHI output round-robin state");
            p = saved;
        }
    std::vector<uint64_t> aliasKeys;
    std::vector<uint32_t> aliasRoutes;
    UNSERIALIZE_CONTAINER(aliasKeys);
    UNSERIALIZE_CONTAINER(aliasRoutes);
    fatal_if(aliasKeys.size() != aliasRoutes.size(), "Invalid CHI route alias state");
    for (std::size_t i = 0; i < aliasKeys.size(); ++i)
        routing.restoreAlias(aliasKeys[i], aliasRoutes[i]);
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
                inputs[c][r][ip].clear();
                occ[c][r][ip] = 0;
                occIncPipe[c][r][ip] = 0;
                occDecPipe[c][r][ip] = 0;
            }

            outGrantMask[c][r] = 0;
            for (int o = 0; o < OutPortNum; ++o) {
                outGrantWinIp[c][r][o] = IN_P0;
            }

            for (int p = 0; p < DefaultPNum; ++p) {
                pCandQ[c][r][p].clear();
            }
        }

        for (int p = 0; p < DefaultPNum; ++p) {
            pPipeQ[c][p].reset();
            pTxFifo[c][p].clear();
            mdlCredit[c][p] = getPdevCreditInit(idxToCh(c));
            mdlCreditIncPipe[c][p] = 0;
            mdlCreditIncPipeH1[c][p] = 0;
            mdlCreditIncPipeH2[c][p] = 0;
            mdlCreditDecPipe[c][p] = 0;
            rrPtrPdevOut[c][p] = 0;

            for (int d = 0; d < DefaultDNum; ++d) {
                pdevReqPipe[c][p][d] = false;
            }
        }

        for (int src = 0; src < InternalNum; ++src) {
            inputFlitvQ[c][src].clear();
            admittedControl[c][src].reset();
        }

        for (int src = 0; src < 24; ++src) {
            inputQ[c][src].clear();
        }
    }

    ownedFlits.fill(0);
    totalOwnedFlits = 0;
    pendingCreditChannels = 0;
    initPdevAge();
    initOutAge();
    initMergeAge();
}

void
ChiRouterRefModel::initPdevAge()
{
    for (auto &channel : pdevArbiters)
        for (auto &arbiter : channel) arbiter.reset();
}

void
ChiRouterRefModel::initOutAge()
{
    for (auto &channel : outputArbiters)
        for (auto &lane : channel)
            for (auto &arbiter : lane) arbiter.reset();
}

void
ChiRouterRefModel::initMergeAge()
{
    for (auto &channel : mergeArbiters)
        for (auto &arbiter : channel) arbiter.reset();
}

void
ChiRouterRefModel::sampleRxPorts()
{
    for (int c = 0; c < RefChannels; ++c) {
        const auto ch = idxToCh(c);
        for (const auto &ingress : ingressPorts) {
            auto &port = *ingress.port;
            const int src = ingress.source;
            const bool local = src >= InternalNum;
            const int pd = src - InternalNum;
            auto sample = [&](QueueDir dir, const FlitVariant &flit) {
                auto &tr = inputQ[c][src].emplace_back();
                makeRoutedFlit(tr, ch, flit, dir, local,
                              local ? pd / DefaultDNum : -1,
                              local ? pd % DefaultDNum : -1,
                              local ? -1 : src);
                ++ownedFlits[c];
                ++totalOwnedFlits;
                if (local) {
                    tr.fromLocalPort = ingress.alternate;
                    stats.localInjectedFlits[c]++;
                } else {
                    tr.fromInternalPeerPort = ingress.alternate;
                    inputFlitvQ[c][src].push_back(
                        {tr.out, tr.routerField, tr.seq});
                    stats.internalReceivedFlits[c][src % DirectionNum]++;
                }
                DPRINTF(ChiRouterRefModel,
                        "[RX] ch=%s dir=%s src=%d local=%u alternate=%u "
                        "route=0x%x out=%s tgt=%u seq=%llu\n",
                        channelName(ch), dir == QueueDir::Rx ? "rx" : "tx",
                        src, local, ingress.alternate, tr.routerField,
                        outPortName(tr.out), flitTargetId(tr.flit), tr.seq);
            };
            // Keep RX before TX and preserve the physical source ordering.
            while (port.hasRxFlit(ch, true)) {
                auto flit = port.getRxFlitNoCredit(ch);
                panic_if(!flit, "%s RX had flit then lost it", name());
                sample(QueueDir::Rx, *flit);
            }
            while (port.hasTxFlit(ch, true)) {
                auto flit = port.getTxFlitNoCredit(ch);
                panic_if(!flit, "%s TX had flit then lost it", name());
                sample(QueueDir::Tx, *flit);
            }
        }
    }
}

void
ChiRouterRefModel::stepOneCycle()
{
    // Snapshot before any egress: the last flit can leave in the first stage
    // while its channel still has bookkeeping to commit later this cycle.
    uint8_t active = pendingCreditChannels;
    for (int c = 0; c < RefChannels; ++c)
        if (ownedFlits[c]) active |= 1U << c;
    auto each = [this, active](auto stage) {
        for (int c = 0; c < RefChannels; ++c)
            if (active & (1U << c)) (this->*stage)(idxToCh(c));
    };
    each(&ChiRouterRefModel::ptxDrainByCreditStage);
    each(&ChiRouterRefModel::outSendCommitStage);
    each(&ChiRouterRefModel::pportMergeStage);
    each(&ChiRouterRefModel::ptxEnqueueStage);
    each(&ChiRouterRefModel::internalPayloadCommitStage);
    each(&ChiRouterRefModel::doOutputArbitration);
    each(&ChiRouterRefModel::pdevArbStage);
    each(&ChiRouterRefModel::samplePdevReqStage);
    each(&ChiRouterRefModel::sampleInternalFlitvStage);
    occUpdateStage(active);
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
            const auto *tr = pTxFifo[c][p].peek(d);
            if (!tr || !sendToDevice(ch, p, d, *tr))
                continue;

            rrPtrPdevOut[c][p] = (d + 1) % DefaultDNum;
            sent = true;
            DPRINTF(ChiRouterRefModel,
                    "[P_TX_DEQ] ch=%s p=%d -> d=%d tx=%llu/%d seq=%llu\n",
                    channelName(ch), p, d,
                    static_cast<unsigned long long>(pTxFifo[c][p].size() - 1),
                    pTxDepth, tr->seq);
            pTxFifo[c][p].pop(d);
            break;
        }

        if (!sent) {
            stats.localDeliveryStallCycles[c][p]++;
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
        auto pending = outGrantMask[c][r];
        while (pending) {
            const unsigned o = __builtin_ctz(pending);
            pending &= pending - 1;
            const auto out = static_cast<OutPort>(o);
            const auto win_ip = outGrantWinIp[c][r][o];
            auto &input = inputs[c][r][win_ip];
            const auto *send = input.peek(out);
            if (!send) {
                warn("%s [OUT_SEND] ch=%s r=%d out=%s win_ip=%s "
                     "missing granted payload\n", name(), channelName(ch),
                     r, outPortName(out), inPortName(win_ip));
                outGrantMask[c][r] &= ~(1U << o);
                continue;
            }
            if (!isPOut(out) && !canSendOut(ch, r, out, send->dir)) {
                const int direction = directionIndex(out);
                if (direction >= 0)
                    stats.internalOutputStallCycles[c][direction]++;
                continue;
            }
            if (isInternalIp(win_ip) && !popInternalReq(ch, r, win_ip, out)) {
                warn("%s [OUT_SEND] ch=%s r=%d win_ip=%s out=%s missing "
                     "internal control token\n", name(), channelName(ch),
                     r, inPortName(win_ip), outPortName(out));
            }
            if (isPOut(out)) {
                const int p = static_cast<int>(out) - static_cast<int>(OUT_P0);
                pCandQ[c][r][p].push_back(*send);
            } else if (!sendToInternal(ch, r, out, *send)) {
                panic("%s output enqueue contradicted its credit check "
                      "ch=%s lane=%d out=%s seq=%llu", name(),
                      channelName(ch), r, outPortName(out), send->seq);
            }
            if (isInternalIp(win_ip)) returnIngressCredit(*send);
            outGrantMask[c][r] &= ~(1U << o);
            DPRINTF(ChiRouterRefModel,
                    "[OUT_SEND] ch=%s r=%d out=%s win_ip=%s seq=%llu\n",
                    channelName(ch), r, outPortName(out), inPortName(win_ip),
                    send->seq);
            input.discard(out);
        }
    }
}

void
ChiRouterRefModel::pportMergeStage(ChannelType ch)
{
    const int c = chIdx(ch);
    const bool dual = useDualCh[c];

    for (int p = 0; p < DefaultPNum; ++p) {
        // One real merge pipeline slot; reserve downstream capacity.
        if (pPipeQ[c][p])
            continue;
        if (!dual) {
            if (!pCandQ[c][0][p].empty()) {
                pPipeQ[c][p] = pCandQ[c][0][p].front();
                pCandQ[c][0][p].pop_front();
                DPRINTF(ChiRouterRefModel,
                        "[P_MERGE] dual=0 ch=%s p=%d pick_r=0\n",
                        channelName(ch), p);
            }
            continue;
        }

        int picked = -1;
        if (pickMergeRouterAge(ch, p, picked)) {
            pPipeQ[c][p] = pCandQ[c][picked][p].front();
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
        if (!pPipeQ[c][p]) {
            continue;
        }

        if (!pTxHasSpace(ch, p)) {
            DPRINTF(ChiRouterRefModel,
                    "[P_TX_BP] ch=%s p=%d tx_full=%d pipe=%llu\n",
                    channelName(ch), p, pTxDepth,
                    static_cast<unsigned long long>(bool(pPipeQ[c][p])));
            continue;
        }

        const auto &tr = *pPipeQ[c][p];
        pTxFifo[c][p].push(tr);

        DPRINTF(ChiRouterRefModel,
                "[P_TX_ENQ] ch=%s p=%d dst_dev=%d tx=%llu/%d seq=%llu\n",
                channelName(ch), p, tr.destDevice,
                static_cast<unsigned long long>(pTxFifo[c][p].size()),
                pTxDepth, tr.seq);
        pPipeQ[c][p].reset();
    }
}

void
ChiRouterRefModel::internalPayloadCommitStage(ChannelType ch)
{
    const int c = chIdx(ch);

    for (int src = 0; src < InternalNum; ++src) {
        const auto ip = srcToInport(src);
        const int r = selectRouterForInternal(ch, src);

        // The primary/EXT wires may share an input unit in single-lane
        // mode. Aggregate counts cannot identify which wire owns the
        // preceding control token: retain that ownership per physical wire.
        if (!admittedControl[c][src]) {
            continue;
        }
        panic_if(inputQ[c][src].empty(), "%s admitted control lost payload", name());

        const auto &tr = inputQ[c][src].front();
        panic_if(tr.seq != admittedControl[c][src]->seq ||
                 tr.out != admittedControl[c][src]->out,
                 "%s physical source control/payload mismatch", name());
        admittedControl[c][src].reset();
        enqueueInbufFlit(ch, r, ip, tr);

        DPRINTF(ChiRouterRefModel,
                "[INT_PAYLOAD] ch=%s src=%d r=%d ip=%s out=%s inbuf=%d "
                "ctrl=%d seq=%llu\n",
                channelName(ch), src, r, inPortName(ip), outPortName(tr.out),
                inbufSize(ch, r, ip), internalReqSize(ch, r, ip), tr.seq);
        inputQ[c][src].pop_front();
    }
}

void
ChiRouterRefModel::doOutputArbitration(ChannelType ch)
{
    const int c = chIdx(ch);
    const int routers = useDualCh[c] ? MaxRouters : 1;

    for (int r = 0; r < routers; ++r) {
        // Garnet-style input request collection: inspect each input head once,
        // then arbitrate independent output request vectors.
        std::array<uint8_t, OutPortNum> requests{};
        uint16_t activeOutputs = 0;
        for (int index = 0; index < OutArbN; ++index) {
            const auto ip = arbReqIdxToIp(r, index);
            auto mask = inputs[c][r][ip].requestMask(isInternalIp(ip));
            activeOutputs |= mask;
            while (mask) {
                const unsigned out = __builtin_ctz(mask);
                requests[out] |= 1U << index;
                mask &= mask - 1;
            }
        }
        activeOutputs &= ~outGrantMask[c][r];
        while (activeOutputs) {
            const unsigned o = __builtin_ctz(activeOutputs);
            activeOutputs &= activeOutputs - 1;
            const auto out = static_cast<OutPort>(o);
            if (!routerCanDriveOut(ch, r, out)) continue;
            const int win_idx = outputArbiters[c][r][o].chooseMask(requests[o]);
            if (win_idx < 0) continue;
            const auto win_ip = arbReqIdxToIp(r, win_idx);

            if (isPOut(out)) {
                const int p = static_cast<int>(out) - static_cast<int>(OUT_P0);
                if (!pCandHasSpace(ch, r, p)) {
                    continue;
                }
            } else {
                const auto *head = inputs[c][r][win_ip].peek(out);
                if (!head) {
                    continue;
                }
                if (!canSendOut(ch, r, out, head->dir)) {
                    const int direction = directionIndex(out);
                    if (direction >= 0) {
                        stats.internalOutputStallCycles[c][direction]++;
                    }
                    continue;
                }
            }

            outGrantMask[c][r] |= 1U << o;
            outGrantWinIp[c][r][o] = win_ip;
            occDecPipe[c][r][win_ip]++;

            if (isPInport(win_ip)) {
                const int p_in = inportToP(win_ip);
                if (p_in >= 0) {
                    mdlCreditIncPipe[c][p_in]++;
                }
            }

            updateOutAge(ch, r, out, win_idx);

            DPRINTF(ChiRouterRefModel,
                    "[OUT_ARB] ch=%s r=%d out=%s win_ip=%s\n",
                    channelName(ch), r, outPortName(out), inPortName(win_ip));
        }
    }
}

void
ChiRouterRefModel::pdevArbStage(ChannelType ch)
{
    const int c = chIdx(ch);

    for (int p = 0; p < DefaultPNum; ++p) {
        if (!hasPdevCredit(ch, p)) {
            const bool pending = std::any_of(
                pdevReqPipe[c][p].begin(), pdevReqPipe[c][p].end(),
                [](bool requested) { return requested; });
            if (pending) {
                stats.localInjectionStallCycles[c][p]++;
            }
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
        const auto &tr = inputQ[c][src].front();
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
        inputQ[c][src].pop_front();
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
        panic_if(admittedControl[c][src], "%s control stage overwrite", name());
        admittedControl[c][src] = tr;

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
ChiRouterRefModel::occUpdateStage(uint8_t active_channels)
{
    for (int c = 0; c < RefChannels; ++c) {
        if (!(active_channels & (1U << c))) continue;
        pendingCreditChannels &= ~(1U << c);
        for (int r = 0; r < MaxRouters; ++r) {
            for (int ip = 0; ip < InPortNum; ++ip) {
                const int inc = occIncPipe[c][r][ip];
                const int dec = occDecPipe[c][r][ip];
                if (inc == 0 && dec == 0) {
                    continue;
                }

                occ[c][r][ip] += inc;
                occ[c][r][ip] -= dec;
                panic_if(occ[c][r][ip] < 0,
                         "%s negative occupancy ch=%s lane=%d input=%s inc=%d dec=%d",
                         name(), channelName(idxToCh(c)), r,
                         inPortName(static_cast<InPort>(ip)), inc, dec);

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
            // H2 remains part of the drain tail even in single-lane mode.
            if (inc || inc_h1) pendingCreditChannels |= 1U << c;

            const int returned = useDualCh[c] ? inc_h2 : inc_h1;
            const int next = mdlCredit[c][p] + returned - dec;
            panic_if(next < 0 || next > max_cr,
                     "%s local input credit violation ch=%s p=%d before=%d returned=%d consumed=%d max=%d",
                     name(), channelName(idxToCh(c)), p,
                     mdlCredit[c][p], returned, dec, max_cr);
            mdlCredit[c][p] = next;
            if (dec != 0) {
                mdlCreditDecPipe[c][p] = 0;

                DPRINTF(ChiRouterRefModel,
                        "[MDL_CR_COMMIT] ch=%s p=%d -%d => credit=%d\n",
                        channelName(idxToCh(c)), p, dec, mdlCredit[c][p]);
            }
        }
    }
}

void
ChiRouterRefModel::makeRoutedFlit(RoutedFlit &tr, ChannelType ch,
                                const FlitVariant &flit, QueueDir dir,
                                bool from_device, int p, int d, int internal)
{
    tr.flit = flit;
    tr.channel = ch;
    tr.fromDevice = from_device;
    tr.p = p;
    tr.d = d;
    tr.internal = internal;
    rememberReverseRoute(ch, flit, from_device, p, d);
    tr.routerField = routeFieldFor(ch, flit);
    tr.out = decodeRouterToOutport(tr.routerField);
    tr.destDevice = tr.routerField & 0x3;
    tr.dir = dir;
    tr.seq = nextSeq++;
}

uint32_t
ChiRouterRefModel::flitTargetId(const FlitVariant &flit) const
{
    return std::visit([](const auto &f) { return f.tgtid; }, flit);
}

uint32_t
ChiRouterRefModel::routeFieldFor(ChannelType ch, const FlitVariant &flit) const
{
    return routing.route(flitTargetId(flit),
        std::visit([](const auto &f) { return f.txnid; }, flit),
        ch == ChannelType::REQ);
}

void
ChiRouterRefModel::rememberReverseRoute(ChannelType ch,
                                        const FlitVariant &flit,
                                        bool from_device, int p, int d)
{
    if (ch != ChannelType::REQ || !from_device || p < 0 || d < 0) {
        return;
    }

    const uint32_t src = std::visit([](const auto &f) { return f.srcid; },
                                    flit);
    const uint32_t txn = std::visit([](const auto &f) { return f.txnid; },
                                    flit);
    const uint32_t route =
        ((static_cast<uint32_t>(localX) & 0xf) << 7) |
        ((static_cast<uint32_t>(localY) & 0x7) << 4) |
        ((static_cast<uint32_t>(p) & 0x3) << 2) |
        (static_cast<uint32_t>(d) & 0x3);

    routing.remember(src, txn, route);
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

int
ChiRouterRefModel::directionIndex(OutPort op) const
{
    switch (op) {
      case OUT_E:
      case OUT_E_EXT:
        return 0;
      case OUT_S:
      case OUT_S_EXT:
        return 1;
      case OUT_W:
      case OUT_W_EXT:
        return 2;
      case OUT_N:
      case OUT_N_EXT:
        return 3;
      default:
        return -1;
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
{ return inputs[chIdx(ch)][router][ip].size(); }

int
ChiRouterRefModel::internalReqSize(ChannelType ch, int router, InPort ip) const
{ return inputs[chIdx(ch)][router][ip].controls(); }

void
ChiRouterRefModel::enqueueInbufFlit(ChannelType ch, int router, InPort ip,
                                    const RoutedFlit &flit)
{ inputs[chIdx(ch)][router][ip].push(flit); }

void
ChiRouterRefModel::enqueueInternalReq(ChannelType ch, int router, InPort ip,
                                      OutPort out)
{ inputs[chIdx(ch)][router][ip].pushControl(out); }

bool
ChiRouterRefModel::popInternalReq(ChannelType ch, int router, InPort ip,
                                  OutPort out)
{ return inputs[chIdx(ch)][router][ip].popControl(out); }

bool
ChiRouterRefModel::hasReqForOut(ChannelType ch, int router, InPort ip,
                                OutPort out) const
{ return inputs[chIdx(ch)][router][ip].requests(out, isInternalIp(ip)); }

int
ChiRouterRefModel::pickOldestPdev(ChannelType ch, int p,
                                  const std::array<bool, DefaultDNum> &req)
    const
{ return pdevArbiters[chIdx(ch)][p].choose(req); }

void
ChiRouterRefModel::updatePdevAge(ChannelType ch, int p, int win_d)
{ pdevArbiters[chIdx(ch)][p].grant(win_d); }

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
{ return outputArbiters[chIdx(ch)][router][out].choose(req); }

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
{ outputArbiters[chIdx(ch)][router][out].grant(win_idx); }

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
{ return mergeArbiters[chIdx(ch)][p].choose(req); }

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
{ mergeArbiters[chIdx(ch)][p].grant(win_router); }

bool
ChiRouterRefModel::canSendOut(ChannelType ch, int router, OutPort out,
                              QueueDir dir) const
{
    if (isPOut(out)) {
        return true;
    }

    const int idx = internalOutPortIndex(router, out);
    if (idx < 0 || idx >= static_cast<int>(internalPorts.size())) {
        return false;
    }

    return portCanSend(*internalPorts[idx], ch, dir);
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

    // Port type selects a physical endpoint. Backpressure must never change
    // that destination to the alternate interface at the same P/D index.
    if (localPorts[idx]->isConnected()) {
        return portCanSend(*localPorts[idx], ch, dir) ?
            localPorts[idx].get() : nullptr;
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

    const int direction = directionIndex(out);
    panic_if(direction < 0,
             "%s internal send used non-directional output %s\n",
             name(), outPortName(out));
    stats.internalSentFlits[chIdx(ch)][direction]++;
    panic_if(!ownedFlits[chIdx(ch)], "%s egress without owned flit", name());
    --ownedFlits[chIdx(ch)];
    --totalOwnedFlits;

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

    stats.localDeliveredFlits[chIdx(ch)]++;
    panic_if(!ownedFlits[chIdx(ch)], "%s egress without owned flit", name());
    --ownedFlits[chIdx(ch)];
    --totalOwnedFlits;

    DPRINTF(ChiRouterRefModel,
            "[TX_PDEV] ch=%s dir=%s p=%d d=%d seq=%llu tgt=%u\n",
            channelName(ch), flit.dir == QueueDir::Rx ? "rx" : "tx", p, d,
            flit.seq, flitTargetId(flit.flit));
    return true;
}

void
ChiRouterRefModel::returnIngressCredit(const RoutedFlit &flit)
{
    if (flit.fromDevice) {
        const int idx = flit.p * DefaultDNum + flit.d;
        auto &ports = flit.fromLocalPort ? localPorts : devicePorts;
        if (idx >= 0 && idx < static_cast<int>(ports.size())) {
            if (flit.dir == QueueDir::Rx)
                ports[idx]->returnRxCredit(flit.channel);
            else
                ports[idx]->returnTxCredit(flit.channel);
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
            if (flit.dir == QueueDir::Rx)
                ports[idx]->returnRxCredit(flit.channel);
            else
                ports[idx]->returnTxCredit(flit.channel);
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
    if (totalOwnedFlits || pendingCreditChannels) return true;
    // A same-tick arrival is not yet visible to sampleRxPorts. Keep the next
    // edge even when its wakeup observed an already scheduled router tick.
    for (int c = 0; c < RefChannels; ++c) {
        const auto ch = idxToCh(c);
        for (const auto &ingress : ingressPorts)
            if (ingress.port->hasRxFlit(ch) || ingress.port->hasTxFlit(ch))
                return true;
    }
    return false;
}

} // namespace gem5::Chi
