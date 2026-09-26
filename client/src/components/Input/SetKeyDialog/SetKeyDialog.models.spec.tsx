import React from 'react';
import { EModelEndpoint } from 'librechat-data-provider';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import SetKeyDialog from './SetKeyDialog';

const mockSaveUserKey = jest.fn();
const mockOnOpenChange = jest.fn();
const mockShowToast = jest.fn();
let mockUserProvideModels = true;

jest.mock('~/hooks', () => ({
  useLocalize: () => (key: string) => key,
  useClockFormat: () => true,
  useUserKey: () => ({ getExpiry: () => undefined, saveUserKey: mockSaveUserKey }),
}));

jest.mock('~/data-provider', () => ({
  useGetEndpointsQuery: () => ({
    data: { Gateway: { userProvideModels: mockUserProvideModels } },
  }),
}));

jest.mock('~/utils', () => ({ logger: { error: jest.fn() } }));
jest.mock('~/common', () => ({ NotificationSeverity: { SUCCESS: 'success', ERROR: 'error' } }));
jest.mock('./HelpText', () => () => null);
jest.mock('./GoogleConfig', () => () => null);
jest.mock('./BedrockConfig', () => () => null);
jest.mock('./OpenAIConfig', () => () => null);
jest.mock('./OtherConfig', () => () => null);

jest.mock('librechat-data-provider/react-query', () => ({
  useRevokeUserKeyMutation: () => ({ mutate: jest.fn(), isLoading: false }),
  useRevokeAllUserKeysMutation: () => ({ mutate: jest.fn(), isLoading: false }),
}));

jest.mock('./InputWithLabel', () => ({
  __esModule: true,
  default: jest
    .requireActual('react')
    .forwardRef(
      (
        { id, label, secret, value, onChange }: Record<string, any>,
        ref: React.Ref<HTMLInputElement>,
      ) => (
        <label htmlFor={id}>
          {label}
          <input
            id={id}
            type={secret ? 'password' : 'text'}
            value={value}
            onChange={onChange}
            ref={ref}
          />
        </label>
      ),
    ),
}));

jest.mock('@librechat/client', () => {
  const React = jest.requireActual('react');
  const Container = ({ children }: { children: React.ReactNode }) => <div>{children}</div>;
  return {
    Label: ({ children, ...props }: React.LabelHTMLAttributes<HTMLLabelElement>) => (
      <label {...props}>{children}</label>
    ),
    Textarea: React.forwardRef(
      (
        props: React.TextareaHTMLAttributes<HTMLTextAreaElement>,
        ref: React.Ref<HTMLTextAreaElement>,
      ) => <textarea {...props} ref={ref} />,
    ),
    Button: ({
      variant: _variant,
      ...props
    }: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant: string }) => (
      <button {...props} />
    ),
    Spinner: () => null,
    Dropdown: () => null,
    OGDialog: ({ open, children }: { open: boolean; children: React.ReactNode }) =>
      open ? <div>{children}</div> : null,
    OGDialogTitle: Container,
    OGDialogHeader: Container,
    OGDialogFooter: Container,
    OGDialogContent: Container,
    OGDialogTrigger: Container,
    useToastContext: () => ({ showToast: mockShowToast }),
  };
});

beforeEach(() => {
  mockUserProvideModels = true;
});

function renderDialog(endpointType = EModelEndpoint.custom) {
  render(
    <SetKeyDialog
      open
      onOpenChange={mockOnOpenChange}
      endpoint="Gateway"
      endpointType={endpointType}
      userProvideURL
    />,
  );
}

function fillCredentials() {
  fireEvent.change(screen.getByLabelText('Gateway API Key'), {
    target: { value: 'isolated-test-key' },
  });
  fireEvent.change(screen.getByLabelText('Gateway API URL'), {
    target: { value: 'https://gateway.example/v1' },
  });
}

it('saves normalized model IDs with the existing encrypted credential payload', async () => {
  renderDialog();
  fillCredentials();
  fireEvent.change(screen.getByLabelText('com_endpoint_custom_models'), {
    target: { value: ' model-a,model-b\nmodel-a ' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'com_ui_submit' }));
  await waitFor(() => expect(mockSaveUserKey).toHaveBeenCalledTimes(1));
  expect(JSON.parse(mockSaveUserKey.mock.calls[0][0])).toEqual({
    apiKey: 'isolated-test-key',
    baseURL: 'https://gateway.example/v1',
    models: ['model-a', 'model-b'],
  });
  expect(mockOnOpenChange).toHaveBeenCalledWith(false);
});

it('keeps the model list optional', async () => {
  renderDialog();
  fillCredentials();
  fireEvent.click(screen.getByRole('button', { name: 'com_ui_submit' }));
  await waitFor(() => expect(mockSaveUserKey).toHaveBeenCalledTimes(1));
  expect(JSON.parse(mockSaveUserKey.mock.calls[0][0]).models).toEqual([]);
});

it('blocks invalid model IDs with an accessible inline error', async () => {
  renderDialog();
  fillCredentials();
  const field = screen.getByLabelText('com_endpoint_custom_models');
  fireEvent.change(field, { target: { value: 'model with spaces' } });
  fireEvent.click(screen.getByRole('button', { name: 'com_ui_submit' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('com_endpoint_custom_models_invalid');
  expect(field).toHaveAttribute('aria-invalid', 'true');
  expect(mockSaveUserKey).not.toHaveBeenCalled();
  expect(mockOnOpenChange).not.toHaveBeenCalled();
});

it('preserves the native payload when custom models are disabled', async () => {
  mockUserProvideModels = false;
  renderDialog();
  fillCredentials();
  expect(screen.queryByLabelText('com_endpoint_custom_models')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'com_ui_submit' }));
  await waitFor(() => expect(mockSaveUserKey).toHaveBeenCalledTimes(1));
  expect(JSON.parse(mockSaveUserKey.mock.calls[0][0])).toEqual({
    apiKey: 'isolated-test-key',
    baseURL: 'https://gateway.example/v1',
  });
});

it('does not add custom model fields to a native provider', () => {
  renderDialog(EModelEndpoint.openAI);
  expect(screen.queryByLabelText('com_endpoint_custom_models')).not.toBeInTheDocument();
});
