from __future__ import annotations

import functools
import operator


def render_python_features(schema: dict) -> list[str]:
    features = schema["features"]
    bits = {name: item["bit"] for name, item in features.items()}
    mandatory = functools.reduce(operator.or_, (item["bit"] for item in features.values() if item["mandatory"]), 0)
    known = functools.reduce(operator.or_, bits.values(), 0)
    requirements = {name: tuple(item["requires"]) for name, item in features.items()}
    sections = schema["enums"]["section_type"]
    conditional = {name: tuple(sections[section] for section in names) for name, names in schema["conditional_required_sections"].items()}
    gates = {sections[name]: functools.reduce(operator.or_, (bits[feature] for feature in names), 0) for name, names in schema["section_required_features"].items()}
    lines = ["class REQUIRED_FEATURE:"]
    lines.extend(f"    {name} = {value}" for name, value in bits.items())
    lines.extend(["", f"FEATURE_BITS = {bits!r}", f"REQUIRED_FEATURES = {mandatory}", f"KNOWN_FEATURE_MASK = {known}",
                  "FEATURE_MIN_WRITER_MINOR = " + repr({name: item["min_writer_minor"] for name, item in features.items()}),
                  "REQUIRED_FEATURE_MIN_READER_MINOR = " + repr({item["bit"]: item["min_reader_minor"] for item in features.values()}),
                  f"FEATURE_REQUIRES = {requirements!r}", f"CONDITIONAL_REQUIRED_SECTIONS = {conditional!r}", f"SECTION_REQUIRED_FEATURES = {gates!r}"])
    lines.extend(f"{name} = REQUIRED_FEATURE.{name}" for name in features)
    lines.extend(f"{name} = {value}" for name, value in schema["constants"].items())
    lines.extend([
        "", "def feature_requirements_met(features):",
        "    if type(features) is not int or features < 0 or features & ~KNOWN_FEATURE_MASK or features & REQUIRED_FEATURES != REQUIRED_FEATURES:",
        "        return False",
        "    return all(not features & bit or all(features & FEATURE_BITS[required] for required in FEATURE_REQUIRES[name]) for name, bit in FEATURE_BITS.items())",
        "", "def feature_min_writer_minor(features):",
        "    return max((FEATURE_MIN_WRITER_MINOR[name] for name, bit in FEATURE_BITS.items() if features & bit), default=0)",
        "", "def conditional_required_sections(features):",
        "    return tuple(sorted({section for name, sections in CONDITIONAL_REQUIRED_SECTIONS.items() if features & FEATURE_BITS[name] for section in sections}))", "",
    ])
    return lines


def render_cpp_features(schema: dict) -> list[str]:
    features = schema["features"]
    bits = {name: item["bit"] for name, item in features.items()}
    mandatory = functools.reduce(operator.or_, (item["bit"] for item in features.values() if item["mandatory"]), 0)
    known = functools.reduce(operator.or_, bits.values(), 0)
    sections = schema["enums"]["section_type"]
    lines = ["enum class RequiredFeature : uint64_t", "{"]
    lines.extend(f"    {name} = {value}ull," for name, value in bits.items())
    lines.extend(["};", f"constexpr uint64_t kRequiredFeatures = {mandatory}ull;", f"constexpr uint64_t kKnownFeatureMask = {known}ull;"])
    for name, item in features.items():
        cpp_name = name.title().replace("_", "")
        mask = functools.reduce(operator.or_, (bits[dependency] for dependency in item["requires"]), 0)
        lines.extend([f"constexpr uint64_t kFeature{cpp_name} = {item['bit']}ull;",
                      f"constexpr uint64_t kFeature{cpp_name}Requires = {mask}ull;",
                      f"constexpr uint16_t kFeature{cpp_name}MinWriterMinor = {item['min_writer_minor']};",
                      f"constexpr uint16_t kRequiredFeature{cpp_name}MinReaderMinor = {item['min_reader_minor']};"])
    lines.extend(["struct FeatureSpec", "{", "    uint64_t bit;", "    uint64_t requires_mask;", "    uint16_t min_writer_minor;", "    uint16_t min_reader_minor;", "};", "constexpr FeatureSpec kFeatureSpecs[] = {"])
    for name in features:
        cpp_name = name.title().replace("_", "")
        lines.append(f"    {{kFeature{cpp_name}, kFeature{cpp_name}Requires, kFeature{cpp_name}MinWriterMinor, kRequiredFeature{cpp_name}MinReaderMinor}},")
    lines.extend(["};", f"constexpr size_t kFeatureCount = {len(features)};",
                  "inline bool featureRequirementsMet(uint64_t features)", "{",
                  "    if ((features & ~kKnownFeatureMask) || (features & kRequiredFeatures) != kRequiredFeatures)", "        return false;",
                  "    for (const auto &spec : kFeatureSpecs)",
                  "        if ((features & spec.bit) && (features & spec.requires_mask) != spec.requires_mask)", "            return false;",
                  "    return true;", "}"])
    lines.extend(["inline uint64_t sectionRequiredFeature(uint16_t type)", "{", "    switch (type) {"])
    for name, names in schema["section_required_features"].items():
        mask = functools.reduce(operator.or_, (bits[feature] for feature in names), 0)
        lines.append(f"      case {sections[name]}: return {mask}ull;")
    lines.extend(["      default: return 0;", "    }", "}", "inline bool featureRequiresSection(uint64_t features, uint16_t type)", "{", "    switch (type) {"])
    owners = {}
    for name, names in schema["conditional_required_sections"].items():
        for section in names:
            owners[section] = owners.get(section, 0) | bits[name]
    lines.extend(f"      case {sections[name]}: return (features & {mask}ull) != 0;" for name, mask in owners.items())
    lines.extend(["      default: return false;", "    }", "}"])
    lines.append("constexpr uint16_t kConditionalSections[] = {" + ", ".join(str(sections[name]) for name in sorted(owners)) + "};")
    lines.extend(f"constexpr uint64_t k{name.title().replace('_', '')} = {value}ull;" for name, value in schema["constants"].items())
    return lines
