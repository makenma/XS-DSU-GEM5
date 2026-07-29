#include <gtest/gtest.h>

#include <sstream>

#include "mem/cache/CHI/HnfPOCQStateGraph.hh"

namespace gem5::Chi
{

namespace
{

PocqEvent
event(PocqEventKind kind)
{
    PocqEvent ev{};
    ev.kind = kind;
    return ev;
}

PocqEvent
lookupDone(bool slcHit, bool sfHit, bool replay)
{
    PocqEvent ev{};
    ev.kind = PocqEventKind::SlcLookupDone;
    ev.slcHit = slcHit;
    ev.sfHit = sfHit;
    ev.replay = replay;
    return ev;
}

void
expectStep(const PocqStepResult& step, PocqState next,
           PocqActionKind action)
{
    ASSERT_TRUE(step.stepped);
    EXPECT_EQ(step.nextState, next);
    ASSERT_EQ(step.actions.size(), 1);
    EXPECT_EQ(step.actions.front(), action);
}

void
expectActions(const PocqStepResult& step, PocqState next,
              std::initializer_list<PocqActionKind> actions)
{
    ASSERT_TRUE(step.stepped);
    EXPECT_EQ(step.nextState, next);
    ASSERT_EQ(step.actions.size(), actions.size());
    size_t index = 0;
    for (PocqActionKind action : actions) {
        EXPECT_EQ(step.actions[index++], action);
    }
}

PocqEvent
admit(PocqTxnKind txn)
{
    PocqEvent ev{};
    ev.kind = PocqEventKind::Admit;
    ev.txn = txn;
    ev.needsCompAck = txn == PocqTxnKind::ReadShared ||
        txn == PocqTxnKind::ReadUnique ||
        txn == PocqTxnKind::ReadOnce;
    return ev;
}

PocqEvent
lookupDone(PocqTxnKind txn, bool slcHit, bool sfHit = false,
           bool needsSnoop = false)
{
    PocqEvent ev = lookupDone(slcHit, sfHit, false);
    ev.txn = txn;
    ev.needsCompAck = txn == PocqTxnKind::ReadShared ||
        txn == PocqTxnKind::ReadUnique ||
        txn == PocqTxnKind::ReadOnce;
    ev.needsSnoop = needsSnoop;
    ev.dataAvailable = slcHit;
    return ev;
}

bool
isAdmit(const PocqEvent& event)
{
    return event.kind == PocqEventKind::Admit;
}

bool
isCompAck(const PocqEvent& event)
{
    return event.kind == PocqEventKind::CompAck;
}

bool
isSlcUpdateDone(const PocqEvent& event)
{
    return event.kind == PocqEventKind::SlcUpdateDone;
}

} // anonymous namespace

TEST(HnfPocqStateGraphTest, SlcHitPath)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::Idle;

    expectStep(graph.tryStep(state, event(PocqEventKind::Admit)),
               PocqState::SlcLookup, PocqActionKind::DoSlcLookup);
    EXPECT_EQ(state, PocqState::SlcLookup);

    expectStep(graph.tryStep(state, lookupDone(true, true, false)),
               PocqState::SlcUpdateIssue, PocqActionKind::UpdateSlcSf);
    expectActions(
        graph.tryStep(state, event(PocqEventKind::SlcUpdateAccepted)),
        PocqState::SlcUpdateWait, {});
    expectStep(graph.tryStep(state, event(PocqEventKind::SlcUpdateDone)),
               PocqState::TxLink, PocqActionKind::QueueCompData);
    expectStep(graph.tryStep(state, event(PocqEventKind::TxLinkDone)),
               PocqState::WaitCompAck, PocqActionKind::WaitCompAck);
    expectStep(graph.tryStep(state, event(PocqEventKind::CompAck)),
               PocqState::Idle, PocqActionKind::Retire);
    EXPECT_EQ(state, PocqState::Idle);
}

TEST(HnfPocqStateGraphTest, SlcMissIssuesMemoryRead)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::SlcLookup;

    expectStep(graph.tryStep(state, lookupDone(false, false, false)),
               PocqState::IssueMcRead, PocqActionKind::QueueTxReq);
    expectStep(graph.tryStep(state, event(PocqEventKind::McDataDone)),
               PocqState::SlcUpdateIssue, PocqActionKind::UpdateSlcSf);
    expectActions(
        graph.tryStep(state, event(PocqEventKind::SlcUpdateAccepted)),
        PocqState::SlcUpdateWait, {});
    expectStep(graph.tryStep(state, event(PocqEventKind::SlcUpdateDone)),
               PocqState::TxLink, PocqActionKind::QueueCompData);
}

