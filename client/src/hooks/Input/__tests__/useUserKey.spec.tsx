import { renderHook } from '@testing-library/react';
import useUserKey from '../useUserKey';

let mockExpiry: string | null | undefined;
const mockMutate = jest.fn();

jest.mock('librechat-data-provider/react-query', () => ({
  useUserKeyQuery: () => ({
    data: mockExpiry === undefined ? undefined : { expiresAt: mockExpiry },
  }),
  useUpdateUserKeysMutation: () => ({ mutate: mockMutate }),
}));

jest.mock('~/data-provider', () => ({
  useGetEndpointsQuery: () => ({ data: {} }),
}));

describe('useUserKey', () => {
  it('keeps missing and revoked credentials distinct from a permanent key', () => {
    mockExpiry = undefined;
    const view = renderHook(() => useUserKey('OpenAI-Compatible'));
    expect(view.result.current.getExpiry()).toBeUndefined();
    mockExpiry = 'never';
    view.rerender();
    expect(view.result.current.getExpiry()).toBe('never');
    mockExpiry = null;
    view.rerender();
    expect(view.result.current.getExpiry()).toBeUndefined();
  });

  it('preserves a dated key and identifies an expired credential', () => {
    mockExpiry = '2000-01-01T00:00:00.000Z';
    const { result } = renderHook(() => useUserKey('OpenAI-Compatible'));
    expect(result.current.getExpiry()).toBe(mockExpiry);
    expect(result.current.checkExpiry()).toBe(false);
  });
});
