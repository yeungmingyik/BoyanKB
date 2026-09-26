import { userProvidedModelsSchema } from 'librechat-data-provider';

export function parseCustomModelIds(value: string): string[] | null {
  const models = [
    ...new Set(
      value
        .split(/[,\r\n]+/)
        .map((model) => model.trim())
        .filter(Boolean),
    ),
  ];
  const result = userProvidedModelsSchema.safeParse(models);
  return result.success ? result.data : null;
}