TEST(HnfPocqStateGraphTest, ReadNoSnpSkipsSlcUpdate)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::Idle;

    expectStep(graph.tryStep(state, admit(PocqTxnKind::ReadNoSnp)),
               PocqState::IssueMcRead, PocqActionKind::QueueTxReq);

    PocqEvent dataDone{};
    dataDone.kind = PocqEventKind::McDataDone;
    dataDone.txn = PocqTxnKind::ReadNoSnp;
    expectStep(graph.tryStep(state, dataDone), PocqState::TxLink,
               PocqActionKind::QueueCompData);
}

TEST(HnfPocqStateGraphTest, ReplaySleeps)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::SlcLookup;

    expectStep(graph.tryStep(state, lookupDone(false, false, true)),
               PocqState::Sleep, PocqActionKind::SleepForReplay);
    EXPECT_EQ(state, PocqState::Sleep);
}

TEST(HnfPocqStateGraphTest, MaintenanceWaitsForSlcUpdateBeforeComp)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::Idle;

    expectStep(graph.tryStep(state, admit(PocqTxnKind::CleanInvalid)),
               PocqState::SlcLookup, PocqActionKind::DoSlcLookup);

    expectStep(graph.tryStep(
                   state, lookupDone(PocqTxnKind::CleanInvalid, true)),
               PocqState::SlcUpdateIssue,
               PocqActionKind::CommitMaintenance);
    expectActions(
        graph.tryStep(state, event(PocqEventKind::SlcUpdateAccepted)),
        PocqState::SlcUpdateWait, {});
    PocqEvent done = event(PocqEventKind::SlcUpdateDone);
    done.txn = PocqTxnKind::CleanInvalid;
    expectStep(graph.tryStep(state, done), PocqState::Idle,
               PocqActionKind::QueueComp);
}

TEST(HnfPocqStateGraphTest, MaintenanceSnoopAlsoWaitsForSlcUpdate)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::SlcLookup;

    expectStep(graph.tryStep(
                   state,
                   lookupDone(PocqTxnKind::MakeInvalid, true, true, true)),
               PocqState::WaitSnoop, PocqActionKind::QueueSnoops);

    PocqEvent snoopDone{};
    snoopDone.kind = PocqEventKind::SnoopDone;
    snoopDone.txn = PocqTxnKind::MakeInvalid;
    expectStep(graph.tryStep(state, snoopDone),
               PocqState::SlcUpdateIssue,
               PocqActionKind::CommitMaintenance);
}

TEST(HnfPocqStateGraphTest, EvictQueuesCompWithoutLookup)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::Idle;

    expectActions(graph.tryStep(state, admit(PocqTxnKind::Evict)),
                  PocqState::Idle,
                  {PocqActionKind::RemoveSharer,
                   PocqActionKind::QueueComp});
}

TEST(HnfPocqStateGraphTest, ReadUniqueWaitsForSnoopBeforeData)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::SlcLookup;

    expectStep(graph.tryStep(
                   state,
                   lookupDone(PocqTxnKind::ReadUnique, true, true, true)),
               PocqState::WaitSnoop, PocqActionKind::QueueSnoops);

    PocqEvent done{};
    done.kind = PocqEventKind::SnoopDone;
    done.txn = PocqTxnKind::ReadUnique;
    done.dataAvailable = true;
    done.needsCompAck = true;
    expectStep(graph.tryStep(state, done), PocqState::SlcUpdateIssue,
               PocqActionKind::UpdateSlcSf);
    expectActions(
        graph.tryStep(state, event(PocqEventKind::SlcUpdateAccepted)),
        PocqState::SlcUpdateWait, {});
    expectStep(graph.tryStep(state, event(PocqEventKind::SlcUpdateDone)),
               PocqState::TxLink, PocqActionKind::QueueCompData);
}

TEST(HnfPocqStateGraphTest, UpdateReplaySleepsFromWaitState)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::SlcUpdateWait;
    PocqEvent replay{};
    replay.kind = PocqEventKind::SlcUpdateDone;
    replay.replay = true;

    expectStep(graph.tryStep(state, replay), PocqState::Sleep,
               PocqActionKind::SleepForReplay);
}

