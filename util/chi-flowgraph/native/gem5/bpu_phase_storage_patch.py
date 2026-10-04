"""Exact, standalone model05 -> bounded lossless BPU report-history patch.

The existing scratch-lifetime change is retained. Prediction, training, events,
clocks and original delta calculations are not changed. Not a default installer.
"""
COMMIT = '65f3241a6cd486dffddaca251536f5c691af9a1f'
HH = 'src/cpu/pred/btb/decoupled_bpred.hh'
CC = 'src/cpu/pred/btb/decoupled_bpred.cc'
STATS = 'src/cpu/pred/btb/decoupled_bpred_stats.cc'
HELPER = 'src/cpu/pred/btb/BpuPhaseSpool.hh'
BASELINE_SHA256 = {
    HH: '13c808b6a860e5318600cd73001d53102a033cbdfc007d62a67ff70fb0a070cc',
    CC: 'a95ab3cf98dc0e90f9bd5e3f76f4411bb5f3904243b5419c468326068c19c8b2',
    STATS: 'd36d8982f32df1d185aab54f59d1049ee2b3fd03ee638a067213f912d4ebcc29',
}
UPSTREAM_HH_SHA256 = '8a4c3955be7ad01a6672e87502931c0b6878b231df52865a29e6b789c8fd1dd1'


def replace_exact(text, old, new, count=1):
    if text.count(old) != count:
        raise ValueError('unexpected BPU phase patch context: ' + old[:100])
    return text.replace(old, new)


def between(text, start, end, replacement):
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError('ambiguous phase formatter boundary')
    a, b = text.index(start), text.index(end)
    if b <= a:
        raise ValueError('reversed phase formatter boundary')
    return text[:a] + replacement + text[b:]


def scratch_source(upstream):
    old = '            stagePred.indirectTargets.clear();\n            stagePred.btbEntries.clear();\n'
    return replace_exact(upstream, old, old + '''            // Per-prediction TAGE -> MGSC communication, not predictor
            // tables or the saved training/squash metadata. Keep finalPred
            // copies bounded by the branches in the current prediction.
            stagePred.tageInfoForMgscs.clear();
''')


