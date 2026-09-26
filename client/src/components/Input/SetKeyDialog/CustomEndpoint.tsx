import { Label, Textarea } from '@librechat/client';
import { EModelEndpoint } from 'librechat-data-provider';
import { useFormContext, Controller } from 'react-hook-form';
import { parseCustomModelIds } from './models';
import InputWithLabel from './InputWithLabel';
import { useLocalize } from '~/hooks';

const CustomEndpoint = ({
  endpoint,
  userProvideURL,
  userProvideModels,
}: {
  endpoint: EModelEndpoint | string;
  userProvideURL?: boolean | null;
  userProvideModels?: boolean;
}) => {
  const { control } = useFormContext();
  const localize = useLocalize();
  return (
    <form className="flex-wrap">
      <Controller
        name="apiKey"
        control={control}
        render={({ field }) => (
          <InputWithLabel
            id="apiKey"
            {...field}
            label={`${endpoint} API Key`}
            labelClassName="mb-1"
            inputClassName="mb-2"
            secret
          />
        )}
      />
      {userProvideURL && (
        <Controller
          name="baseURL"
          control={control}
          render={({ field }) => (
            <InputWithLabel
              id="baseURL"
              {...field}
              label={`${endpoint} API URL`}
              labelClassName="mb-1"
            />
          )}
        />
      )}
      {userProvideModels && (
        <Controller
          name="models"
          control={control}
          rules={{
            validate: (value) =>
              parseCustomModelIds(value ?? '') !== null ||
              localize('com_endpoint_custom_models_invalid'),
          }}
          render={({ field, fieldState }) => (
            <div className="mt-4 flex flex-col gap-2">
              <Label htmlFor="custom-model-ids" className="text-sm font-medium">
                {localize('com_endpoint_custom_models')}
              </Label>
              <Textarea
                {...field}
                id="custom-model-ids"
                value={field.value ?? ''}
                rows={3}
                autoComplete="off"
                placeholder={localize('com_endpoint_custom_models_placeholder')}
                aria-invalid={fieldState.invalid}
                aria-describedby={fieldState.error ? 'custom-model-ids-error' : undefined}
              />
              {fieldState.error && (
                <p
                  id="custom-model-ids-error"
                  role="alert"
                  className="text-sm text-text-destructive"
                >
                  {fieldState.error.message}
                </p>
              )}
            </div>
          )}
        />
      )}
    </form>
  );
};

export default CustomEndpoint;