TEST(HnfPocqStateGraphTest, UpdateCannotCompleteBeforeAcceptance)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::SlcUpdateIssue;

    const PocqStepResult done =
        graph.tryStep(state, event(PocqEventKind::SlcUpdateDone));
    EXPECT_FALSE(done.stepped);
    EXPECT_EQ(state, PocqState::SlcUpdateIssue);

    PocqEvent replay{};
    replay.kind = PocqEventKind::SlcUpdateDone;
    replay.replay = true;
    const PocqStepResult replay_result = graph.tryStep(state, replay);
    EXPECT_FALSE(replay_result.stepped);
    EXPECT_EQ(state, PocqState::SlcUpdateIssue);
}

TEST(HnfPocqStateGraphTest, SnoopWithoutDataFallsBackToMemory)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::WaitSnoop;

    PocqEvent done{};
    done.kind = PocqEventKind::SnoopDone;
    done.txn = PocqTxnKind::ReadShared;
    done.dataAvailable = false;
    expectStep(graph.tryStep(state, done), PocqState::IssueMcRead,
               PocqActionKind::QueueTxReq);
}

TEST(HnfPocqStateGraphTest, WriteWaitsForDataAndSlcUpdateThenRetires)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::Idle;

    expectActions(graph.tryStep(state, admit(PocqTxnKind::WriteUnique)),
                  PocqState::WaitWriteData,
                  {PocqActionKind::QueueCompDBIDResp,
                   PocqActionKind::WaitWriteData});

    expectActions(graph.tryStep(state, event(PocqEventKind::WriteDataDone)),
                  PocqState::SlcLookup, {PocqActionKind::DoSlcLookup});

    PocqEvent lookupDone = event(PocqEventKind::SlcLookupDone);
    lookupDone.txn = PocqTxnKind::WriteUnique;
    expectStep(graph.tryStep(state, lookupDone), PocqState::SlcUpdateIssue,
               PocqActionKind::StoreWriteData);
    expectActions(
        graph.tryStep(state, event(PocqEventKind::SlcUpdateAccepted)),
        PocqState::SlcUpdateWait, {});
    PocqEvent updateDone = event(PocqEventKind::SlcUpdateDone);
    updateDone.txn = PocqTxnKind::WriteUnique;
    expectStep(graph.tryStep(state, updateDone), PocqState::Idle,
               PocqActionKind::Retire);
}

TEST(HnfPocqStateGraphTest, EverySupportedTransactionHasAnAdmitPath)
{
    const std::vector<PocqTxnKind> reads = {
        PocqTxnKind::ReadShared,
        PocqTxnKind::ReadUnique,
        PocqTxnKind::ReadOnce,
    };
    const std::vector<PocqTxnKind> maintenance = {
        PocqTxnKind::CleanInvalid,
        PocqTxnKind::MakeInvalid,
        PocqTxnKind::MakeUnique,
    };
    const std::vector<PocqTxnKind> writes = {
        PocqTxnKind::WriteBackFull,
        PocqTxnKind::WriteCleanFull,
        PocqTxnKind::WriteUnique,
        PocqTxnKind::WriteEvictFull,
    };

    for (PocqTxnKind txn : reads) {
        POCQ_StateGraph graph;
        PocqState state = PocqState::Idle;
        const auto step = graph.tryStep(state, admit(txn));
        ASSERT_TRUE(step.stepped) << static_cast<unsigned>(txn);
        EXPECT_EQ(state, PocqState::SlcLookup);
        EXPECT_EQ(step.actions.front(), PocqActionKind::DoSlcLookup);
    }

    {
        POCQ_StateGraph graph;
        PocqState state = PocqState::Idle;
        const auto step = graph.tryStep(
            state, admit(PocqTxnKind::ReadNoSnp));
        ASSERT_TRUE(step.stepped);
        EXPECT_EQ(state, PocqState::IssueMcRead);
        EXPECT_EQ(step.actions.front(), PocqActionKind::QueueTxReq);
    }

    for (PocqTxnKind txn : maintenance) {
        POCQ_StateGraph graph;
        PocqState state = PocqState::Idle;
        const auto step = graph.tryStep(state, admit(txn));
        ASSERT_TRUE(step.stepped) << static_cast<unsigned>(txn);
        EXPECT_EQ(state, PocqState::SlcLookup);
        EXPECT_EQ(step.actions.front(), PocqActionKind::DoSlcLookup);
    }

    {
        POCQ_StateGraph graph;
        PocqState state = PocqState::Idle;
        const auto step = graph.tryStep(state, admit(PocqTxnKind::Evict));
        ASSERT_TRUE(step.stepped);
        EXPECT_EQ(state, PocqState::Idle);
        EXPECT_EQ(step.actions.front(), PocqActionKind::RemoveSharer);
    }

    for (PocqTxnKind txn : writes) {
        POCQ_StateGraph graph;
        PocqState state = PocqState::Idle;
        const auto step = graph.tryStep(state, admit(txn));
        ASSERT_TRUE(step.stepped) << static_cast<unsigned>(txn);
        EXPECT_EQ(state, PocqState::WaitWriteData);
        EXPECT_EQ(step.actions.front(), PocqActionKind::QueueCompDBIDResp);
    }
}