def patches(original):
    hh, cc, stats = (original[name] for name in (HH, CC, STATS))
    hh = replace_exact(hh, '#include "cpu/pred/btb/abtb.hh"',
                       '#include "cpu/pred/btb/abtb.hh"\n#include "cpu/pred/btb/BpuPhaseSpool.hh"')
    declarations = [
        '    std::vector<BranchStatsMap> topMispredictsByBranchByPhase;',
        '    std::vector<BranchStatsMap> topMispredictsByBranchBySubPhase;',
        '    std::vector<std::unordered_map<Addr, int>> takenBranchesByPhase;',
        '    std::vector<std::unordered_map<Addr, int>> takenBranchesBySubPhase;',
        '    std::vector<std::unordered_map<Addr, std::pair<BTBEntry, int>>> BTBEntriesByPhase;',
        '    std::vector<std::vector<int>> fsqEntryNumCommittedInstDistByPhase;',
        '    std::vector<std::vector<int>> fsqEntryNumFetchedInstDistByPhase;',
    ]
    for index, declaration in enumerate(declarations):
        # The header also contains commented-out historical declarations.
        hh = replace_exact(hh, '\n' + declaration + '\n', '\n' + (
            '    std::array<phase_storage::Spool, 5> phaseHistory;\n'
            '    uint64_t phaseDumpId{0};\n' if index == 0 else
            '    // Complete snapshots are retained in phaseHistory journals.\n'))
    for variable in ('phaseIdToDump', 'numInstCommitted', 'phaseSizeByInst', 'subPhaseIdToDump'):
        hh = replace_exact(hh, '    int ' + variable + '{', '    uint64_t ' + variable + '{')
    hh = replace_exact(hh, '    int subPhaseSizeByInst()', '    uint64_t subPhaseSizeByInst()')
    hh = replace_exact(hh,
        'bool processPhase(bool isSubPhase, int phaseID, int &phaseToDump,',
        'bool processPhase(bool isSubPhase, uint64_t phaseID, uint64_t &phaseToDump,')
    hh = replace_exact(hh,
        '                     std::vector<BranchStatsMap> &phaseStatsList,\n', '')
    hh = replace_exact(hh,
        '                     std::unordered_map<Addr, int> &currentPhaseBranches,\n'
        '                     std::vector<std::unordered_map<Addr, int>> &phaseBranchesList);',
        '                     std::unordered_map<Addr, int> &currentPhaseBranches);')
    cc = replace_exact(cc, '    registerExitCallback([this]() {\n        this->dumpStats();',
        '''    for (size_t i = 0; i < phaseHistory.size(); ++i) {
        phaseHistory[i].configure(simout.resolve(
            name() + "." + phase_storage::FileNames[i] + ".spool"));
    }

    registerExitCallback([this]() {
        this->dumpStats();''')
    stats = replace_exact(stats,
        '''    // Helper function: create output file and write header
    auto createOutputFile = [](const std::string& filename, const std::string& header) {
        auto handle = simout.create(filename, false, true);
        *handle->stream() << header << std::endl;
        return handle;
    };''',
        '''    // A successful dump preserves both the old shared names and all BPUs.
    const auto dumpPrefix = name() + ".dump-" + std::to_string(phaseDumpId) + ".";
    auto openOutputFile = [&](const std::string& filename) {
        return std::make_unique<phase_storage::Report>(simout.resolve(filename),
            simout.resolve(name() + "." + filename),
            simout.resolve(dumpPrefix + filename));
    };
    auto createOutputFile = [&](const std::string& filename, const std::string& header) {
        auto handle = openOutputFile(filename);
        *handle->stream() << header << std::endl;
        return handle;
    };
    phase_storage::require(phaseHistory[0].size() == phaseHistory[2].size() &&
        phaseHistory[0].size() == phaseHistory[3].size() &&
        phaseHistory[0].size() == phaseHistory[4].size(), "incomplete main phase group");
    auto writePhaseReport = [&](size_t index, const std::string& header) {
        const std::string filename = phase_storage::FileNames[index];
        auto report = createOutputFile(filename, header);
        phase_storage::Report raw(simout.resolve(name() + "." + filename + ".records.jsonl"),
            "", simout.resolve(dumpPrefix + filename + ".records.jsonl"));
        phaseHistory[index].replay([&](uint64_t, const std::string &record,
                                       const std::string &row) {
            *report->stream() << row;
            *raw.stream() << record << '\\n';
        });
        report->close(); raw.close();
    };''')
    stats = between(stats, '    // Create CSV header for topN tables\n',
        '    // 5. Output history misprediction data\n',
        '''    writePhaseReport(0, phase_storage::branchHeader(false));
    writePhaseReport(1, phase_storage::branchHeader(true));

''')
    stats = between(stats, '    // Process FSQ distribution data\n',
        '    // Save the database\n',
        '''    writePhaseReport(2, phase_storage::fsqHeader(maxInstsNum));
    writePhaseReport(3, phase_storage::fsqHeader(maxInstsNum));
    writePhaseReport(4, phase_storage::btbHeader());

    phase_storage::Report manifest(simout.resolve(name() + ".phase-manifest.json"),
        "", simout.resolve(dumpPrefix + "phase-manifest.json"));
    *manifest.stream() << "{\\"schemaVersion\\":1,\\"bpuName\\":\\"" << name()
        << "\\",\\"numInstCommitted\\":" << numInstCommitted
        << ",\\"phaseSizeByInst\\":" << phaseSizeByInst
        << ",\\"subPhaseSize\\":" << subPhaseSizeByInst()
        << ",\\"mainPhases\\":" << phaseHistory[0].size()
        << ",\\"subPhases\\":" << phaseHistory[1].size()
        << ",\\"partialMainInsts\\":" << numInstCommitted % phaseSizeByInst
        << ",\\"partialSubInsts\\":" << numInstCommitted % subPhaseSizeByInst()
        << ",\\"completeRecords\\":true,\\"dumpId\\":" << phaseDumpId << "}\\n";
    manifest.close();
    for (auto &journal : phaseHistory) journal.close();
    phaseDumpId = phase_storage::nextCount(phaseDumpId);

''')
    # Only this custom BPU report method uses these output handles.
    dump_end = stats.index('DecoupledBPUWithBTB::BpTrace::BpTrace')
    stats = stats[:dump_end].replace('simout.close(outFile);', 'outFile->close();') + stats[dump_end:]
    stats = replace_exact(stats,
        'DecoupledBPUWithBTB::processPhase(bool isSubPhase, int phaseID, int &phaseToDump,',
        'DecoupledBPUWithBTB::processPhase(bool isSubPhase, uint64_t phaseID, uint64_t &phaseToDump,')
    stats = replace_exact(stats,
        '                                std::vector<BranchStatsMap> &phaseStatsList,\n', '')
    stats = replace_exact(stats,
        '                                std::unordered_map<Addr, int> &currentPhaseBranches,\n'
        '                                std::vector<std::unordered_map<Addr, int>> &phaseBranchesList)',
        '                                std::unordered_map<Addr, int> &currentPhaseBranches)')
    stats = replace_exact(stats, 'DPRINTF(Profiling, "dump %s phase %d\\n",',
                          'DPRINTF(Profiling, "dump %s phase %llu\\n",')
    stats = replace_exact(stats, 'isSubPhase ? "sub" : "main", phaseToDump);',
                          'isSubPhase ? "sub" : "main", static_cast<unsigned long long>(phaseToDump));')
    stats = replace_exact(stats, '''    phaseStatsList.push_back(currentPhaseStats);

    // Handle taken branches map
    phaseBranchesList.push_back(currentPhaseBranches);
    currentPhaseBranches.clear();

    // Increment phase counter for next time
    phaseToDump++;''', '''    // Match the old vector push_back's unordered-map copy before formatting.
    const auto takenSnapshot = currentPhaseBranches;
    const auto record = phase_storage::branchRecord(
        isSubPhase, phaseToDump - 1, currentPhaseStats, takenSnapshot);
    phaseHistory[isSubPhase ? 1 : 0].append(phaseToDump - 1, record.first, record.second);
    currentPhaseBranches.clear();

    // This counter is report-only; overflow is an explicit error, never wrap.
    phaseToDump = phase_storage::nextCount(phaseToDump);''')
    stats = replace_exact(stats, '    numInstCommitted++;',
                          '    numInstCommitted = phase_storage::nextCount(numInstCommitted);')
    for identifier in ('currentPhaseID', 'currentSubPhaseID'):
        stats = replace_exact(stats, '        int ' + identifier + ' =',
                              '        uint64_t ' + identifier + ' =')
    for line in ('                        topMispredictsByBranchByPhase,\n',
                 '                    topMispredictsByBranchBySubPhase,\n'):
        stats = replace_exact(stats, line, '')
    stats = replace_exact(stats,
        '                        currentPhaseTakenBranches,\n                        takenBranchesByPhase))',
        '                        currentPhaseTakenBranches))')
    stats = replace_exact(stats,
        '                    currentSubPhaseTakenBranches,\n                    takenBranchesBySubPhase);',
        '                    currentSubPhaseTakenBranches);')
    stats = replace_exact(stats, '''            // Store the distributions
            fsqEntryNumCommittedInstDistByPhase.push_back(committedInstDist);
            fsqEntryNumFetchedInstDistByPhase.push_back(fetchedInstDist);

            // Process BTB entries
            BTBEntriesByPhase.push_back(processBTBEntries());''',
        '''            // Persist all bins and BTB fields; no phase-length vector remains.
            const uint64_t id = currentPhaseID - 1;
            const auto committed = phase_storage::fsqRecord(
                false, id, committedInstDist, maxInstsNum, phaseSizeByInst);
            phaseHistory[2].append(id, committed.first, committed.second);
            const auto fetched = phase_storage::fsqRecord(
                true, id, fetchedInstDist, maxInstsNum, phaseSizeByInst);
            phaseHistory[3].append(id, fetched.first, fetched.second);
            const auto entries = processBTBEntries();
            const auto btb = phase_storage::btbRecord(id, entries);
            phaseHistory[4].append(id, btb.first, btb.second);''')
    # Cumulative report-only counters previously had signed overflow. These are
    # not predictor table counters, branch types, or core/FTQ state.
    for field in ('totalCount', 'mispredCount', 'dirWrongCount', 'targetWrongCount', 'noPredCount'):
        hh = replace_exact(hh, '        int ' + field + ';', '        uint64_t ' + field + ';')
        hh = replace_exact(hh, field + '++;', field + ' = phase_storage::nextCount(' + field + ');')
    hh = replace_exact(hh,
        'return totalCount > 0 ? (double)(mispredCount * 1000) / totalCount : 0;',
        'return phase_storage::perMille(mispredCount, totalCount);')
    hh = replace_exact(hh, 'std::map<std::pair<Addr, Addr>, int> topMispredicts;',
                       'std::map<std::pair<Addr, Addr>, uint64_t> topMispredicts;')
    # All occurrences of these three container types in these two files are the
    # report tracking/phase delta interfaces, including the taken-map lambda.
    hh = hh.replace('std::pair<BTBEntry, int>', 'std::pair<BTBEntry, uint64_t>')
    hh = hh.replace('std::unordered_map<Addr, int>', 'std::unordered_map<Addr, uint64_t>')
    hh = hh.replace('std::vector<int>', 'std::vector<uint64_t>')
    stats = stats.replace('std::pair<BTBEntry, int>', 'std::pair<BTBEntry, uint64_t>')
    stats = stats.replace('std::unordered_map<Addr, int>', 'std::unordered_map<Addr, uint64_t>')
    stats = stats.replace('std::vector<int>', 'std::vector<uint64_t>')
    stats = replace_exact(stats, 'std::vector<std::pair<std::pair<Addr, Addr>, int>> topMisPredPC(',
                          'std::vector<std::pair<std::pair<Addr, Addr>, uint64_t>> topMisPredPC(')
    stats = replace_exact(stats, 'std::tuple<Addr, int, int, int, double, int, int, int>',
                          'std::tuple<Addr, int, uint64_t, uint64_t, double, uint64_t, uint64_t, uint64_t>')
    stats = replace_exact(stats, '    int mispCntThres = 100;', '    uint64_t mispCntThres = 100;')
    for index in (5, 6, 7):
        stats = replace_exact(stats, '(int)std::get<' + str(index) + '>(entry)',
                              'std::get<' + str(index) + '>(entry)')
    for expression in ('it->second.second', 'commitFsqEntryHasInstsVector[target.commitInstNum]',
                       'commitFsqEntryFetchedInstsVector[target.fetchInstNum]', 'find_it->second'):
        stats = replace_exact(stats, expression + '++;',
                              expression + ' = phase_storage::nextCount(' + expression + ');')
    stats = replace_exact(stats, '            it->second++;',
                          '            it->second = phase_storage::nextCount(it->second);')
    stats = replace_exact(stats, 'DPRINTF(Profiling, "found, total %d, miss %d\\n",\n'
                          '                statsIt->second.totalCount, statsIt->second.mispredCount);',
                          'DPRINTF(Profiling, "found, total %llu, miss %llu\\n",\n'
                          '                static_cast<unsigned long long>(statsIt->second.totalCount),\n'
                          '                static_cast<unsigned long long>(statsIt->second.mispredCount));')
    return {HH: hh, CC: cc, STATS: stats}
