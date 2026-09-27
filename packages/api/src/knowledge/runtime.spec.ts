import { getChatModelClass, Providers } from '@librechat/agents';
import type { initializeKnowledgeRuntime } from './runtime';

jest.mock('@librechat/agents', () => ({
  Providers: jest.requireActual('@librechat/agents').Providers,
  getChatModelClass: jest.fn(),
}));

const loadModelClass = jest.mocked(getChatModelClass);

describe('knowledge runtime startup', () => {
  let initialize: typeof initializeKnowledgeRuntime;

  beforeEach(() => {
    loadModelClass.mockReset();
    jest.isolateModules(() => {
      initialize =
        jest.requireActual<typeof import('./runtime')>('./runtime').initializeKnowledgeRuntime;
    });
  });

  it('does not load provider modules when knowledge is disabled', () => {
    initialize(undefined);
    initialize({ enabled: false });
    expect(loadModelClass).not.toHaveBeenCalled();
  });

  it('loads every built-in provider before completing startup without constructing models', () => {
    const constructor = jest.fn();
    loadModelClass.mockReturnValue(constructor as never);

    initialize({ enabled: true });

    expect(loadModelClass.mock.calls.map(([provider]) => provider)).toEqual(
      Object.values(Providers),
    );
    expect(constructor).not.toHaveBeenCalled();
    expect(loadModelClass.mock.calls.every((args) => args.length === 1)).toBe(true);
  });

  it('reuses the completed startup across configurations and later requests', () => {
    initialize({ enabled: true });
    const calls = loadModelClass.mock.calls.length;
    initialize({ enabled: false });
    initialize({ enabled: true });
    expect(loadModelClass).toHaveBeenCalledTimes(calls);
  });

  it('fails startup with a fixed error when a provider module cannot load', () => {
    loadModelClass.mockImplementationOnce(() => {
      throw new Error('private-provider-configuration');
    });

    expect(() => initialize({ enabled: true })).toThrow(new Error('KNOWLEDGE_RUNTIME_UNAVAILABLE'));
    expect(loadModelClass).toHaveBeenCalledTimes(1);

    loadModelClass.mockReset();
    initialize({ enabled: true });
    expect(loadModelClass).toHaveBeenCalledTimes(Object.values(Providers).length);
  });
});
