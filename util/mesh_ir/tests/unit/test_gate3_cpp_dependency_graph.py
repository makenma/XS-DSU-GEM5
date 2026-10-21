import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture(scope="module")
def dependency_graph_probe(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mesh_ir_dependency_graph")
    source = directory / "probe.cc"
    executable = directory / "probe"
    probe_object = directory / "probe.o"
    graph_object = directory / "mesh_ir_dependency_graph.o"
    probe_dependencies = directory / "probe.d"
    graph_dependencies = directory / "mesh_ir_dependency_graph.d"
    source.write_text(
        r'''
#include <iostream>
#include <string>
#include <vector>

#include "dev/ai_mesh/mesh_ir_dependency_graph.hh"

using namespace gem5::ai_mesh;

bool
buildBranching(VerifiedDependencyGraph &graph, MeshLoadError &error)
{
    return buildVerifiedDependencyGraph(
        {2, 0, 3, 1},
        {{2, 1}, {2, 1}, {2, 4}, {1, 3}, {4, 3}}, graph, error);
}

int
canonical()
{
    VerifiedDependencyGraph graph;
    MeshLoadError error{"E_STALE", "stale"};
    if (!buildBranching(graph, error) || !error.code.empty() ||
        graph.canonicalOrder() != std::vector<size_t>{2, 4, 1, 3})
        return 1;
    bool answer = true;
    if (!graph.happensBefore(2, 3, answer, error) || !answer ||
        !error.code.empty())
        return 2;
    if (!graph.happensBefore(4, 1, answer, error) || answer ||
        !error.code.empty())
        return 3;
    if (!graph.happensBefore(3, 2, answer, error) || answer ||
        !error.code.empty())
        return 4;
    if (!graph.happensBefore(2, 2, answer, error) || answer ||
        !error.code.empty())
        return 5;
    return 0;
}

int
emptyAndIsolated()
{
    VerifiedDependencyGraph graph;
    MeshLoadError error;
    bool answer = true;
    if (graph.happensBefore(1, 1, answer, error) || !answer ||
        error.code != "E_ABI_BOUNDS")
        return 1;
    if (!buildVerifiedDependencyGraph({}, {}, graph, error) ||
        !graph.canonicalOrder().empty() || !error.code.empty())
        return 2;
    answer = true;
    if (graph.happensBefore(1, 1, answer, error) || !answer ||
        error.code != "E_ABI_BOUNDS")
        return 3;
    if (!buildVerifiedDependencyGraph({1, 0, 2}, {}, graph, error) ||
        graph.canonicalOrder() != std::vector<size_t>{2, 1, 3} ||
        !error.code.empty())
        return 4;
    answer = true;
    if (!graph.happensBefore(1, 3, answer, error) || answer ||
        !error.code.empty())
        return 5;
    return 0;
}

int
rejectionsAndAtomicPublication()
{
    VerifiedDependencyGraph graph;
    MeshLoadError error;
    if (!buildBranching(graph, error))
        return 1;
    const auto order = graph.canonicalOrder();
    for (const auto &candidate : std::vector<std::vector<size_t>>{
             {0, 0}, {0, 2}, {1, 1}}) {
        if (buildVerifiedDependencyGraph(candidate, {}, graph, error) ||
            error.code != "E_ABI_BOUNDS" || graph.canonicalOrder() != order)
            return 2;
    }
    if (buildVerifiedDependencyGraph({0, 1}, {{0, 1}}, graph, error) ||
        error.code != "E_ABI_BOUNDS" || graph.canonicalOrder() != order)
        return 3;
    if (buildVerifiedDependencyGraph({0}, {{1, 1}, {2, 1}}, graph, error) ||
        error.code != "E_ABI_BOUNDS" || graph.canonicalOrder() != order)
        return 4;
    if (buildVerifiedDependencyGraph({0}, {{1, 1}}, graph, error) ||
        error.code != "E_DEPENDENCY_CYCLE" || graph.canonicalOrder() != order)
        return 5;
    if (buildVerifiedDependencyGraph({0, 1}, {{1, 2}, {2, 1}}, graph, error) ||
        error.code != "E_DEPENDENCY_CYCLE" || graph.canonicalOrder() != order)
        return 6;
    bool answer = true;
    if (graph.happensBefore(0, 1, answer, error) || !answer ||
        error.code != "E_ABI_BOUNDS")
        return 7;
    if (!graph.happensBefore(2, 3, answer, error) || !answer ||
        !error.code.empty())
        return 8;
    return 0;
}

int
treeTp1Scale()
{
    constexpr size_t commandCount = 3139;
    constexpr size_t eventCount = 3136;
    constexpr size_t completionPopulation = 2 * commandCount + eventCount;
    constexpr size_t population = completionPopulation;
    std::vector<size_t> ranks(population);
    for (size_t index = 0; index < population; ++index)
        ranks[index] = index;
    std::vector<DependencyGraphEdge> edges;
    for (size_t node = 2; node <= population; ++node)
        edges.push_back({node - 1, node});
    edges.push_back({1, population});
    VerifiedDependencyGraph graph;
    MeshLoadError error;
    if (!buildVerifiedDependencyGraph(ranks, edges, graph, error) ||
        graph.canonicalOrder().size() != population || !error.code.empty())
        return 1;
    bool answer = false;
    if (!graph.happensBefore(1, population, answer, error) || !answer ||
        !error.code.empty())
        return 2;
    if (!graph.happensBefore(2, population, answer, error) || !answer ||
        !error.code.empty())
        return 3;
    if (!graph.happensBefore(population, 1, answer, error) || answer ||
        !error.code.empty())
        return 4;
    return 0;
}

int
main(int argc, char **argv)
{
    const std::string mode = argc == 2 ? argv[1] : "";
    int result = 1;
    if (mode == "canonical")
        result = canonical();
    else if (mode == "empty")
        result = emptyAndIsolated();
    else if (mode == "atomic")
        result = rejectionsAndAtomicPublication();
    else if (mode == "scale")
        result = treeTp1Scale();
    if (result == 0)
        std::cout << "OK";
    return result;
}
'''.lstrip(),
        encoding="utf-8",
    )
    for translation_unit, object_file, dependency_file in (
        (source, probe_object, probe_dependencies),
        (
            ROOT / "src/dev/ai_mesh/mesh_ir_dependency_graph.cc",
            graph_object,
            graph_dependencies,
        ),
    ):
        compiled = subprocess.run(
            [
                "g++",
                "-std=c++17",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-pedantic-errors",
                "-I",
                str(ROOT / "src"),
                "-MMD",
                "-MF",
                str(dependency_file),
                "-c",
                str(translation_unit),
                "-o",
                str(object_file),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert compiled.returncode == 0, compiled.stderr
    linked = subprocess.run(
        ["g++", str(probe_object), str(graph_object), "-o", str(executable)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert linked.returncode == 0, linked.stderr
    return executable


@pytest.mark.parametrize("mode", ("canonical", "empty", "atomic", "scale"))
def test_cpp_dependency_graph_admits_only_checked_canonical_dags(
    dependency_graph_probe, mode,
):
    result = subprocess.run(
        [str(dependency_graph_probe), mode],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "OK"
