"""Passive, exact-byte per-instance mirrors of the four legacy TXT reports."""
import hashlib

COMMIT = '65f3241a6cd486dffddaca251536f5c691af9a1f'
BASELINE_SHA256 = {
    'src/cpu/pred/ITTAGE.cc': 'fee9b31520b5bdb18a67955d63ac9b063914c8dacedeba9b4aca85b4b2c45c97',
    'src/cpu/o3/commit.cc': '9d266932c391b426e33e7ae804d451bf8ea5ba5383013e1355ea32a4db150721',
}

COPY_HELPER = r'''
// BPU_PHASE_PASSIVE_MIRROR_V1: host output only, original writer is unchanged.
namespace {
void phaseMirrorFile(const std::string &source, const std::string &destination)
{
    std::ifstream in(gem5::simout.resolve(source), std::ios::binary);
    std::ofstream out(gem5::simout.resolve(destination),
                      std::ios::binary | std::ios::trunc);
    if (!in.good() || !out.good())
        throw std::runtime_error("BPU mirror: cannot open " + source);
    std::array<char, 8192> buffer{};
    while (in.read(buffer.data(), buffer.size()) || in.gcount())
        out.write(buffer.data(), in.gcount());
    if (!in.eof() || in.bad())
        throw std::runtime_error("BPU mirror: read failed " + source);
    out.flush();
    if (!out.good()) throw std::runtime_error("BPU mirror: write failed");
    out.close();
    if (out.fail()) throw std::runtime_error("BPU mirror: close failed");
}
} // anonymous namespace
'''


def substitutions(path):
    include = '#include "base/output.hh"\n'
    edits = [(include, include + '#include <array>\n#include <fstream>\n#include <stdexcept>\n')]
    edits += [('namespace gem5\n{', COPY_HELPER + '\nnamespace gem5\n{')]
    if path.endswith('ITTAGE.cc'):
        anchor = '            simout.close(out_handle);\n        }\n    });'
        calls = ''.join('        phaseMirrorFile("' + f + '", name() + ".' + f + '");\n'
                        for f in ('altuseCnt.txt', 'TableHitCnt.txt', 'missHistMap.txt'))
        edits.append((anchor, '            simout.close(out_handle);\n        }\n' + calls + '    });'))
    else:
        anchor = '        simout.close(out_handle);\n    });'
        edits.append((anchor, '        simout.close(out_handle);\n'
                      '        phaseMirrorFile("misPredIndirect.txt", name() + ".misPredIndirect.txt");\n    });'))
    return edits


def prepare(path, data):
    """Accept exact baseline or exact own output, rejecting unrelated edits."""
    text = data.decode('utf-8')
    edits = substitutions(path)
    if hashlib.sha256(data).hexdigest() != BASELINE_SHA256[path]:
        restored = text
        for before, after in reversed(edits):
            if restored.count(after) != 1:
                raise ValueError('unknown source or incomplete mirror: ' + path)
            restored = restored.replace(after, before, 1)
        if hashlib.sha256(restored.encode()).hexdigest() != BASELINE_SHA256[path]:
            raise ValueError('refusing independent source changes: ' + path)
        text = restored
    original = text.encode()
    for before, after in edits:
        if text.count(before) != 1:
            raise ValueError('mirror exact context mismatch: ' + path)
        text = text.replace(before, after, 1)
    return original, text.encode()
