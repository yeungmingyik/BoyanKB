import React from 'react';
import { render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/extend-expect';
import { PRODUCT_NAME, PRODUCT_VERSION } from '~/common/product';
import Footer from '../Footer';

jest.mock('react-gtm-module', () => ({
  __esModule: true,
  default: { initialize: jest.fn() },
}));

jest.mock('~/data-provider', () => ({
  useGetStartupConfig: jest.fn(() => ({ data: undefined, isFetching: false, error: null })),
}));

const mockTranslations: Record<string, string> = {
  com_ui_latest_footer: 'Every AI for Everyone.',
  com_ui_privacy_policy: 'Privacy policy',
  com_ui_terms_of_service: 'Terms of service',
};

jest.mock('~/hooks', () => ({
  useLocalize: () => (key: string) => mockTranslations[key] ?? key,
}));

describe('Footer', () => {
  test('renders the product version by default', () => {
    render(<Footer startupConfig={null} />);
    expect(screen.getByText(`${PRODUCT_NAME} ${PRODUCT_VERSION}`)).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
  });

  test('opens custom footer markdown links in a new tab', () => {
    render(<Footer startupConfig={{ customFooter: '[Docs](https://example.com/docs)' }} />);
    const link = screen.getByRole('link', { name: 'Docs' });
    expect(link).toHaveAttribute('href', 'https://example.com/docs');
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
  });

  test('keeps privacy policy and terms of service links in the same tab', () => {
    render(
      <Footer
        startupConfig={{
          interface: {
            privacyPolicy: { externalUrl: 'https://example.com/privacy' },
            termsOfService: { externalUrl: 'https://example.com/terms' },
          },
        }}
      />,
    );
    expect(screen.getByRole('link', { name: 'Privacy policy' })).not.toHaveAttribute('target');
    expect(screen.getByRole('link', { name: 'Terms of service' })).not.toHaveAttribute('target');
  });
});
