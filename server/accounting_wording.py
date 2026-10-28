"""Update existing accounting questions from the supplied training tests."""
import argparse
import copy
import json
from pathlib import Path

RELEASE = 'accounting-wording-20261028'
PATCHES = json.loads(Path(__file__).with_name(RELEASE + '.json').read_text())


def replace_questions(content, source):
    result = copy.deepcopy(content)
    if source != 'accounting':
        return result, 0
    changed = 0
    seen = set()
    for section in result.get('sections', []):
        for block in section.get('blocks', []):
            patch = PATCHES.get(block.get('id'))
            if not patch:
                continue
            if block['id'] in seen:
                raise ValueError('Duplicate question ID: ' + block['id'])
            seen.add(block['id'])
            if block.get('text') not in (patch['previous_text'], patch['content']['text']):
                raise ValueError('Question was independently edited; review before replacing: ' + block['id'])
            if block.get('type') not in ('single', 'multiple'):
                raise ValueError('Question type was independently edited: ' + block['id'])
            if any(block.get(k) != v for k, v in patch['content'].items()):
                block.update(patch['content'])
                changed += 1
    if seen != set(PATCHES):
        raise ValueError('Some original questions are missing; do not recreate deleted questions implicitly')
    return result, changed


def release(db_path, apply=False):
    from material_links import release as publish_content_release
    return publish_content_release(db_path, apply, release_id=RELEASE,
        transform=replace_questions, sources=('accounting',), change_kind='questions')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    import app
    print(json.dumps(release(app.DB_PATH, args.apply), ensure_ascii=False))
