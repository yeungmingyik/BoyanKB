import { RecoilRoot } from 'recoil';
import { act, renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import useSidebarState from '../useSidebarState';

jest.mock('~/store', () => {
  const { atom } = jest.requireActual('recoil');
  return {
    __esModule: true,
    default: { sidebarExpanded: atom({ key: 'sidebar-breakpoint-expanded', default: true }) },
  };
});

let viewportWidth = 1280;
const listeners = new Set<() => void>();

function wrapper({ children }: { children: ReactNode }) {
  return <RecoilRoot>{children}</RecoilRoot>;
}

function resize(width: number) {
  viewportWidth = width;
  act(() => {
    for (const listener of [...listeners]) listener();
  });
}

describe('useSidebarState breakpoint', () => {
  beforeEach(() => {
    viewportWidth = 1280;
    listeners.clear();
    jest.spyOn(window, 'matchMedia').mockImplementation((query) => {
      const condition = /\((min|max)-width:\s*(\d+(?:\.\d+)?)px\)/.exec(query);
      return {
        media: query,
        get matches() {
          if (!condition) return false;
          return condition[1] === 'min'
            ? viewportWidth >= Number(condition[2])
            : viewportWidth <= Number(condition[2]);
        },
        onchange: null,
        addEventListener: (_event, listener) => listeners.add(listener as () => void),
        removeEventListener: (_event, listener) => listeners.delete(listener as () => void),
        addListener: jest.fn(),
        removeListener: jest.fn(),
        dispatchEvent: jest.fn(),
      } as MediaQueryList;
    });
  });

  it.each<[number, boolean]>([
    [767, true],
    [767.5, true],
    [768, false],
    [769, false],
  ])('uses the expected navigation layout at %s pixels', (width, mobile) => {
    viewportWidth = width;
    const { result } = renderHook(useSidebarState, { wrapper });
    expect(result.current.isSmallScreen).toBe(mobile);
  });

  it('preserves the desktop sidebar at 768 and closes it only when entering mobile', () => {
    viewportWidth = 769;
    const { result } = renderHook(useSidebarState, { wrapper });
    expect(result.current.expanded).toBe(true);

    resize(768);
    expect(result.current.isSmallScreen).toBe(false);
    expect(result.current.expanded).toBe(true);

    resize(767.5);
    expect(result.current.isSmallScreen).toBe(true);
    expect(result.current.expanded).toBe(false);

    act(() => result.current.setExpanded(true));
    expect(result.current.expanded).toBe(true);

    resize(768);
    expect(result.current.isSmallScreen).toBe(false);
    expect(result.current.expanded).toBe(true);

    resize(767);
    expect(result.current.isSmallScreen).toBe(true);
    expect(result.current.expanded).toBe(false);
  });
});
