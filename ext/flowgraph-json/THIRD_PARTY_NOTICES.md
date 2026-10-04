# JSON for Modern C++: provenance and third-party notices

This directory vendors the modular header distribution of **nlohmann/json 3.11.3**,
upstream <https://github.com/nlohmann/json>. The 45 headers under `nlohmann/`
were copied without modification from `/usr/include/nlohmann/` supplied by the
locally installed Ubuntu package `nlohmann-json3-dev 3.11.3-1`. All 45 vendored
headers were compared byte for byte with that package before this notice was added.
They are source dependencies of the optional CHI FlowGraph observer and census.

`LICENSE` contains the complete MIT (also called Expat) permission and warranty
text, transcribed from the installed package's
`/usr/share/doc/nlohmann-json3-dev/copyright` without changing its terms. Copyright
names and date ranges below follow the SPDX notices in the exact 3.11.3 headers;
these notices are retained in the source files. The package copyright document's
SHA-256 is `7c38f08817ef2ecc12cbe7a8a955a52de0e746ba48dbbd82fe1f9aeb4eb83df1`.

| Files | Copyright notice retained in the source | License |
| --- | --- | --- |
| `nlohmann/**` | 2013-2023 Niels Lohmann, <https://nlohmann.me> | MIT |
| `nlohmann/detail/conversions/to_chars.hpp` | 2009 Florian Loitsch, <https://florian.loitsch.com/> | MIT |
| `nlohmann/detail/output/serializer.hpp` | 2008-2009 Björn Hoehrmann, <bjoern@hoehrmann.de> | MIT |
| `nlohmann/thirdparty/hedley/hedley.hpp` | 2016-2021 Evan Nemerson, <evan@nemerson.com>; Hedley <https://nemequ.github.io/hedley> | MIT |
| `nlohmann/detail/meta/cpp_future.hpp` | 2018 The Abseil Authors | MIT |

The rows naming additional authors supplement the nlohmann/json notice. All
vendored headers carry `SPDX-License-Identifier: MIT`. The Ubuntu package's
unbundled test tools, fuzzer, amalgamation script, and Debian packaging sources
are not included here; their separate license entries do not describe files in
this vendored header subset. This notice adds provenance and does not modify the
source headers or their license terms.
