import { parseCustomModelIds } from './models';

it('normalizes pasted model IDs without duplicate entries', () => {
  expect(parseCustomModelIds(' model-a,org/model-b:latest\r\nmodel-a\n model-c@v1 ')).toEqual([
    'model-a',
    'org/model-b:latest',
    'model-c@v1',
  ]);
});

it('accepts an empty optional model list', () => {
  expect(parseCustomModelIds(' ,\r\n ')).toEqual([]);
});

it.each(['model with spaces', '<model>', '-model', '模型', 'a'.repeat(257)])(
  'rejects invalid model ID %s',
  (value) => {
    expect(parseCustomModelIds(value)).toBeNull();
  },
);

it('rejects more than 50 model IDs', () => {
  expect(
    parseCustomModelIds(Array.from({ length: 51 }, (_, index) => `model-${index}`).join(',')),
  ).toBeNull();
});