TEST(HnfPocqStateGraphTest, NoMatchingEdgeLeavesStateUnchanged)
{
    POCQ_StateGraph graph;
    PocqState state = PocqState::Idle;

    PocqStepResult step = graph.tryStep(
        state, event(PocqEventKind::CompAck));
    EXPECT_FALSE(step.stepped);
    EXPECT_EQ(step.nextState, PocqState::Idle);
    EXPECT_TRUE(step.actions.empty());
    EXPECT_EQ(state, PocqState::Idle);
}

TEST(HnfPocqStateGraphTest, MarkPublicNodes)
{
    POCQ_StateGraph graph;
    // Use nodes that are NOT in the graph — markPublicNode just stores pointers.
    PocqNode n1(PocqState::Idle);
    PocqNode n2(PocqState::SlcLookup);

    graph.markPublicNode(n1, SubGraphPublicState::Start);
    graph.markPublicNode(n2, SubGraphPublicState::Exit);

    EXPECT_EQ(graph.getStartNode(), &n1);
    EXPECT_EQ(graph.getExitNode(), &n2);
}

TEST(HnfPocqStateGraphTest, CompositeNodeEnterAndStepSubGraph)
{
    // Build a sub-graph using a POCQ_StateGraph.
    // We use its built-in nodes (accessed via nodeFor) to avoid state conflicts.
    POCQ_StateGraph subGraph;
    PocqNode& subIdle = subGraph.nodeFor(PocqState::Idle);
    PocqNode& subExit = subGraph.nodeFor(PocqState::SlcLookup);
    subGraph.markPublicNode(subIdle, SubGraphPublicState::Start);
    subGraph.markPublicNode(subExit, SubGraphPublicState::Exit);
    // Add a clean transition: Idle --Admit--> SlcLookup (Exit)
    subGraph.addTransition(subIdle, subExit, isAdmit,
                           {PocqActionKind::DoSlcLookup});

    // Build top-level graph: Idle --Admit--> SlcLookup (composite)
    //                         SlcLookup --CompAck--> Idle
    POCQ_StateGraph topGraph;
    PocqNode& topIdle = topGraph.nodeFor(PocqState::Idle);
    PocqNode& topActive = topGraph.nodeFor(PocqState::SlcLookup);
    topActive.setSubGraph(&subGraph);
    // Add parent-level transitions (in addition to built-in ones).
    // The built-in constructor already has: Idle --Admit--> SlcLookup
    // and SlcLookup branches to Sleep/SlcUpdateIssue/IssueMcRead.
    // We add: SlcLookup --CompAck--> Idle for sub-graph exit path.
    topGraph.addTransition(topActive, topIdle, isCompAck,
                           {PocqActionKind::Retire});

    // Step 1: Idle --Admit--> SlcLookup (composite)
    PocqState state = PocqState::Idle;
    PocqStepResult step =
        topGraph.tryStep(state, event(PocqEventKind::Admit));
    EXPECT_TRUE(step.stepped);
    EXPECT_EQ(state, PocqState::SlcLookup);
    EXPECT_TRUE(subGraph.isActive());

    // Step 2: Sub-graph steps: subIdle --Admit--> subExit (reaches Exit)
    step = topGraph.tryStep(state, event(PocqEventKind::Admit));
    EXPECT_TRUE(step.stepped);
    EXPECT_EQ(step.actions.front(), PocqActionKind::DoSlcLookup);
    // Sub-graph reached Exit; parent also has SlcLookup --Admit--> IssueMcRead
    // (built-in) which may fire. But Admit from within sub-graph exit falls
    // through to parent edges: the built-in isLookupMissNeedingMemory matches
    // Admit? No, the condition checks event.kind == SlcLookupDone.
    // Actually, the first matching parent edge wins.
    // With the event being Admit, the built-in edge Idle--Admit-->SlcLookup
    // is from Idle, not from SlcLookup. So it won't match here.
    // The CompAck edge from SlcLookup won't match Admit either.
    EXPECT_EQ(state, PocqState::SlcLookup);
    EXPECT_FALSE(subGraph.isActive());

    // Step 3: CompAck fires → parent edge SlcLookup --CompAck--> Idle
    step = topGraph.tryStep(state, event(PocqEventKind::CompAck));
    EXPECT_TRUE(step.stepped);
    EXPECT_EQ(state, PocqState::Idle);
    EXPECT_EQ(step.actions.front(), PocqActionKind::Retire);
}

