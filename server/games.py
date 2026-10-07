"""Shared, server-authoritative assessment of configurable learning games."""
from fastapi import HTTPException

TYPES = {'sequence', 'sort', 'match', 'errors', 'scenario', 'documents', 'hotspots'}

def validate(b, publish=False):
    def fail(message): raise HTTPException(422, message)
    if type(b.get('points', 10)) is not int or not 0 <= b.get('points', 10) <= 100:
        fail('Баллы игры: от 0 до 100')
    if not isinstance(b.get('hint', ''), str) or len(b.get('hint', '')) > 5000:
        fail('Подсказка: до 5000 символов')
    if b['type'] == 'scenario':
        steps = b.get('steps', [])
        if not isinstance(steps, list) or not 1 <= len(steps) <= 30: fail('Добавьте от 1 до 30 шагов ситуации')
        for si, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get('text'), str) or len(step['text']) > 5000: fail('Заполните текст шага')
            if publish and not step['text'].strip(): fail('Заполните описание каждого шага')
            options = step.get('options', [])
            if not isinstance(options, list) or not 2 <= len(options) <= 10: fail('Шаг: от 2 до 10 действий')
            if type(step.get('correct')) is not int or not 0 <= step['correct'] < len(options): fail('Укажите верное действие шага')
            for o in options:
                if not isinstance(o, dict) or any(not isinstance(o.get(k, ''), str) or len(o.get(k, '')) > 5000 for k in ('text', 'consequence')): fail('Неверное действие ситуации')
                nxt = o.get('next', -1)
                if type(nxt) is not int or (nxt != -1 and not si < nxt < len(steps)): fail('Переход ведёт на следующий шаг или завершает ситуацию')
                if publish and not o.get('text', '').strip(): fail('Заполните действия ситуации')
        return
    items = b.get('items', [])
    if not isinstance(items, list) or not 2 <= len(items) <= 30 or any(not isinstance(x, str) or len(x) > 2000 for x in items): fail('Игра: от 2 до 30 карточек')
    if publish and any(not x.strip() for x in items): fail('Заполните карточки игры')
    correct = b.get('correct', [])
    if not isinstance(correct, list) or any(type(x) is not int for x in correct): fail('Неверный ключ игры')
    if b['type'] == 'sequence':
        if sorted(correct) != list(range(len(items))): fail('Порядок должен содержать каждую карточку один раз')
    elif b['type'] in ('sort', 'match'):
        targets = b.get('targets', [])
        if not isinstance(targets, list) or not 2 <= len(targets) <= 30 or any(not isinstance(x, str) or not x.strip() or len(x)>2000 for x in targets): fail('Заполните категории или пары')
        if len(correct) != len(items) or any(not 0 <= x < len(targets) for x in correct): fail('Укажите соответствие каждой карточки')
        if publish and b['type'] == 'match' and (len(items) != len(targets) or len(set(correct)) != len(correct)): fail('В парах каждый ответ используется один раз')
    else:
        if len(set(correct)) != len(correct) or any(not 0 <= x < len(items) for x in correct) or (publish and not correct): fail('Выберите правильные карточки')
    if b['type'] == 'hotspots':
        regions = b.get('regions', [])
        if not isinstance(regions,list) or len(regions) != len(items): fail('Для каждой области задайте координаты')
        for r in regions:
            if not isinstance(r, dict) or any(type(r.get(k)) not in (int, float) or not 0 <= r[k] <= 100 for k in ('x','y','w','h')) or r['w'] <= 0 or r['h'] <= 0 or r['x']+r['w']>100 or r['y']+r['h']>100: fail('Область должна помещаться внутри изображения')
        if publish and not b.get('image'): fail('Загрузите изображение для поиска на экране')

def evaluate(b, answer):
    """Only submitted, structurally valid answers count towards completion."""
    if not isinstance(answer, dict) or answer.get('submitted') is not True: return False, False
    values = answer.get('values')
    if b['type'] == 'scenario':
        if not isinstance(values, list): return False, False
        index, right = 0, True
        for value in values:
            if index == -1 or type(value) is not int: return False, False
            step = b['steps'][index]
            if not 0 <= value < len(step['options']): return False, False
            right = right and value == step['correct']
            index = step['options'][value].get('next', -1)
        return index == -1, index == -1 and right
    if not isinstance(values, list) or any(type(v) is not int for v in values): return False, False
    size = len(b['items'])
    if b['type'] == 'sequence': valid = sorted(values) == list(range(size))
    elif b['type'] in ('sort','match'):
        valid = len(values) == size and all(0 <= v < len(b['targets']) for v in values)
        if b['type'] == 'match': valid = valid and len(set(values)) == len(values)
    else: valid = len(set(values)) == len(values) and all(0 <= v < size for v in values)
    expected = b['correct']
    right = values == expected if b['type'] in ('sequence','sort','match') else sorted(values) == sorted(expected)
    return valid, valid and right
