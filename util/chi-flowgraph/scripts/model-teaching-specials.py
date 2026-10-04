"""Append the 15 reviewed special protocol illustrations to the demo catalog."""
import json
from teaching_flow import DEST
from teaching_control import build_cases as control_cases
from teaching_data import build_cases as data_cases
from teaching_races import build_cases as race_cases


def main():
    cases = race_cases() + control_cases() + data_cases()
    assert len(cases) == len({c.key for c in cases}) == 15
    catalog = json.loads((DEST / 'catalog.json').read_text(encoding='utf-8'))
    new = [case.write() for case in cases]
    ids = {e['id'] for e in new}
    catalog = [e for e in catalog if e['id'] not in ids] + new
    (DEST / 'catalog.json').write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'Published {len(new)} protocol illustrations; {len(catalog)} total demos. Not CPU coverage.')


if __name__ == '__main__':
    main()