TEST(HnfPocqStateGraphTest, SubGraphInProgressWaitsForNextEvent)
{
    // Sub-graph: Idle --Admit--> SlcLookup --SlcUpdateDone--> TxLink (Exit)
    POCQ_StateGraph subGraph;
    PocqNode& subIdle = subGraph.nodeFor(PocqState::Idle);
    PocqNode& subMid = subGraph.nodeFor(PocqState::SlcLookup);
    PocqNode& subExit = subGraph.nodeFor(PocqState::TxLink);
    subGraph.markPublicNode(subIdle, SubGraphPublicState::Start);
    subGraph.markPublicNode(subExit, SubGraphPublicState::Exit);
    subGraph.addTransition(subIdle, subMid, isAdmit,
                           {PocqActionKind::DoSlcLookup});
    subGraph.addTransition(subMid, subExit, isSlcUpdateDone,
                           {PocqActionKind::UpdateSlcSf});

    // Top graph: Idle --Admit--> SlcLookup (composite)
    POCQ_StateGraph topGraph;
    PocqNode& topIdle = topGraph.nodeFor(PocqState::Idle);
    PocqNode& topActive = topGraph.nodeFor(PocqState::SlcLookup);
    topActive.setSubGraph(&subGraph);

    // Step 1: Idle --Admit--> SlcLookup (composite), enters subGraph
    PocqState state = PocqState::Idle;
    topGraph.tryStep(state, event(PocqEventKind::Admit));
    EXPECT_EQ(state, PocqState::SlcLookup);
    EXPECT_TRUE(subGraph.isActive());

    // Step 2: Sub-graph: subIdle --Admit--> subMid (not exit yet)
    PocqStepResult step = topGraph.tryStep(
        state, event(PocqEventKind::Admit));
    EXPECT_TRUE(step.stepped);
    EXPECT_EQ(step.actions.front(), PocqActionKind::DoSlcLookup);
    EXPECT_EQ(state, PocqState::SlcLookup);
    EXPECT_TRUE(subGraph.isActive());

    // Step 3: Sub-graph: subMid --SlcUpdateDone--> subExit (Exit)
    // The same event does NOT match any parent edge from SlcLookup,
    // so parent stays at SlcLookup but sub-graph exits.
    step = topGraph.tryStep(state, event(PocqEventKind::SlcUpdateDone));
    EXPECT_TRUE(step.stepped);
    EXPECT_EQ(step.actions.front(), PocqActionKind::UpdateSlcSf);
    EXPECT_EQ(state, PocqState::SlcLookup);
    EXPECT_FALSE(subGraph.isActive());
}

TEST(HnfPocqStateGraphTest, PrintStateShowsRecursiveHierarchy)
{
    // Sub-graph: Start=Idle, Exit=Idle
    POCQ_StateGraph subGraph;
    PocqNode& subIdle = subGraph.nodeFor(PocqState::Idle);
    subGraph.markPublicNode(subIdle, SubGraphPublicState::Start);
    subGraph.markPublicNode(subIdle, SubGraphPublicState::Exit);

    // Top graph: SlcLookup is composite
    POCQ_StateGraph topGraph;
    PocqNode& topActive = topGraph.nodeFor(PocqState::SlcLookup);
    topActive.setSubGraph(&subGraph);

    // Enter composite node
    PocqState state = PocqState::Idle;
    topGraph.tryStep(state, event(PocqEventKind::Admit));

    // printState on the active node: "SlcLookup > Idle"
    std::ostringstream oss;
    const PocqNode& node = topGraph.nodeFor(state);
    node.printState(oss);
    EXPECT_EQ(oss.str(), "SlcLookup > Idle");
}

} // namespace gem5::Chi
